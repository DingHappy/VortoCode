"""MainAgent must surface unknown website results and fence model retries.

Scripted model and browser only; no real login, site, provider or installed App.
"""
import json

from src.agents.gate import make_confirm_gate
from src.agents.main_agent import MainAgent
from src.agents.tools.browser import build_browser_tools
from src.agents.tools.website_browser import shutdown_browsers
from src.browser.connections import BrowserConnections
from tests.unit.test_browser_uncertain_actions import Browser


async def test_unknown_submission_is_failed_and_repeated_model_request_cannot_dispatch(tmp_path, monkeypatch):
    from src.agents import web_fetch
    monkeypatch.setattr(web_fetch, "refusal_reason", lambda _host: "")
    root = tmp_path / "project"
    root.mkdir()
    registry = BrowserConnections(str(root), state_root=tmp_path / "host")
    row = registry.approve("https://example.com", "synthetic-account", "desktop")
    browser, approvals, events, replies = Browser(), [], [], []

    async def ask(text):
        approvals.append(text)
        return True

    gate = make_confirm_gate(ask, can_ask_human=True)
    tools = build_browser_tools(str(root), gate, browser_factory=lambda: browser, connections=registry)
    entries = {tool.name: tool for tool in tools}

    class Model:
        calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 2:
                assert "无法核实" in str(messages)
            if self.calls <= 2:
                return {"content": json.dumps({"tool": "browser_click", "args": {"ref": 1}})}
            assert "结果不明" in str(messages)
            return {"content": "操作结果不明，已暂停提交，请先人工核对；尚未确认签到成功。"}

    try:
        await entries["browser_open"].handler({"url": "https://example.com", "connection_id": row["id"]})
        await entries["browser_login"].handler({})
        baseline = len(approvals)
        browser.fault = "timeout"
        model = Model()
        agent = MainAgent(tools, llm=model, native=False,
                          on_tool_event=lambda stage, payload: events.append((stage, payload)))
        await agent.run_turn("请执行一次网站签到。", mode="build", emit=replies.append)
        assert model.calls == 3 and len(browser.clicks) == 1
        assert len(approvals) == baseline + 1
        assert [item["status"] for stage, item in events if stage == "finish"] == ["failed", "failed"]
        assert len(replies) == 1 and "尚未确认签到成功" in replies[0]
        assert entries["browser_click"].resource.observe()["state"] == "needs_reconciliation"
    finally:
        await shutdown_browsers()
