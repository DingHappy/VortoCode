"""Unattended duty must surface mixed findings and isolate scheduler failures."""
import asyncio

import pytest

from src.scheduling import cron, heartbeat, scheduler


@pytest.mark.parametrize("reply", [
    "HEARTBEAT_OK\n磁盘空间不足",
    "发现异常，不能回复 HEARTBEAT_OK",
    "引用的旧记录：HEARTBEAT_OK；当前检查失败",
    "```\nHEARTBEAT_OK\n```",
])
async def test_mixed_or_quoted_acknowledgements_are_not_silenced(tmp_path, reply):
    notifications = []

    async def run_session(*args, **kwargs):
        return reply

    async def notify(text):
        notifications.append(text)

    result = await heartbeat.run_heartbeat(str(tmp_path), run_session=run_session,
                                           notify=notify, hour=12)
    assert result["action"] == "surfaced"
    assert len(notifications) == 1
    assert reply in notifications[0]


async def test_cron_failure_does_not_skip_heartbeat(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("VORTOCODE_CRON", "1")
    monkeypatch.setenv("VORTOCODE_HEARTBEAT", "1")
    monkeypatch.setattr(heartbeat, "heartbeat_every_seconds", lambda: 0)
    stop, calls = asyncio.Event(), []

    async def failing_cron(*args, **kwargs):
        calls.append("cron")
        raise RuntimeError("private-error-detail")

    async def healthy_heartbeat(*args, **kwargs):
        calls.append("heartbeat")
        stop.set()

    monkeypatch.setattr(cron, "run_due", failing_cron)
    monkeypatch.setattr(heartbeat, "run_heartbeat", healthy_heartbeat)
    await asyncio.wait_for(scheduler.scheduler_loop(stop, cwd=str(tmp_path), runner=None, notify=None), 1)
    assert calls == ["cron", "heartbeat"]
    assert "Cron scheduler tick failed (RuntimeError)" in caplog.text
    assert "private-error-detail" not in caplog.text


async def test_heartbeat_failure_is_visible_without_retrying_the_model(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("VORTOCODE_CRON", "1")
    monkeypatch.setenv("VORTOCODE_HEARTBEAT", "1")
    monkeypatch.setattr(heartbeat, "heartbeat_every_seconds", lambda: 0)
    stop, calls = asyncio.Event(), []

    async def healthy_cron(*args, **kwargs):
        calls.append("cron")

    async def failing_heartbeat(*args, **kwargs):
        calls.append("heartbeat")
        stop.set()
        raise ValueError("private-error-detail")

    monkeypatch.setattr(cron, "run_due", healthy_cron)
    monkeypatch.setattr(heartbeat, "run_heartbeat", failing_heartbeat)
    await asyncio.wait_for(scheduler.scheduler_loop(stop, cwd=str(tmp_path), runner=None, notify=None), 1)
    assert calls == ["cron", "heartbeat"]
    assert "Heartbeat tick failed (ValueError)" in caplog.text
    assert "private-error-detail" not in caplog.text


async def test_scheduler_cancellation_propagates(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_CRON", "1")
    monkeypatch.setenv("VORTOCODE_HEARTBEAT", "1")
    calls = []

    async def cancelled_cron(*args, **kwargs):
        raise asyncio.CancelledError()

    async def heartbeat_check(*args, **kwargs):
        calls.append("heartbeat")

    monkeypatch.setattr(cron, "run_due", cancelled_cron)
    monkeypatch.setattr(heartbeat, "run_heartbeat", heartbeat_check)
    with pytest.raises(asyncio.CancelledError):
        await scheduler.scheduler_loop(asyncio.Event(), cwd=str(tmp_path), runner=None, notify=None)
    assert calls == []
