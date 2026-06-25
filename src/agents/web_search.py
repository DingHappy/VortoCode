"""web_search：让 agent 把一个查询变成一批结果（标题/URL/摘要），再用 web_fetch 深读。

中转站没有搜索后端、且不接外部搜索 API key，所以走 **DuckDuckGo 的 HTML 端点**
（`html.duckduckgo.com/html/?q=`，无需 key）。这是 best-effort：DDG 改版/限流就可能解析不到，
一律优雅降级成 '(' 开头的说明串（不抛），调用方原样回灌给模型。

复用 web_fetch 的安全基建：同一个 `_urlopen` 发请求接口（便于测试 monkeypatch、不触网）+
`_host_is_safe` 的 SSRF 校验。结果里的真实 URL 藏在 DDG 的跳转链接 `?uddg=<urlencoded>` 里，解出来。
"""

from __future__ import annotations

import html as _html
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


def _fetch_search_html(query: str) -> tuple[str | None, str | None]:
    """请求 DDG html 端点，返回 (raw_html, None) 或 (None, 错误说明串)。"""
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    host = urllib.parse.urlparse(url).hostname or ""
    if not web_fetch._host_is_safe(host):          # 复用 SSRF 校验（理论上 DDG 恒公网，稳妥兜底）
        return None, f"(拒绝：搜索域名解析异常 {host})"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        resp = web_fetch._urlopen(req, _TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return None, f"(搜索失败：{e})"
    raw = resp.read(_MAX_BYTES)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return raw, None


def web_search(query: str, *, max_results: int = _DEFAULT_MAX) -> str:
    """搜索 query，返回带编号的「标题 / URL / 摘要」列表文本。失败/无结果返回 '(' 开头说明串。"""
    q = (query or "").strip()
    if not q:
        return "(web_search 需要 query)"
    raw, err = _fetch_search_html(q)
    if err:
        return err
    results = _parse_ddg_html(raw, max(1, int(max_results or _DEFAULT_MAX)))
    if not results:
        return f"(没搜到结果，或搜索页结构有变 / 被限流：{q})"
    lines = [f"# 搜索：{q}（{len(results)} 条，可用 web_fetch 深读其中链接）"]
    for i, r in enumerate(results, 1):
        block = f"{i}. {r['title']}\n   {r['url']}"
        if r["snippet"]:
            block += f"\n   {r['snippet']}"
        lines.append(block)
    return "\n".join(lines)
