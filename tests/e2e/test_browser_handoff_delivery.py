"""Synthetic local delivery: original loop -> login gate -> artifact -> reply.

Browser and model are scripted. The artifact is really written in tmp_path;
this is not real website, Chromium, provider, or installed Desktop acceptance.
"""
import json

from src.agents.confirmation_context import website_confirmation
from src.agents.gate import make_confirm_gate, request
from src.agents.main_agent import MainAgent
from src.agents.tools.artifacts import ArtifactStore, build_artifact_tools
from src.agents.tools.browser import build_browser_tools
from src.agents.tools.website_browser import shutdown_browsers
from src.browser.connections import BrowserConnections
from tests.unit.test_browser_handoff import Window


async def test_handoff_returns_to_same_task_and_delivers_reviewable_local_artifact(tmp_path, monkeypatch):
    from src.agents import web_fetch
    monkeypatch.setattr(web_fetch, "refusal_reason", lambda _host: "")
    root = tmp_path / "project"
    root.mkdir()
    registry = BrowserConnections(str(root), state_root=tmp_path / "host")
    row = registry.approve("https://example.com", "工作", "desktop")
    browser, approvals, published, replies = Window(), [], [], []

    async def ask(_text):
        presentation = website_confirmation()
        approvals.append(presentation["action"] if presentation else "artifact")
        if presentation and presentation["action"] == "login":
            assert not browser.reads
            browser.url = "https://example.com/report"
        return True

    gate = make_confirm_gate(ask, can_ask_human=True)

    async def approve_artifact(_preview, _update):
        return await request(gate, "保存本次网站整理结果为可审阅的本机制品？", "write")

    tools = build_browser_tools(str(root), gate, browser_factory=lambda: browser, connections=registry)
    tools += build_artifact_tools(str(root), confirm=approve_artifact,
                                  on_published=lambda meta, url: published.append((meta, url)))

    class Model:
        calls = 0

        async def chat(self, messages, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"content": json.dumps({"tool": "browser_open", "args": {
                    "url": "https://example.com", "connection_id": row["id"]}})}
            if self.calls == 2:
                source = next(m["content"] for m in reversed(messages)
                              if m["role"] == "user" and "[工具 browser_open 结果]" in str(m.get("content")))
                assert "正文" in source and "主人已确认恢复" in source
                return {"content": json.dumps({"tool": "publish_artifact", "args": {
                    "title": "Website report", "html": "<html><body><h1>网站整理结果</h1><p>正文</p></body></html>"}})}
            return {"content": "已生成本地报告，请打开制品核对；未代替你验收。"}

    model = Model()
    agent = MainAgent(tools, llm=model, native=False)
    try:
        await agent.run_turn("打开已登记的工作网站，完成登录核对后整理为本地报告。", mode="build", emit=replies.append)
        assert approvals == ["open", "login", "artifact"]
        assert model.calls == 3  # No extra browser_login model request.
        assert len(published) == 1 and len(replies) == 1
        item = published[0][0]
        assert ArtifactStore(str(root)).list()[0]["id"] == item["id"]
        assert "正文" in ArtifactStore(str(root)).html(item["id"])
        assert "请打开制品核对" in replies[0]
    finally:
        await shutdown_browsers()
