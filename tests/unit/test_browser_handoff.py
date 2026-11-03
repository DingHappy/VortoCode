"""Real gate/WS/website adapter with a fake browser; no model or website traffic."""
import asyncio
import json

import pytest

from src.agents.confirmation_context import (
    start_confirmation_turn, website_confirmation,
)
from src.agents.gate import make_confirm_gate
from src.agents.tools.browser import build_browser_tools
from src.agents.tools.website_browser import shutdown_browsers
from src.browser.connections import BrowserConnections
from src.browser.page_privacy import AUTH_STATE_JS, SNAPSHOT_JS
from src.gateway import protocol as P
from src.web.routers import realtime
from tests.unit.test_browser_control import _FakeBrowser
from tests.unit.test_gateway_decisions import _DecisionWS


class Window(_FakeBrowser):
    def __init__(self):
        super().__init__()
        self.reads = []

    async def evaluate(self, expression):
        self.reads.append(expression)
        return await super().evaluate(expression)


class Client(_DecisionWS):
    def __init__(self, sid):
        super().__init__(sid)
        self.events = []

    async def send_json(self, event):
        self.events.append(event)


@pytest.fixture
async def flow(tmp_path, monkeypatch):
    from src.agents import web_fetch
    monkeypatch.setattr(web_fetch, "refusal_reason", lambda _host: "")
    root = tmp_path / "project"
    root.mkdir()
    registry = BrowserConnections(str(root), state_root=tmp_path / "host")
    browser = Window()
    client, queue = Client("browser-owner"), asyncio.Queue()
    gate = make_confirm_gate(realtime._make_ws_confirm(client, queue), can_ask_human=True)
    tools = {t.name: t for t in build_browser_tools(str(root), gate,
        browser_factory=lambda: browser, connections=registry)}
    start_confirmation_turn()
    pending = []

    def run(name, args=None):
        task = asyncio.create_task(tools[name].handler(args or {}))
        pending.append(task)
        return task

    async def next_card():
        while True:
            event = await asyncio.wait_for(queue.get(), 1)
            if event["type"] == "agent_confirm":
                return event

    async def answer(card, ok, source=None):
        await realtime.handle_websocket_message(source or client,
            {"type": "agent_confirm_response", "id": card["id"], "ok": ok})

    yield registry, browser, client, queue, run, next_card, answer
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    await shutdown_browsers()


async def open_login(flow):
    registry, browser, client, queue, run, next_card, answer = flow
    row = registry.approve("https://example.com", "工作", "mobile")
    task = run("browser_open", {"url": "https://example.com/private/callback?code=synthetic-secret",
                                "connection_id": row["id"]})
    opening = await next_card()
    assert opening["website"] == {"version": 1, "action": "open", "origin": "https://example.com",
                                  "account_label": "工作", "device_mode": "mobile"}
    await answer(opening, True)
    login = await next_card()  # No model call to browser_login.
    assert login["website"]["action"] == "login"
    assert "synthetic-secret" not in str(login) and not browser.reads
    return row, task, login


async def test_inline_handoff_waits_for_original_owner_and_checks_before_reading(flow):
    registry, browser, client, queue, run, next_card, answer = flow
    row, task, card = await open_login(flow)
    await answer(card, True, Client("another-session"))
    assert not task.done() and not browser.reads
    browser.url = "https://example.com/finished?code=synthetic-secret"
    await answer(card, True)
    result = await asyncio.wait_for(task, 1)
    assert "主人已确认" in result and "正文" in result and "synthetic-secret" not in result
    assert AUTH_STATE_JS in browser.reads and SNAPSHOT_JS in browser.reads
    assert "不重复请求" in await run("browser_login")
    assert "正文" in await run("browser_snapshot")
    assert not realtime.pending_confirmations("sid-browser-owner")
    assert website_confirmation() is None


async def test_declined_login_stays_paused_without_reprompt_or_page_reads(flow):
    registry, browser, client, queue, run, next_card, answer = flow
    row, task, card = await open_login(flow)
    await answer(card, False)
    assert "真人" in await task
    assert "本轮已请求" in await run("browser_snapshot")
    assert "本轮已请求" in await run("browser_login")
    assert not browser.reads
    assert not realtime.pending_confirmations("sid-browser-owner")
    # A new user turn may ask again; it does not replace the browser or grant.
    start_confirmation_turn()
    resumed = run("browser_login")
    second = await next_card()
    assert second["id"] != card["id"]
    assert second["website"] == card["website"]
    browser.url = "https://example.com/ready"
    await answer(second, True)
    assert "主人已确认" in await resumed


async def test_early_resume_on_sso_does_not_read_login_or_auto_reask(flow):
    registry, browser, client, queue, run, next_card, answer = flow
    row, task, card = await open_login(flow)
    browser.url = "https://accounts.google.com/signin?code=synthetic-secret"
    await answer(card, True)
    result = await task
    assert "真人" in result and SNAPSHOT_JS not in browser.reads
    before = list(browser.reads)
    assert "本轮已请求" in await run("browser_login")
    assert browser.reads == before and not realtime.pending_confirmations("sid-browser-owner")


