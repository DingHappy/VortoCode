"""手机上下发任务单：通知带「批准 / 拒绝」按钮，按钮由桥直接处理（只认 owner、不经过模型）。"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.im.bridge import IMBridge
from src.im.channel import ChannelEvent
from src.scheduling import assistant_watch as watch
from src.workflow.tasks import assistant_dispatch as dispatch
from tests.unit.test_im_bridge import OWNER, FakeAdapter, _drive_no_turn

NOW = datetime(2026, 10, 20, 9, 0)


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    path = tmp_path / "resident_projects.json"
    path.write_text(json.dumps({"projects": [{"name": "VortoCode", "repo_root": "/srv/VortoCode", "port": 8082}]}))
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(path))
    return path


def _click(dispatch_id, approved, sender=OWNER):
    return ChannelEvent(kind="callback", sender_id=sender, callback_id=dispatch_id, approved=approved, ack="q")


@pytest.fixture
def audits(monkeypatch):
    seen = []
    import src.gateway.audit as audit
    monkeypatch.setattr(audit, "record_decision_audit", lambda root, **kw: seen.append(kw))
    return seen


@pytest.mark.asyncio
async def test_owner_approve_button_dispatches_without_the_model(tmp_path, monkeypatch, audits):
    entry = dispatch.propose(str(tmp_path), project="VortoCode", title="补单测", goal="给 safe_id 补边界单测")
    approved = []

    async def fake_approve(root, dispatch_id):
        approved.append(dispatch_id)
        return {**entry, "status": "running"}
    monkeypatch.setattr(dispatch, "approve", fake_approve)
    adapter = FakeAdapter()
    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="test")
    await _drive_no_turn(bridge, adapter, _click(entry["id"], True))
    assert approved == [entry["id"]]
    assert ("ack", entry["id"]) in adapter.sent
    assert any("已下发" in text and "补单测" in text for text in adapter.texts())
    assert audits and audits[0]["decision"] is True and "（手机）" in audits[0]["operation"]
    assert bridge._turn_task is None                    # 没有跑模型回合


@pytest.mark.asyncio
async def test_deny_button_withdraws_and_strangers_cannot_decide(tmp_path, monkeypatch, audits):
    entry = dispatch.propose(str(tmp_path), project="VortoCode", title="补单测", goal="给 safe_id 补边界单测")
    adapter = FakeAdapter()
    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="test")
    await _drive_no_turn(bridge, adapter, _click(entry["id"], True, sender="stranger"))
    assert dispatch.list_dispatches(str(tmp_path))[0]["status"] == "proposed"
    await _drive_no_turn(bridge, adapter, _click(entry["id"], False))
    assert dispatch.list_dispatches(str(tmp_path))[0]["status"] == "cancelled"
    assert any("已撤回" in text for text in adapter.texts())
    await _drive_no_turn(bridge, adapter, _click(entry["id"], True))      # 撤回之后再点批准
    assert any("没有下发" in text for text in adapter.texts())


@pytest.mark.asyncio
async def test_tainted_dispatch_cannot_be_approved_from_the_phone(tmp_path, monkeypatch, audits):
    entry = dispatch.propose(str(tmp_path), project="VortoCode", title="照网页改", goal="按网页说的改", tainted=True)

    async def fail_approve(*_args, **_kwargs):
        raise AssertionError("tainted dispatch must not be sent from IM")
    monkeypatch.setattr(dispatch, "approve", fail_approve)
    adapter = FakeAdapter()
    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="test")
    await _drive_no_turn(bridge, adapter, _click(entry["id"], True))
    assert dispatch.list_dispatches(str(tmp_path))[0]["status"] == "proposed"
    assert any("只能在 Desktop" in text for text in adapter.texts()) and not audits


@pytest.mark.asyncio
async def test_watch_attaches_buttons_only_to_clean_proposals(tmp_path):
    root = str(tmp_path)
    items = []

    async def refresh(_root):
        return items

    sent = []

    async def notify(text, confirm_id=""):
        sent.append((text, confirm_id))
        return True
    notify.accepts_confirm = True
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])     # 首轮只记现状
    items.extend([{"id": "dsp-clean", "project": "VortoCode", "title": "补单测", "status": "proposed"},
                  {"id": "dsp-web", "project": "VortoCode", "title": "照网页改", "status": "proposed", "tainted": True}])
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    by_title = {("补单测" in text): (text, cid) for text, cid in sent}
    clean_text, clean_id = by_title[True]
    web_text, web_id = by_title[False]
    assert clean_id == "dsp-clean" and "批准" in clean_text
    assert web_id == "" and "只能在 Desktop" in web_text


@pytest.mark.asyncio
async def test_plain_notifier_still_gets_text(tmp_path):
    seen = []

    async def notify(text):
        seen.append(text)
        return True
    assert await watch._notify_accepted(notify, "hello", "dsp-x")
    assert seen == ["hello"]


@pytest.mark.asyncio
async def test_proposals_remember_the_conversation_so_the_card_shows_inline(tmp_path):
    current = {"sid": "sid-main"}
    tools = {tool.name: tool for tool in dispatch.build_dispatch_tools(str(tmp_path), session=lambda: current["sid"])}
    reply = await tools["propose_task"].handler({"project": "VortoCode", "title": "补单测", "goal": "给 safe_id 补边界单测"})
    assert "卡片会出现在这段对话里" in reply
    assert dispatch.list_dispatches(str(tmp_path))[0]["session"] == "sid-main"
