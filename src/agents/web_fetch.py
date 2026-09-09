"""web_fetch：让 agent 读公网 URL（文档 / issue / 报错页）。stdlib urllib，无新依赖。

安全（只读但外向，必须设防）：
- scheme 限 http/https；
- **SSRF 防护**：解析 host → 任一解析 IP 命中私网/环回/链路本地/保留/多播 即拒（防读内网服务）；
- **逐跳校验重定向**（关掉自动跟随，手动跟、每跳都重新校验 host）；
- 下载封顶 + 超时；HTML → 正文。
出错一律返回以 '(' 开头的说明串（不抛），调用方原样回灌给模型。
"""

from __future__ import annotations

import html as _html
import ipaddress
import re
import socket
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

# 代理的 **fake-IP** 段——不是真实目的地，是给代理做路由的号码牌（见 _is_fake_ip）。
_FAKE_IP_NETS = (
    ipaddress.ip_network("198.18.0.0/15"),      # Clash / mihomo 默认 fake-ip-range
    ipaddress.ip_network("28.0.0.0/8"),         # sing-box 默认
)

_MAX_BYTES = 2_000_000          # 下载上限（防超大页面）
_MAX_TEXT = 6000                # 回灌给模型的正文上限
_TIMEOUT = 10                   # 单次请求超时（秒）
_MAX_REDIRECTS = 5


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """关掉 urllib 的自动重定向 —— 改为手动逐跳校验 host，堵住"重定向到内网"的 SSRF。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _urlopen(req, timeout):
    """实际发请求的接口（抽出来便于测试 monkeypatch，不触网）。"""
    opener = urllib.request.build_opener(_NoRedirect)
    return opener.open(req, timeout=timeout)


def _proxy_in_effect(host: str) -> bool:
    """该 host 的请求是否会经系统/环境代理出去（build_opener 默认含 ProxyHandler，两处口径一致）。

    走代理时 DNS 由代理**远端**解析——典型国内代理机上本地 getaddrinfo 对外网域名整个不可用。
    """
    try:
        proxies = urllib.request.getproxies()
        if not (proxies.get("https") or proxies.get("http")):
            return False
        return not urllib.request.proxy_bypass(host)
    except Exception:  # noqa: BLE001
        return False


def _is_fake_ip(addr) -> bool:
    """这个地址是不是代理的 **fake-IP 占位符**（而不是真实目的地）。

    Clash/mihomo 的 TUN/fake-ip 模式下，本地 DNS 对**所有**域名都返回 fake-ip-range 里的
    地址；它只是给代理做路由的号码牌，连接实际由代理远端解析。Python 认为 198.18.0.0/15
    （RFC 2544 基准测试段）`is_private=True`，于是下面的 SSRF 校验会把每一个外网域名都判成
    内网、一律拒绝——真机现象是 web_search/web_fetch 全废。

    2026-09-09 实测（同一进程、同一代理）：
        _host_is_safe('html.duckduckgo.com') = False   ← 闸门拒绝
        实际发请求                            = HTTP 202, 0.77s  ← 网络好得很
    也就是说，闸门是**按一个不会被使用的 IP 做判断，然后拒绝了自己**，报的还是"解析异常"。
    """
    return any(addr in net for net in _FAKE_IP_NETS)


def refusal_reason(host: str) -> str:
    """host 被拒的**真实原因**（一句人话）；没被拒返回空串。

    原来所有拒绝都统一说"域名解析异常"——而 fake-IP 那种情形解析明明好好的，这句话把人
    往"是不是网断了"上带（真机排查时确实被带偏过一次）。判定一个字不改，只让人看得懂。
    与 taint.py"来源分档、判定不分档"是同一手法。
    """
    if _host_is_safe(host):
        return ""
    if not host:
        return "缺少主机名"
    try:
        ipaddress.ip_address(host.strip("[]"))
        return f"{host} 是内网/保留地址的 IP 字面量"
    except ValueError:
        pass
    try:
        socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001
        return f"{host} 本地解析不了，且未配置代理（配了代理会放行，由代理远端解析）"
    return (f"{host} 解析到内网/保留地址；若你在用 Clash/mihomo 的 fake-ip 模式，"
            f"请确认代理环境变量（HTTP_PROXY/HTTPS_PROXY）对本进程可见")


def _host_is_safe(host: str) -> bool:
    """SSRF 校验：内网/保留地址一律拒，公网放行。

    - IP 字面量：直接按地址段判（有无代理都拦内网字面量）；
    - 域名且本地能解析：任一解析 IP 命中私网/环回/保留 → 拒（内网名照拦）；
    - 域名且本地解析失败：**配了代理则放行**（DNS 由代理远端解析，本地失败是代理环境常态，
      真机 dogfood 抓的：代理机上本地 DNS 全挂，这里一票否决把 web_search/web_fetch 全拦死）；
      无代理维持拒绝（反正连接也会失败，且防解析异常当后门）。
    - 域名且**解析结果全是 fake-IP**：这次解析没给出任何真实目的地，语义上等同于"解析失败"，
      走同一条代理放行分支。这与上一条是同一个坑的两半——那次修的是"解析不出"，
      这次是"解析出了个假的"，而 fake-IP 模式比本地 DNS 全挂常见得多。

    四道防线一条没动：IP 字面量照拦、无代理照拦、解析出真实内网 IP 照拦、逐跳重定向照校验。
    """
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host.strip("[]"))     # IP 字面量：不查 DNS 直接判段
    except ValueError:
        addr = None
    if addr is not None:
        return not (addr.is_private or addr.is_loopback or addr.is_link_local
                    or addr.is_reserved or addr.is_multicast or addr.is_unspecified)
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001
        return _proxy_in_effect(host)                     # 解析不了：代理环境放行，否则拒
    resolved = []
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            if not _is_fake_ip(addr):
                return False                              # 真实内网地址：拦（原逻辑不变）
        resolved.append(addr)
    # **all 而不是 any**：只要有一个真实地址就用真实地址判（上面的循环已经判过了）。
    # 只有全部都是占位符、这次解析等于什么真实信息都没给出时，才认定它无效。
    # any 会让一个混进来的 fake-IP 把真实内网地址的判定绕过去——那才是开后门。
    if resolved and all(_is_fake_ip(a) for a in resolved):
        return _proxy_in_effect(host)
    return True


def _strip_html(raw: str) -> str:
    """粗暴但够用的 HTML→正文：去 script/style/注释/标签、反转义、压空白。"""
    raw = re.sub(r"(?is)<(script|style|noscript|head)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = _html.unescape(raw)
    text = re.sub(r"[ \t\r\f]+", " ", text)
    text = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", text)
    return text.strip()


def fetch_url(url: str) -> str:
    """抓 URL 返回正文文本（带 SSRF 防护/封顶/超时/逐跳重定向校验）。失败返回 '(' 开头说明串。"""
    cur = (url or "").strip()
    if not cur:
        return "(web_fetch 需要 url)"
    for _hop in range(_MAX_REDIRECTS + 1):
        p = urlparse(cur)
        if p.scheme not in ("http", "https"):
            return f"(只支持 http/https，拒绝: {p.scheme or '无 scheme'})"
        if not _host_is_safe(p.hostname or ""):
            return f"(拒绝抓取非公网地址，疑似 SSRF: {p.hostname})"
        req = urllib.request.Request(cur, headers={"User-Agent": "VortoCode-web_fetch/1.0"})
        try:
            resp = _urlopen(req, _TIMEOUT)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):     # 手动跟重定向，下一轮重新校验 host
                loc = e.headers.get("Location") if getattr(e, "headers", None) else None
                if not loc:
                    return f"(重定向 {e.code} 但无 Location)"
                cur = urljoin(cur, loc)
                continue
            return f"(HTTP {e.code} {getattr(e, 'reason', '')})"
        except Exception as e:  # noqa: BLE001
            return f"(抓取失败: {e})"
        ctype = ""
        try:
            ctype = (resp.headers.get("Content-Type") or "").lower()
        except Exception:  # noqa: BLE001
            pass
        raw = resp.read(_MAX_BYTES)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        text = _strip_html(raw) if ("html" in ctype or raw.lstrip()[:1] == "<") else raw.strip()
        clipped = text[:_MAX_TEXT]
        tail = "\n…(正文已截断)" if len(text) > _MAX_TEXT else ""
        return f"# {cur}（{len(text)} 字符）\n{clipped}{tail}"
    return f"(重定向次数超过 {_MAX_REDIRECTS}: {cur})"
