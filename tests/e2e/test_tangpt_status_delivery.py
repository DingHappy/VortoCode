"""Original MainAgent -> read-only site evidence -> reply, all IO scripted."""
import json

import pytest

from src.agents.gate import make_confirm_gate
from src.agents.main_agent import MainAgent
from src.agents.tools.browser import build_browser_tools
from src.agents.tools.website_browser import shutdown_browsers
from src.browser import tangpt
from src.browser.connections import BrowserConnections
from tests.unit.test_browser_uncertain_actions import Browser
from tests.unit.test_tangpt_status import observation


@pytest.mark.parametrize("label,state", [("[签到已得10000, 补签卡: 88]", "page_reports_signed"),
                                        ("签到", "page_offers_checkin"), ("无法识别的状态", "unknown")])
async def test_site_status_returns_to_original_task_without_clicking_or_granting_execution(tmp_path, monkeypatch, label, state):
    from src.agents import web_fetch
    monkeypatch.setattr(web_fetch, "refusal_reason", lambda _host: "")
    root = tmp_path / "project"
    root.mkdir()
    registry = BrowserConnections(str(root), state_root=tmp_path / "host")
    row = registry.approve(tangpt.ORIGIN, "not-the-actual-account", "desktop")
    browser, approvals, replies, events = Browser(), [], [], []
    original = browser.evaluate
    async def evaluate(expression):
        return observation(label) if expression == tangpt.STATUS_JS else await original(expression)
    browser.evaluate = evaluate

    async def ask(text):
        approvals.append(text)
        return True

    tools = build_browser_tools(str(root), make_confirm_gate(ask, can_ask_human=True),
                               browser_factory=lambda: browser, connections=registry)
    entries = {tool.name: tool for tool in tools}

    class Model:
        calls = 0
        async def chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"content": json.dumps({"tool": "browser_site_status", "args": {}})}
            assert self.calls == 2 and state in str(messages)
            assert '"account_verified": false' in str(messages)
            assert '"business_verified": false' in str(messages)
            return {"content": "已读取页面签到证据；账号和站点日期仍需核对，本次未点击签到。"}

    try:
        await entries["browser_open"].handler({"url": tangpt.ORIGIN, "connection_id": row["id"]})
        await entries["browser_login"].handler({})
        count = len(approvals)
        model = Model()
        agent = MainAgent(tools, llm=model, native=False,
                          on_tool_event=lambda stage, payload: events.append((stage, payload)))
        await agent.run_turn("先看看唐帕当前账号和签到状态。", mode="plan", emit=replies.append)
        assert model.calls == 2 and len(approvals) == count and browser.clicks == []
        assert len(replies) == 1 and "本次未点击签到" in replies[0]
        finishes = [payload for stage, payload in events if stage == "finish"]
        assert len(finishes) == 1 and finishes[0]["name"] == "browser_site_status"
        assert finishes[0]["status"] == "succeeded"
    finally:
        await shutdown_browsers()
