"""Follow-up acknowledgements mean sent or queued, never just attempted."""
import asyncio
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from src.scheduling import assistant_watch as watch
from src.scheduling import notices


NOW = datetime(2026, 10, 15, 9, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("failure", [False, RuntimeError("private notification body")])
async def test_failed_status_remains_pending_without_blocking_other_items(tmp_path, failure, caplog):
    root = str(tmp_path)
    items = [{"id": key, "project": "demo", "title": key, "status": "running"}
             for key in ("first", "second")]

    async def refresh(_root):
        return items

    attempts = []

    async def notify(text):
        attempts.append(text)
        if "first" in text and len(attempts) == 1:
            if isinstance(failure, Exception):
                raise failure
            return failure
        return True

    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    for item in items:
        item["status"] = "done"
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    assert len(attempts) == 2
    assert watch._load_state(root)["seen"] == {"first": "running", "second": "done"}
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    assert len(attempts) == 3 and "first" in attempts[-1]
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    assert len(attempts) == 3
    assert "private notification body" not in caplog.text


@pytest.mark.parametrize("failure", [False, RuntimeError("private report body")])
async def test_report_retries_saved_draft_without_another_model_call(tmp_path, failure, caplog):
    root = str(tmp_path)
    written, attempts = [], []

    async def refresh(_root):
        return []

    async def writer(_root, _now, _label):
        written.append(True)
        return "synthetic morning report"

    async def notify(text):
        attempts.append(text)
        if len(attempts) == 1:
            if isinstance(failure, Exception):
                raise failure
            return failure
        return True

    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=["09:00"], writer=writer)
    assert watch._load_state(root)["reports"] == []
    assert watch._load_state(root)["report_drafts"] == {"2026-10-15 09:00": attempts[0]}
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=["09:00"], writer=writer)
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=["09:00"], writer=writer)
    assert written == [True] and attempts == ["synthetic morning report"] * 2
    assert watch._load_state(root)["reports"] == ["2026-10-15 09:00"]
    assert watch._load_state(root)["report_drafts"] == {}
    assert "private report body" not in caplog.text


async def test_expired_report_draft_is_not_replayed(tmp_path):
    root = str(tmp_path)

    async def refresh(_root):
        return []

    attempts = []

    async def notify(text):
        attempts.append(text)
        return False

    async def writer(*_args):
        return "synthetic report"

    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=["09:00"], writer=writer)
    await watch.watch_once(root, NOW + timedelta(hours=3), notify=notify, refresh=refresh,
                           times=["09:00"], writer=writer)
    assert len(attempts) == 1 and watch._load_state(root)["report_drafts"] == {}


async def test_cancellation_preserves_prior_acceptance_and_retries_only_interrupted_item(tmp_path):
    root = str(tmp_path)
    items = [{"id": key, "project": "demo", "title": key, "status": "running"}
             for key in ("first", "second")]

    async def refresh(_root):
        return items

    attempts = []

    async def notify(text):
        attempts.append(text)
        if len(attempts) == 2:
            raise asyncio.CancelledError
        return True

    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    for item in items:
        item["status"] = "done"
    with pytest.raises(asyncio.CancelledError):
        await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    assert watch._load_state(root)["seen"] == {"first": "done", "second": "running"}
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    assert len(attempts) == 3 and "second" in attempts[-1]


@pytest.mark.parametrize("outcome", ["sent", "queued", "lost"])
async def test_owner_receipt_requires_send_or_persisted_retry(tmp_path, monkeypatch, outcome):
    from src.gateway import im_runtime

    async def notify_owner(*_args, **_kwargs):
        return outcome == "sent"

    monkeypatch.setattr(im_runtime, "notify_owner", notify_owner)
    # A readable file in place of logs/ causes a real filesystem write failure.
    if outcome == "lost":
        logs = tmp_path / ".vortocode" / "logs"
        logs.parent.mkdir()
        logs.write_text("blocked")
    assert await watch.owner_reporter(str(tmp_path))("synthetic notice") is (outcome != "lost")
    assert notices.pending_undelivered_count(str(tmp_path)) == (1 if outcome == "queued" else 0)


def test_relay_rejects_notification_when_send_and_retry_storage_both_fail(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.gateway import im_runtime
    from src.web.server import app

    async def notify_owner(*_args, **_kwargs):
        return False

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    monkeypatch.setattr(im_runtime, "notify_owner", notify_owner)
    logs = tmp_path / ".vortocode" / "logs"
    logs.parent.mkdir()
    logs.write_text("blocked")
    response = TestClient(app).post("/api/im/notify", json={"text": "synthetic notice"})
    assert response.status_code == 503
    assert response.json() == {"detail": "通知未发送且无法保存补发，请稍后重试"}


async def test_accepted_outbox_notification_is_not_queued_again_by_watcher(tmp_path, monkeypatch):
    from src.gateway import im_runtime

    root = str(tmp_path)
    item = {"id": "one", "project": "demo", "title": "one", "status": "running"}

    async def refresh(_root):
        return [item]

    async def disconnected(*_args, **_kwargs):
        return False

    monkeypatch.setattr(im_runtime, "notify_owner", disconnected)
    notify = watch.owner_reporter(root)
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    item["status"] = "done"
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    assert notices.pending_undelivered_count(root) == 1
    sent = []

    async def recovered(text):
        sent.append(text)

    result = await notices.flush_undelivered(root, recovered)
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    assert result["sent"] == 1 and len(sent) == 1 and "等待审查" in sent[0]
    assert notices.pending_undelivered_count(root) == 0


async def test_failed_report_composition_does_not_block_other_slots(tmp_path):
    root = str(tmp_path)
    writes, accepted = [], []

    async def refresh(_root):
        return []

    async def writer(*_args):
        writes.append(True)
        if len(writes) == 1:
            raise RuntimeError("synthetic composition failure")
        return "synthetic report"

    async def notify(text):
        accepted.append(text)
        return True

    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=["08:00", "09:00"], writer=writer)
    assert watch._load_state(root)["reports"] == ["2026-10-15 09:00"]
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=["08:00", "09:00"], writer=writer)
    assert len(writes) == 3 and len(accepted) == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_report_cache_is_private_and_generated_state_is_ignored(tmp_path):
    watch._save_state(str(tmp_path), {"report_drafts": {"slot": "synthetic private draft"}})
    assert stat.S_IMODE(watch._state_path(str(tmp_path)).stat().st_mode) == 0o600
    assert "dispatches/" in (tmp_path / ".vortocode" / ".gitignore").read_text()
    assert list(watch._state_path(str(tmp_path)).parent.glob("*.tmp")) == []
