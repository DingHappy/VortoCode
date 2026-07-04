"""web_fetch（src/agents/web_fetch.py）—— 联网读 URL，带 SSRF 防护。全程不触网：
用 IP 字面量（getaddrinfo 不走 DNS）+ monkeypatch _urlopen 造响应。"""

import urllib.error

import pytest

from src.agents import web_fetch as wf


def test_host_is_safe_rejects_private_and_loopback():
    assert wf._host_is_safe("8.8.8.8") and wf._host_is_safe("1.1.1.1")        # 公网 IP 字面量
    for bad in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.0.1", "::1", "0.0.0.0"):
        assert not wf._host_is_safe(bad), bad
    assert not wf._host_is_safe("localhost")                                  # 解析到 127.0.0.1
    assert not wf._host_is_safe("")


def test_host_is_safe_proxy_aware(monkeypatch):
    """代理环境（真机 dogfood 抓的）：本地 DNS 对外网整个不可用，解析失败不再一票否决。"""
    def _no_dns(host, port):
        raise OSError("resolution unavailable")
    monkeypatch.setattr(wf.socket, "getaddrinfo", _no_dns)

    monkeypatch.setattr(wf.urllib.request, "getproxies", lambda: {})
    assert not wf._host_is_safe("example.com")             # 无代理：解析不了 → 拒（旧行为）

    monkeypatch.setattr(wf.urllib.request, "getproxies",
                        lambda: {"https": "http://127.0.0.1:7897"})
    monkeypatch.setattr(wf.urllib.request, "proxy_bypass", lambda host: False)
    assert wf._host_is_safe("example.com")                 # 有代理：DNS 由代理远端解析 → 放行

    monkeypatch.setattr(wf.urllib.request, "proxy_bypass", lambda host: True)
    assert not wf._host_is_safe("internal.corp")           # 命中代理绕过名单：不吃代理豁免

    monkeypatch.setattr(wf.urllib.request, "proxy_bypass", lambda host: False)
    assert not wf._host_is_safe("10.0.0.8")                # 内网 IP 字面量：有代理也照拦


def test_strip_html_to_text():
    html = "<html><head><style>x{}</style></head><body>Hello <b>World</b>&amp;Co<script>bad()</script></body></html>"
    out = wf._strip_html(html)
    assert "Hello World" in out and "&Co" in out
    assert "bad()" not in out and "x{}" not in out                           # script/style 去掉


def test_fetch_rejects_non_http_scheme():
    assert "只支持 http/https" in wf.fetch_url("ftp://example.com/x")
    assert "只支持 http/https" in wf.fetch_url("file:///etc/passwd")


def test_fetch_rejects_ssrf_targets():
    assert "SSRF" in wf.fetch_url("http://127.0.0.1/admin")
    assert "SSRF" in wf.fetch_url("http://10.0.0.5/internal")
    assert "SSRF" in wf.fetch_url("http://localhost:8080/")


def test_fetch_empty_url():
    assert "需要 url" in wf.fetch_url("")


class _FakeResp:
    def __init__(self, body: bytes, ctype="text/html"):
        self.headers = {"Content-Type": ctype}
        self._b = body

    def read(self, n=-1):
        return self._b


def test_fetch_success_html_to_text(monkeypatch):
    monkeypatch.setattr(wf, "_urlopen",
                        lambda req, timeout: _FakeResp(b"<html><body>Doc <b>Body</b></body></html>"))
    out = wf.fetch_url("http://8.8.8.8/doc")                                  # 公网 IP，过 SSRF
    assert "Doc Body" in out and out.startswith("# http://8.8.8.8/doc")


def test_fetch_plaintext_passthrough(monkeypatch):
    monkeypatch.setattr(wf, "_urlopen",
                        lambda req, timeout: _FakeResp(b"plain readme text", ctype="text/plain"))
    out = wf.fetch_url("http://1.1.1.1/readme.txt")
    assert "plain readme text" in out


def test_fetch_follows_redirect_to_public(monkeypatch):
    calls = {"n": 0}

    def fake(req, timeout):
        calls["n"] += 1
        if calls["n"] == 1:                                                  # 第一跳 302 → 公网目标
            raise urllib.error.HTTPError(req.full_url, 302, "Found",
                                         {"Location": "http://1.1.1.1/final"}, None)
        return _FakeResp(b"<html><body>Final Page</body></html>")
    monkeypatch.setattr(wf, "_urlopen", fake)
    out = wf.fetch_url("http://8.8.8.8/start")
    assert "Final Page" in out and "1.1.1.1/final" in out                    # 跟到最终页
    assert calls["n"] == 2


def test_fetch_blocks_redirect_to_private(monkeypatch):
    def fake(req, timeout):                                                   # 重定向到内网 → 下一跳被 SSRF 拦
        raise urllib.error.HTTPError(req.full_url, 302, "Found",
                                     {"Location": "http://127.0.0.1/evil"}, None)
    monkeypatch.setattr(wf, "_urlopen", fake)
    out = wf.fetch_url("http://8.8.8.8/redirector")
    assert "SSRF" in out and "127.0.0.1" in out                              # 逐跳校验挡住


def test_fetch_http_error_reported(monkeypatch):
    def fake(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(wf, "_urlopen", fake)
    assert "HTTP 404" in wf.fetch_url("http://8.8.8.8/missing")


@pytest.mark.asyncio
async def test_build_web_tools_exposes_web_fetch():
    from src.agents.main_agent import build_web_tools
    tools = {t.name: t for t in build_web_tools()}
    assert "web_fetch" in tools and tools["web_fetch"].read_only is True
    # 工具 handler 走到 fetch_url：SSRF 目标被拒（端到端、不触网）
    out = await tools["web_fetch"].handler({"url": "http://127.0.0.1/x"})
    assert "SSRF" in out
