"""Causal snapshot replacement and retry tests: local data and fake transports."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Barrier

import httpx
import pytest
from fastapi import FastAPI

from src.agents.backends.observation_order import ObservationConflict, ObservationCursor, ObservationOrder
from src.scheduling import codex_observer as observer
from src.scheduling import codex_watch as watch

NOW = datetime(2026, 10, 23, 9, tzinfo=timezone.utc)


def payload(generation="g1", sequence=1, parent=None, *, status="active", turn_id="t1"):
    order = ObservationOrder(ObservationCursor("Mac-A", generation, sequence), parent)
    return {"host": "Mac-A", "report_order": order.to_dict(), "threads": [{"id": "th1", "name": "Review",
        "status": status, "flags": [], "turn": {"id": turn_id, "status": "inProgress" if status == "active" else "completed"}}]}


def cursor(raw):
    return ObservationCursor.from_dict(raw)


def test_ordered_reports_replace_in_sequence_and_persist_through_receiver_reload(tmp_path):
    root = str(tmp_path)
    first = watch.receive_report(root, payload(), now=NOW)
    assert first.cursor == payload()["report_order"]["cursor"] and not first.duplicate
    next_payload = payload(sequence=2, parent=cursor(first.cursor), status="idle")
    second = watch.receive_report(root, next_payload, now=NOW + timedelta(minutes=1))
    loaded = json.loads((tmp_path / ".vortocode/codex/state.json").read_text())
    assert second.cursor == loaded["report_cursor"]
    assert len(loaded["events"]) == 1 and loaded["daily"]
    following = payload(sequence=3, parent=cursor(loaded["report_cursor"]), turn_id="t2")
    watch.receive_report(root, following, now=NOW + timedelta(minutes=2))
    assert watch.progress_snapshot(root)["threads"][0]["turn"]["id"] == "t2"


def test_exact_duplicate_never_refreshes_time_events_marks_or_notifications(tmp_path):
    root = str(tmp_path)
    original = payload()
    first = watch.receive_report(root, original, now=NOW)
    path = tmp_path / ".vortocode/codex/state.json"
    before = path.read_bytes()
    duplicate = watch.receive_report(root, deepcopy(original), now=NOW + timedelta(hours=2))
    assert duplicate.duplicate and duplicate.cursor == first.cursor and duplicate.notices == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("fault", ["same cursor different body", "old parent", "skipped sequence", "old sequence", "new generation not first", "legacy"])
def test_stale_conflicting_or_downgraded_reports_never_overwrite_current_state(tmp_path, fault):
    root = str(tmp_path)
    first = watch.receive_report(root, payload(), now=NOW)
    second = watch.receive_report(root, payload(sequence=2, parent=cursor(first.cursor)), now=NOW)
    stale = payload(sequence=3, parent=cursor(second.cursor))
    if fault == "same cursor different body":
        stale = payload(sequence=2, parent=cursor(first.cursor), status="idle")
    elif fault == "old parent":
        stale = payload(sequence=3, parent=cursor(first.cursor))
    elif fault == "skipped sequence":
        stale = payload(sequence=4, parent=cursor(second.cursor))
    elif fault == "old sequence":
        stale = payload()
    elif fault == "new generation not first":
        stale = payload("g2", sequence=3, parent=cursor(second.cursor))
    else:
        del stale["report_order"]
    path = tmp_path / ".vortocode/codex/state.json"
    before = path.read_bytes()
    with pytest.raises(ObservationConflict):
        watch.receive_report(root, stale, now=NOW + timedelta(minutes=1))
    assert path.read_bytes() == before


def test_new_generation_must_name_current_predecessor_and_old_generation_is_fenced(tmp_path):
    root = str(tmp_path)
    old = watch.receive_report(root, payload(), now=NOW)
    new = watch.receive_report(root, payload("g2", parent=cursor(old.cursor)), now=NOW)
    assert new.cursor["generation_id"] == "g2"
    with pytest.raises(ObservationConflict):
        watch.receive_report(root, payload(sequence=2, parent=cursor(old.cursor)), now=NOW)
    assert watch.load_state(root)["report_cursor"] == new.cursor


def test_a_previously_unseen_delayed_generation_cannot_take_over_by_arrival_time(tmp_path):
    root = str(tmp_path)
    delayed = payload("delayed")
    accepted = watch.receive_report(root, payload("current"), now=NOW)
    with pytest.raises(ObservationConflict):
        watch.receive_report(root, delayed, now=NOW + timedelta(hours=1))
    assert watch.load_state(root)["report_cursor"] == accepted.cursor


def test_legacy_is_supported_until_an_ordered_producer_activates(tmp_path):
    root = str(tmp_path)
    assert watch.record_report(root, {"threads": []}, now=NOW) == []
    view = watch.progress_snapshot(root)
    assert view["report_protocol"] == 1 and view["report_cursor"] is None
    watch.receive_report(root, payload(), now=NOW)
    with pytest.raises(ObservationConflict):
        watch.record_report(root, {"threads": []}, now=NOW)


def test_competing_first_reports_cannot_both_commit(tmp_path):
    root, barrier = str(tmp_path), Barrier(2)
    def receive(generation):
        barrier.wait(timeout=5)
        try:
            watch.receive_report(root, payload(generation), now=NOW)
            return "committed"
        except (ObservationConflict, watch.ObservationBusy):
            return "rejected"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(receive, ["g1", "g2"]))
    assert sorted(outcomes) == ["committed", "rejected"]
    assert watch.load_state(root)["report_cursor"]["sequence"] == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file-lock subprocess probe")
def test_another_process_holding_the_commit_lock_returns_busy_without_writing(tmp_path):
    directory = tmp_path / ".vortocode/codex"
    directory.mkdir(parents=True)
    code = "import fcntl,sys; f=open(sys.argv[1],'a+b'); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); sys.stdin.readline()"
    with subprocess.Popen([sys.executable, "-c", code, str(directory / ".report.lock")],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as proc:
        try:
            assert proc.stdout.readline().strip() == "ready"
            with pytest.raises(watch.ObservationBusy):
                watch.receive_report(str(tmp_path), payload(), now=NOW)
            assert not (directory / "state.json").exists()
        finally:
            proc.communicate("release\n", timeout=5)
    watch.receive_report(str(tmp_path), payload(), now=NOW)


@pytest.mark.parametrize("invalid", [None, {}, {"schema_version": True, "parent": None},
    {"schema_version": 2, "parent": None},
    {"schema_version": 1, "parent": None, "cursor": {"producer_id": "p", "generation_id": "g", "sequence": True}},
    {"schema_version": 1, "parent": None, "cursor": {"producer_id": "", "generation_id": "g", "sequence": 1}},
    {"schema_version": 1, "parent": None, "cursor": {"producer_id": "p", "generation_id": "g", "sequence": 2 ** 53}},
])
def test_malformed_order_never_silently_falls_back_to_legacy(tmp_path, invalid):
    body = {"threads": [], "report_order": invalid}
    with pytest.raises(ValueError):
        watch.receive_report(str(tmp_path), body, now=NOW)
    assert not (tmp_path / ".vortocode/codex/state.json").exists()


class LocalTransport:
    def __init__(self, root):
        self.root, self.requests = root, []
        self.lose_reply = False

    async def __call__(self, method, path, body=None):
        self.requests.append((method, path, deepcopy(body)))
        if method == "GET":
            return watch.progress_snapshot(self.root)
        try:
            result = watch.receive_report(self.root, body, now=NOW)
        except ObservationConflict as error:
            raise observer.ResidentHTTPError(409) from error
        if self.lose_reply:
            self.lose_reply = False
            raise TimeoutError("response lost after commit")
        return {"ok": True, "report_cursor": result.cursor, "duplicate": result.duplicate}


async def test_publisher_retries_exact_pending_report_then_sends_latest_snapshot(tmp_path):
    transport = LocalTransport(str(tmp_path))
    publisher = observer.ReportPublisher(call=transport, generation_id="g1")
    first_threads = payload()["threads"]
    transport.lose_reply = True
    with pytest.raises(TimeoutError):
        await publisher.publish(first_threads)
    pending = deepcopy(publisher.pending)
    first_threads[0]["name"] = "Mutated after send"
    latest = payload(status="idle")["threads"]
    assert await publisher.publish(latest)
    sent = [body for method, _, body in transport.requests if method == "POST"]
    assert len(sent) == 3 and sent[0] == sent[1] == pending
    assert sent[2]["report_order"]["cursor"]["sequence"] == 2
    state = watch.load_state(str(tmp_path))
    assert state["threads"][0]["status"] == "idle" and len(state["events"]) == 1


async def test_superseded_publisher_does_not_reclaim_new_cursor_or_retry_old_reports(tmp_path):
    transport = LocalTransport(str(tmp_path))
    old = observer.ReportPublisher(call=transport, generation_id="old")
    new = observer.ReportPublisher(call=transport, generation_id="new")
    assert await old.publish([])
    assert await new.publish([])
    assert not await old.publish([])
    count = len(transport.requests)
    assert old.superseded and not await old.publish([])
    assert len(transport.requests) == count
    assert watch.load_state(str(tmp_path))["report_cursor"]["generation_id"] == "new"


async def test_transient_busy_keeps_pending_identity_and_allows_safe_retry(tmp_path):
    base = LocalTransport(str(tmp_path))
    bodies = []
    async def transport(method, path, body=None):
        if method == "POST":
            bodies.append(deepcopy(body))
            if len(bodies) == 1:
                raise observer.ResidentHTTPError(503)
        return await base(method, path, body)
    publisher = observer.ReportPublisher(call=transport, generation_id="g1")
    with pytest.raises(observer.ResidentHTTPError):
        await publisher.publish([])
    assert publisher.pending is not None and not publisher.superseded
    assert await publisher.publish([])
    assert bodies[0] == bodies[1]


async def test_cancellation_after_send_keeps_original_pending_report_for_acknowledgment(tmp_path):
    base = LocalTransport(str(tmp_path))
    ready = asyncio.Event()
    first = True
    async def transport(method, path, body=None):
        nonlocal first
        result = await base(method, path, body)
        if method == "POST" and first:
            first = False
            ready.set()
            await asyncio.Event().wait()
        return result
    publisher = observer.ReportPublisher(call=transport, generation_id="g1")
    task = asyncio.create_task(publisher.publish([]))
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    original = deepcopy(publisher.pending)
    assert original is not None and publisher.sequence == 0
    assert await publisher.publish([])
    posts = [body for method, _, body in base.requests if method == "POST"]
    assert posts[0] == posts[1] == original


async def test_old_resident_capability_absence_preserves_legacy_transport():
    calls = []
    async def transport(method, path, body=None):
        calls.append((method, body))
        return {"threads": []} if method == "GET" else {"ok": True}
    publisher = observer.ReportPublisher(call=transport)
    assert await publisher.publish([])
    assert "report_order" not in calls[-1][1]


@pytest.mark.parametrize("status", [{"report_protocol": 2}, {"report_protocol": True},
    {"report_protocol": 1}, {"report_protocol": 1, "report_cursor": {}}, []])
async def test_unknown_or_malformed_capability_never_downgrades_to_legacy(status):
    calls = []
    async def transport(method, path, body=None):
        calls.append(method)
        return status
    with pytest.raises(ValueError):
        await observer.ReportPublisher(call=transport).publish([])
    assert calls == ["GET"]


async def test_mismatched_receipt_does_not_advance_pending_sequence(tmp_path):
    base = LocalTransport(str(tmp_path))
    async def transport(method, path, body=None):
        result = await base(method, path, body)
        if method == "POST":
            result["report_cursor"]["sequence"] = 999
        return result
    publisher = observer.ReportPublisher(call=transport)
    with pytest.raises(ValueError):
        await publisher.publish([])
    assert publisher.sequence == 0 and publisher.pending is not None


async def test_parallel_publish_on_same_producer_is_refused():
    ready, release = asyncio.Event(), asyncio.Event()
    async def transport(method, path, body=None):
        ready.set()
        await release.wait()
        return {"threads": []} if method == "GET" else {"ok": True}
    publisher = observer.ReportPublisher(call=transport)
    task = asyncio.create_task(publisher.publish([]))
    await ready.wait()
    try:
        with pytest.raises(RuntimeError, match="并发"):
            await publisher.publish([])
    finally:
        release.set()
        assert await task


async def test_existing_http_routes_expose_cursor_and_reject_late_reports(tmp_path, monkeypatch):
    from src.web.routers import codex
    monkeypatch.setattr(codex, "_general_root", lambda: str(tmp_path))
    app = FastAPI()
    app.include_router(codex.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        status = (await client.get("/api/codex/status")).json()
        assert status["report_protocol"] == 1 and status["report_cursor"] is None
        first = await client.post("/api/codex/report", json=payload())
        assert first.status_code == 200 and first.json()["report_cursor"]
        assert (await client.post("/api/codex/report", json=payload())).json()["duplicate"]
        parent = cursor(first.json()["report_cursor"])
        assert (await client.post("/api/codex/report", json=payload(sequence=2, parent=parent))).status_code == 200
        assert (await client.post("/api/codex/report", json=payload())).status_code == 409
        assert (await client.post("/api/codex/report", json={"threads": []})).status_code == 409


async def test_http_publisher_response_loss_does_not_duplicate_waiting_notification(tmp_path, monkeypatch):
    from src.web.routers import codex
    from src.scheduling import notices
    monkeypatch.setattr(codex, "_general_root", lambda: str(tmp_path))
    clock, sent = [NOW], []
    monkeypatch.setattr(watch, "_now", lambda: clock[0])
    async def notify(text, **kwargs):
        sent.append(text)
        return True
    monkeypatch.setattr(notices, "make_notifier", lambda *args, **kwargs: notify)
    app = FastAPI()
    app.include_router(codex.router)
    source = payload()["threads"]
    source[0]["flags"] = ["waitingOnApproval"]
    lost = [False]
    receipts = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        async def transport(method, path, body=None):
            response = await client.request(method, path, json=body)
            if response.status_code != 200:
                raise observer.ResidentHTTPError(response.status_code)
            result = response.json()
            if method == "POST":
                receipts.append(result)
                if lost[0]:
                    lost[0] = False
                    raise TimeoutError("committed response lost")
            return result
        publisher = observer.ReportPublisher(call=transport)
        assert await publisher.publish(source)
        clock[0] += timedelta(minutes=3)
        lost[0] = True
        with pytest.raises(TimeoutError):
            await publisher.publish(source)
        assert len(sent) == 1
        assert await publisher.publish(source)
        assert len(sent) == 1 and any(item["duplicate"] for item in receipts)
