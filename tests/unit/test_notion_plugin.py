"""Notion 插件：令牌只进请求头、不跟重定向、搜索/读页/读库的渲染、错误说人话且不带令牌。"""
import io
import json
import os
import urllib.error

import pytest

import src.plugins as plugins
from src.agents import web_fetch
from src.plugins import notion

READY = plugins.plugin_ready
TOKEN = "ntn_secret_value_123"
PID = "1a2b3c4d5e6f40718293a4b5c6d7e8f9"
DASHED = "1a2b3c4d-5e6f-4071-8293-a4b5c6d7e8f9"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(plugins, "plugin_ready", READY)
    folder = tmp_path / ".vortocode" / "connectors"
    folder.mkdir(parents=True)
    path = folder / "notion.token"
    path.write_text(TOKEN + "\n")
    os.chmod(path, 0o600)
    return tmp_path


def rich(text):
    return [{"plain_text": text}]


class FakeNotion:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url, *, headers=None, data=None, **_kw):
        assert headers["Authorization"] == f"Bearer {TOKEN}" and headers["Notion-Version"]
        path = url.removeprefix("https://api.notion.com/v1/")
        self.calls.append((path, json.loads(data) if data else None))
        reply = self.routes.get(path.split("?")[0] + ("?cursor" if "start_cursor" in path else ""))
        if isinstance(reply, int):
            raise web_fetch.FetchError(f"对方返回 HTTP {reply}", reply)
        return json.dumps(reply).encode()


def tool(name):
    return {t.name: t for t in notion.build_tools()}[name]


async def test_search_lists_pages_and_databases_without_the_token(home, monkeypatch):
    fake = FakeNotion({"search": {"results": [
        {"object": "page", "id": DASHED, "last_edited_time": "2026-10-20T08:00:00Z",
         "properties": {"Name": {"type": "title", "title": rich("<旅行清单>")}}},
        {"object": "database", "id": "db", "title": rich("读书"), "last_edited_time": "2026-10-01T00:00:00Z"},
        {"object": "user"}]}})
    monkeypatch.setattr(web_fetch, "fetch_bytes", fake)
    out = await tool("notion_search").handler({"query": "旅行", "limit": 99})
    assert fake.calls[0][1]["query"] == "旅行" and fake.calls[0][1]["page_size"] == 20
    assert out.startswith("<vortocode_untrusted_notion>") and "旅行清单" in out and "读书" in out
    assert "<旅行清单>" not in out and TOKEN not in out
    assert all(t.read_only and t.untrusted_source and t.external_content for t in notion.build_tools())


async def test_read_page_renders_properties_blocks_nested_and_paged(home, monkeypatch):
    fake = FakeNotion({
        f"pages/{DASHED}": {"object": "page", "properties": {
            "Name": {"type": "title", "title": rich("周计划")},
            "状态": {"type": "status", "status": {"name": "进行中"}},
            "截止": {"type": "date", "date": {"start": "2026-10-25"}}}},
        f"blocks/{DASHED}/children": {"results": [
            {"type": "heading_2", "heading_2": {"rich_text": rich("本周")}},
            {"type": "to_do", "to_do": {"rich_text": rich("订机票"), "checked": True}},
            {"id": "tog", "type": "toggle", "has_children": True, "toggle": {"rich_text": rich("细节")}},
            {"id": "kid", "type": "child_page", "child_page": {"title": "附录"}}],
            "has_more": True, "next_cursor": "c1"},
        f"blocks/{DASHED}/children?cursor": {"results": [
            {"type": "paragraph", "paragraph": {"rich_text": rich("第二页")}}], "has_more": False},
        "blocks/tog/children": {"results": [{"type": "bulleted_list_item",
                                             "bulleted_list_item": {"rich_text": rich("靠窗")}}]},
    })
    monkeypatch.setattr(web_fetch, "fetch_bytes", fake)
    out = json.loads((await tool("notion_read").handler(
        {"page_id": f"https://www.notion.so/me/x-{PID}?pvs=4"})).split("\n", 2)[2].rsplit("\n", 1)[0])["content"]
    assert out.splitlines() == ["# 周计划", "状态：进行中", "截止：2026-10-25", "## 本周", "[x] 订机票",
                                "▸ 细节", "    - 靠窗", "[子页面] 附录（id kid）", "第二页"]


async def test_database_falls_back_and_errors_say_what_to_do(home, monkeypatch):
    fake = FakeNotion({f"pages/{DASHED}": 404,
                       f"databases/{DASHED}": {"object": "database", "title": rich("读书")},
                       f"databases/{DASHED}/query": {"results": [
                           {"id": "r1", "properties": {"书名": {"type": "title", "title": rich("三体")},
                                                       "读完": {"type": "checkbox", "checkbox": True}}}]}})
    monkeypatch.setattr(web_fetch, "fetch_bytes", fake)
    out = await tool("notion_read").handler({"page_id": PID})
    assert "读书（数据库，最近 1 行）" in out and "三体（读完=是） id r1" in out
    monkeypatch.setattr(web_fetch, "fetch_bytes", FakeNotion({"search": 401}))
    out = await tool("notion_search").handler({})
    assert out == "Notion 读取失败：令牌无效或已被收回"
    assert "page_id" in await tool("notion_read").handler({"page_id": "nope"})


async def test_missing_token_and_credentialed_requests_never_follow_redirects(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert "还没接 Notion" in await tool("notion_search").handler({})

    def redirect(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 307, "moved", {"Location": "https://elsewhere.example/"}, io.BytesIO())

    monkeypatch.setattr(web_fetch, "_urlopen", redirect)
    monkeypatch.setattr(web_fetch, "_host_is_safe", lambda host: True)
    with pytest.raises(web_fetch.FetchError, match="不跟随"):
        web_fetch.fetch_bytes("https://api.notion.com/v1/search", headers={"Authorization": "Bearer x"}, data=b"{}")
