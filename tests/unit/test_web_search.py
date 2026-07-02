"""web_search（DuckDuckGo HTML 爬取）—— src/agents/web_search.py。

不触网：monkeypatch web_fetch._urlopen 喂入仿真 HTML（结构照搬 DDG 真实结果页）。
解析器/URL 解码/降级路径都确定性测。
"""
import pytest

from src.agents import web_fetch
from src.agents import web_search as ws

# 仿 DDG html 结果页：result__a 含跳转链接(?uddg=真实URL)，result__snippet 含摘要(带 <b> 高亮)。
SAMPLE = """
<div class="result results_links">
  <h2 class="result__title">
    <a class="result__a" rel="nofollow"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fasyncio&amp;rut=abc">Asyncio <b>Gather</b> Guide</a>
  </h2>
  <a class="result__snippet"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fasyncio&amp;rut=abc">Learn <b>asyncio.gather</b> with examples.</a>
</div>
<div class="result results_links">
  <h2 class="result__title">
    <a class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fasyncio.html&amp;rut=def">Python Docs</a>
  </h2>
  <a class="result__snippet" href="x">Official asyncio documentation.</a>
</div>
"""


class _FakeResp:
    def __init__(self, body: bytes):
        self._b = body

    def read(self, n: int = -1):
        return self._b


def _patch_html(monkeypatch, html: str):
    monkeypatch.setattr(web_fetch, "_urlopen", lambda req, timeout: _FakeResp(html.encode("utf-8")))
    # 同时跳过 _host_is_safe 的真实 DNS/SSRF 解析——本组测试针对解析/格式化，不测 SSRF；
    # 否则无网络（如 CI runner / 沙箱）时 getaddrinfo 失败会让这些"离线"测试假性红。
    monkeypatch.setattr(web_fetch, "_host_is_safe", lambda host: True)


def test_decode_ddg_href_unwraps_uddg():
    got = ws._decode_ddg_href("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=x")
    assert got == "https://example.com/a"                  # uddg 解码出真实 URL


def test_decode_ddg_href_direct_and_protocol_relative():
    assert ws._decode_ddg_href("https://x.com/p") == "https://x.com/p"
    assert ws._decode_ddg_href("//cdn.x.com/p") == "https://cdn.x.com/p"
    assert ws._decode_ddg_href("/relative/only") == ""     # 无法解析 → 空（被过滤）


def test_parse_extracts_title_url_snippet():
    res = ws._parse_ddg_html(SAMPLE, limit=10)
    assert len(res) == 2
    assert res[0] == {"title": "Asyncio Gather Guide",     # <b> 标签被清掉
                      "url": "https://example.com/asyncio",
                      "snippet": "Learn asyncio.gather with examples."}
    assert res[1]["url"] == "https://docs.python.org/3/library/asyncio.html"
    assert res[1]["snippet"] == "Official asyncio documentation."


def test_parse_respects_limit():
    assert len(ws._parse_ddg_html(SAMPLE, limit=1)) == 1


def test_web_search_formats_results(monkeypatch):
    _patch_html(monkeypatch, SAMPLE)
    out = ws.web_search("asyncio gather", max_results=5)
    assert "# 搜索：asyncio gather（2 条" in out
    assert "1. Asyncio Gather Guide" in out
    assert "https://example.com/asyncio" in out
    assert "Official asyncio documentation." in out


def test_web_search_empty_query_no_fetch(monkeypatch):
    # 空 query 直接拒，不该发请求
    monkeypatch.setattr(web_fetch, "_urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该联网")))
    assert ws.web_search("   ") == "(web_search 需要 query)"


def test_web_search_no_results_graceful(monkeypatch):
    _patch_html(monkeypatch, "<html><body>nothing here</body></html>")
    out = ws.web_search("zxcvqwer")
    assert out.startswith("(没搜到结果")                    # 解析不到 → 优雅降级、不抛


def test_web_search_fetch_error_graceful(monkeypatch):
    def _boom(req, timeout):
        raise OSError("connection reset")
    monkeypatch.setattr(web_fetch, "_host_is_safe", lambda host: True)   # 跳过真实 DNS，测的是 _urlopen 抛错路径
    monkeypatch.setattr(web_fetch, "_urlopen", _boom)
    out = ws.web_search("anything")
    assert out.startswith("(搜索失败")                      # 网络炸了也只返回说明串、不抛


@pytest.mark.asyncio
async def test_web_search_tool_wired_into_build_web_tools(monkeypatch):
    from src.agents.main_agent import build_web_tools
    _patch_html(monkeypatch, SAMPLE)
    tools = {t.name: t for t in build_web_tools()}
    assert "web_search" in tools and tools["web_search"].read_only is True
    out = await tools["web_search"].handler({"query": "asyncio"})
    assert "Asyncio Gather Guide" in out
