"""Owner reminders cannot resolve a different client's confirmation."""
import asyncio
from types import SimpleNamespace

import pytest

from src.gateway import im_runtime
from src.web.routers import realtime
from tests.unit.test_gateway_decisions import _DecisionWS
from tests.unit.test_im_bridge import FakeAdapter, ScriptedLLM
from src.im.bridge import IMBridge


@pytest.mark.asyncio
async def test_desktop_confirmation_reminds_owner_but_waits_for_original_client(monkeypatch):
    sent = []
    delivered = asyncio.Event()

    async def notifier(text):
        sent.append(text)
        delivered.set()

    monkeypatch.setattr(im_runtime, "_OWNER_NOTIFIER", notifier)
    ws = _DecisionWS("owner")
    queue = asyncio.Queue()
    task = asyncio.create_task(realtime._make_ws_confirm(ws, queue)("写文件？"))
    try:
        event = await asyncio.wait_for(queue.get(), 1)
        await asyncio.wait_for(delivered.wait(), 1)
        assert "需要操作确认" in sent[0] and "写文件？" in sent[0]
        assert not task.done()
        await realtime.handle_websocket_message(
            _DecisionWS("stranger"),
            {"type": "agent_confirm_response", "id": event["id"], "ok": True},
        )
        assert not task.done()
        await realtime.handle_websocket_message(
            ws, {"type": "agent_confirm_response", "id": event["id"], "ok": False},
        )
        assert await asyncio.wait_for(task, 1) is False
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_notification_failure_does_not_block_client_approval(monkeypatch):
    failed = asyncio.Event()

    async def notifier(text):
        failed.set()
        raise OSError("offline")

    monkeypatch.setattr(im_runtime, "_OWNER_NOTIFIER", notifier)
    ws = _DecisionWS("owner")
    queue = asyncio.Queue()
    task = asyncio.create_task(realtime._make_ws_confirm(ws, queue)("执行？"))
    event = await asyncio.wait_for(queue.get(), 1)
    await asyncio.wait_for(failed.wait(), 1)
    await realtime.handle_websocket_message(
        ws, {"type": "agent_confirm_response", "id": event["id"], "ok": True},
    )
    assert await asyncio.wait_for(task, 1) is True


@pytest.mark.asyncio
async def test_confirmation_close_cancels_slow_notification(monkeypatch):
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def notifier(text):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(im_runtime, "_OWNER_NOTIFIER", notifier)
    ws = _DecisionWS("owner")
    queue = asyncio.Queue()
    task = asyncio.create_task(realtime._make_ws_confirm(ws, queue)("执行？"))
    event = await asyncio.wait_for(queue.get(), 1)
    await asyncio.wait_for(started.wait(), 1)
    await realtime.handle_websocket_message(
        ws, {"type": "agent_confirm_response", "id": event["id"], "ok": False},
    )
    assert await asyncio.wait_for(task, 1) is False
    assert stopped.is_set()
    assert event["id"] not in realtime._PENDING_CONFIRMS


@pytest.mark.asyncio
async def test_blocked_task_reminds_owner_to_answer_in_original_client(tmp_path):
    adapter = FakeAdapter()
    bridge = IMBridge(str(tmp_path), adapter, "42", channel="telegram",
                      llm=ScriptedLLM("must not run"))
    bridge._on_task_update(SimpleNamespace(id="task-42", status="blocked"))
    await asyncio.sleep(0)
    assert len(adapter.sent) == 1
    assert "task-42" in str(adapter.sent[0]) and "等待你的回答" in str(adapter.sent[0])
