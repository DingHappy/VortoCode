"""web_search：让 agent 把一个查询变成一批结果（标题/URL/摘要），再用 web_fetch 深读。

中转站没有搜索后端、且不接外部搜索 API key，所以爬公开端点，**双后端 + 自动回退**：

- **DuckDuckGo HTML**（`html.duckduckgo.com/html/?q=`）——默认首选；
- **Bing RSS**（`bing.com/search?format=rss`）——DDG 不可达时兜底。真机 dogfood 抓的：
  国内网络 DDG 域名被 DNS 污染（解析到保留段/他人 IP），SSRF 校验直接拒，搜索全挂；
  Bing 国内可达，RSS 输出格式稳定、无验证码（HTML 端点会回 JS challenge 空壳，别用）。

DDG 网络层挂过一次（污染/超时）即**进程内粘性降级**为 Bing 优先（同 native-tools 回退哲学）；
`VORTOCODE_SEARCH_BACKEND=ddg|bing` 可显式钉死单后端。一切失败都优雅降级成 '(' 开头的
说明串（不抛），调用方原样回灌给模型。

复用 web_fetch 的安全基建：同一个 `_urlopen` 发请求接口（便于测试 monkeypatch、不触网）+
`_host_is_safe` 的 SSRF 校验。DDG 结果的真实 URL 藏在跳转链接 `?uddg=<urlencoded>` 里，解出来。
"""

from __future__ import annotations

import html as _html
import os
import re
import urllib.parse
import urllib.request

from src.agents import web_fetch

_TIMEOUT = 10
_MAX_BYTES = 2_000_000
_DEFAULT_MAX = 8
# DDG 对默认 urllib UA 可能不友好；用浏览器式 UA（实测可拿到结果页）。
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# DDG 网络层失败一次（DNS 污染被拒/超时）就粘性降级：本进程后续搜索 Bing 优先，
# 不再每次白等 DDG 超时。测试可直接重置本标志。
_ddg_down = False

