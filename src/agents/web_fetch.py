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


def _host_is_safe(host: str) -> bool:
    """host 的所有解析 IP 都是公网才放行（任一私网/环回/保留 → 拒）。"""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001 —— 解析不了就拒
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return False
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