async def test_form_appearing_at_final_snapshot_keeps_pause_without_false_resume(flow):
    registry, browser, client, queue, run, next_card, answer = flow
    row, task, card = await open_login(flow)
    original = browser.evaluate

    async def changed(expression):
        if expression == SNAPSHOT_JS:
            return {"needs_login": True}
        return await original(expression)

    browser.evaluate = changed
    await answer(card, True)
    result = await task
    assert "真人" in result and "主人已确认恢复" not in result
    assert "本轮已请求" in await run("browser_snapshot")


async def test_revoked_grant_during_login_cannot_resume(flow):
    registry, browser, client, queue, run, next_card, answer = flow
    row, task, card = await open_login(flow)
    registry.revoke(row["id"])
    await answer(card, True)
    assert "撤销" in await task and not browser.reads


async def test_anonymous_handoff_cannot_resume_on_a_different_public_site(flow):
    registry, browser, client, queue, run, next_card, answer = flow
    task = run("browser_open", {"url": "https://example.com/login"})
    card = await next_card()
    assert card["website"]["action"] == "login"
    assert card["website"]["origin"] == "https://example.com"
    browser.url = "https://another.example/ready"
    await answer(card, True)
    assert "真人" in await task
    assert AUTH_STATE_JS not in browser.reads and SNAPSHOT_JS not in browser.reads


async def test_cancelled_handoff_closes_card_and_cannot_accept_late_approval(flow):
    registry, browser, client, queue, run, next_card, answer = flow
    row, task, card = await open_login(flow)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not realtime.pending_confirmations("sid-browser-owner")
    await answer(card, True)
    assert not browser.reads and browser.running
    assert "本轮已请求" in await run("browser_login")


async def test_pending_handoff_replays_original_card_and_no_new_approval(flow, monkeypatch):
    registry, browser, client, queue, run, next_card, answer = flow
    row, task, card = await open_login(flow)
    stored = realtime.pending_confirmations("sid-browser-owner")[0]
    assert stored["website"] == card["website"]
    stored["website"]["origin"] = "https://changed.example"
    assert realtime.pending_confirmations("sid-browser-owner")[0]["website"] == card["website"]
    monkeypatch.setattr(realtime, "_get_session", lambda _ws: {"transcript": [], "agent": None})
    monkeypatch.setattr(realtime, "_prompt_queue_event", lambda _ws: P.make_event(P.AGENT_QUEUE, items=[]))
    reconnected = Client("browser-owner")
    await realtime._replay_history(reconnected)
    replayed = next(e for e in reconnected.events if e["type"] == "agent_confirm")
    assert replayed["id"] == card["id"] and replayed["website"] == card["website"]
    for key, expected in {"operation_kind": "interact", "risk": "high", "required_key": "approval", "approval_scope": "once"}.items():
        assert replayed[key] == stored[key] == card[key] == expected
    assert not task.done() and not browser.reads
    await answer(card, False, reconnected)
    assert "真人" in await task


async def test_declined_connection_does_not_repeat_card_within_turn(flow):
    registry, browser, client, queue, run, next_card, answer = flow
    args = {"url": "https://example.com/secret-path?code=synthetic-secret", "account_label": "个人"}
    first = run("browser_connect", args)
    card = await next_card()
    assert card["website"]["action"] == "connect" and "synthetic-secret" not in str(card)
    await answer(card, False)
    assert "未同意" in await first
    assert "未同意" in await run("browser_connect", args)
    assert not registry.list() and not browser.running
    assert not realtime.pending_confirmations("sid-browser-owner")
    assert website_confirmation() is None


async def test_regular_confirmation_does_not_inherit_website_metadata(flow):
    registry, browser, client, queue, run, next_card, answer = flow
    row, task, card = await open_login(flow)
    await answer(card, False)
    await task
    ordinary = asyncio.create_task(realtime._make_ws_confirm(client, queue)("普通操作？"))
    try:
        event = await next_card()
        assert "website" not in event
        await answer(event, False)
        assert await ordinary is False
    finally:
        ordinary.cancel()
        await asyncio.gather(ordinary, return_exceptions=True)


async def test_real_agent_turn_resets_handoff_budget_only_for_new_user_input(tmp_path, monkeypatch):
    from src.agents import web_fetch
    from src.agents.main_agent import MainAgent
    from tests.unit.test_main_agent import ScriptedLLM
    monkeypatch.setattr(web_fetch, "refusal_reason", lambda _host: "")
    root = tmp_path / "project"
    root.mkdir()
    registry = BrowserConnections(str(root), state_root=tmp_path / "host")
    row = registry.approve("https://example.com", "工作", "desktop")
    browser, cards = Window(), []

    async def ask(_message):
        cards.append(website_confirmation())
        return cards[-1]["action"] != "login"

    tools = build_browser_tools(str(root), make_confirm_gate(ask, can_ask_human=True),
        browser_factory=lambda: browser, connections=registry)
    opening = json.dumps({"tool": "browser_open", "args": {"url": "https://example.com:443/", "connection_id": row["id"]}})
    login = '{"tool":"browser_login","args":{}}'
    agent = MainAgent(tools, llm=ScriptedLLM(opening, login, "等待你处理。", login, "仍保持暂停。"))
    try:
        await agent.run_turn("打开工作网站", mode="build")
        assert [c["action"] for c in cards] == ["open", "login"]
        await agent.run_turn("请再让我核对", mode="build")
        assert [c["action"] for c in cards] == ["open", "login", "login"]
        assert not browser.reads
    finally:
        await shutdown_browsers()