_TITLE_RE = re.compile(r'<a\b[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)


def _clean(fragment: str) -> str:
    """HTML 片段 → 纯文本：去标签、反转义、压空白。"""
    text = re.sub(r"<[^>]+>", "", fragment)
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()


def _decode_ddg_href(href: str) -> str:
    """DDG 结果链接是跳转包装 `//duckduckgo.com/l/?uddg=<urlencoded 真实URL>&rut=...`，解出真实 URL。"""
    href = _html.unescape(href)
    m = re.search(r"[?&]uddg=([^&]+)", href)
    if m:
        return urllib.parse.unquote(m.group(1))
    if href.startswith("//"):            # 偶有协议相对的直链
        return "https:" + href
    return href if href.startswith("http") else ""


def _parse_ddg_html(raw: str, limit: int) -> list[dict]:
    """从 DDG html 结果页抽 [{title,url,snippet}]，按出现序、摘要按位置就近配对。"""
    titles = list(_TITLE_RE.finditer(raw))
    snippets = [(m.start(), m.group(1)) for m in _SNIPPET_RE.finditer(raw)]
    out: list[dict] = []
    for i, tm in enumerate(titles):
        if len(out) >= limit:
            break
        url = _decode_ddg_href(tm.group(1))
        title = _clean(tm.group(2))
        if not url or not title:
            continue
        next_pos = titles[i + 1].start() if i + 1 < len(titles) else len(raw)
        snippet = ""
        for sp, stext in snippets:                 # 该标题之后、下一标题之前的首个摘要
            if tm.start() < sp < next_pos:
                snippet = _clean(stext)
                break
        out.append({"title": title, "url": url, "snippet": snippet})
    return out


def _fetch_html(url: str) -> tuple[str | None, str | None]:
    """请求一个搜索端点，返回 (raw_html, None) 或 (None, 错误说明串)。"""
    host = urllib.parse.urlparse(url).hostname or ""
    if not web_fetch._host_is_safe(host):          # 复用 SSRF 校验（DNS 污染到保留段在此被拒）
        return None, f"拒绝：搜索域名解析异常 {host}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        resp = web_fetch._urlopen(req, _TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return None, f"请求失败：{e}"
    raw = resp.read(_MAX_BYTES)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return raw, None


def _search_ddg(query: str, limit: int) -> tuple[list[dict] | None, str | None]:
    """DDG html 端点 → (results, None) / (None, 错误串)。解析为空按无结果（results=[]）返回。"""
    raw, err = _fetch_html("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query))
    if err:
        return None, err
    return _parse_ddg_html(raw, limit), None


_RSS_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.S)
_RSS_FIELD_RES = {k: re.compile(rf"<{k}>(.*?)</{k}>", re.S) for k in ("title", "link", "description")}


def _parse_bing_rss(raw: str, limit: int) -> list[dict]:
    """Bing RSS（format=rss）→ [{title,url,snippet}]。RSS 是稳定 XML，直链无跳转包装。"""
    out: list[dict] = []
    for item in _RSS_ITEM_RE.finditer(raw):
        if len(out) >= limit:
            break
        fields = {}
        for k, rx in _RSS_FIELD_RES.items():
            m = rx.search(item.group(1))
            fields[k] = _clean(m.group(1)) if m else ""
        if fields["link"].startswith("http") and fields["title"]:
            out.append({"title": fields["title"], "url": fields["link"],
                        "snippet": fields["description"]})
    return out


def _search_bing(query: str, limit: int) -> tuple[list[dict] | None, str | None]:
    """Bing RSS 端点（国内可达；HTML 端点对无 cookie 客户端回 JS challenge 空壳，不用）。"""
    raw, err = _fetch_html("https://www.bing.com/search?q=" + urllib.parse.quote(query)
                           + f"&format=rss&count={limit}")
    if err:
        return None, err
    return _parse_bing_rss(raw, limit), None


_BACKENDS = {"ddg": _search_ddg, "bing": _search_bing}


def _backend_order() -> list[str]:
    """后端尝试顺序：env 钉死单后端 > DDG 挂过则 Bing 优先 > 默认 DDG 优先。"""
    pref = (os.getenv("VORTOCODE_SEARCH_BACKEND") or "").strip().lower()
    if pref in ("ddg", "duckduckgo"):
        return ["ddg"]
    if pref == "bing":
        return ["bing"]
    return ["bing", "ddg"] if _ddg_down else ["ddg", "bing"]


def web_search(query: str, *, max_results: int = _DEFAULT_MAX) -> str:
    """搜索 query，返回带编号的「标题 / URL / 摘要」列表文本。失败/无结果返回 '(' 开头说明串。

    依 _backend_order 逐后端尝试，拿到结果即返回；DDG 网络层失败会粘性降级（见模块头）。
    """
    global _ddg_down
    q = (query or "").strip()
    if not q:
        return "(web_search 需要 query)"
    limit = max(1, int(max_results or _DEFAULT_MAX))
    errors: list[str] = []
    got_page = False                                  # 至少有一个后端返回了页面（只是没解析出结果）
    for name in _backend_order():
        results, err = _BACKENDS[name](q, limit)
        if err:
            errors.append(f"{name}: {err}")
            if name == "ddg":
                _ddg_down = True                      # 网络层挂了 → 本进程后续 Bing 优先
            continue
        got_page = True
        if not results:
            continue
        lines = [f"# 搜索：{q}（{len(results)} 条，可用 web_fetch 深读其中链接）"]
        for i, r in enumerate(results, 1):
            block = f"{i}. {r['title']}\n   {r['url']}"
            if r["snippet"]:
                block += f"\n   {r['snippet']}"
            lines.append(block)
        return "\n".join(lines)
    if got_page:
        return f"(没搜到结果，或搜索页结构有变 / 被限流：{q})"
    return "(搜索失败：" + "；".join(errors) + ")"
