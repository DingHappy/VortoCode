"""Observation ownership, reminder scope and one-snapshot read consistency."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from src.agents.backends.codex import CodexBackend
from src.agents.backends.control import BackendControlError
from src.scheduling import codex_observer as observer
from src.scheduling import codex_supervisor as supervisor
from src.scheduling import codex_watch as watch

NOW = datetime(2026, 10, 23, 9, tzinfo=timezone.utc)


def thread(at=NOW, *, tid="t1", state="waiting", instance="observer-A", legacy=False):
    waiting = state == "waiting"
    source = observer.summarize_thread(
        {"id": "th1", "name": "Review", "status": {"type": "idle" if state == "finished" else "active",
            "activeFlags": ["waitingOnApproval"] if waiting else []}},
        {"id": tid, "status": "completed" if state == "finished" else "inProgress",
         "items": [{"type": "userMessage", "content": [{"type": "text", "text": "Review changes"}]}]},
        observed_at=at.timestamp())
    source["execution"]["ref"]["instance_id"] = instance
    if legacy:
        del source["execution"]
    return source


@pytest.mark.parametrize("change", ["turn", "instance", "host", "kind", "gap", "legacy turn"])
@pytest.mark.parametrize("already_notified", [False, True])
def test_a_new_wait_neither_inherits_old_timer_nor_loses_its_notification(tmp_path, change, already_notified):
    root = str(tmp_path)
    initial = thread(legacy=change == "legacy turn")
    assert watch.record_report(root, {"threads": [initial], "host": "Mac-A"}, now=NOW) == []
    if already_notified:
        notices = watch.record_report(root, {"threads": [initial], "host": "Mac-A"}, now=NOW + timedelta(minutes=3))
        assert len(notices) == 1
    later = NOW + timedelta(minutes=4 if already_notified else 1)
    if change == "gap":
        later += timedelta(hours=1)
    next_thread = thread(later, tid="t2" if change in {"turn", "legacy turn"} else "t1",
                         instance="observer-B" if change == "instance" else "observer-A", legacy=change == "legacy turn")
    if change == "kind":
        next_thread["flags"] = ["waitingOnUserInput"]
    host = "Mac-B" if change == "host" else "Mac-A"
    payload = {"threads": [next_thread], "host": host}
    assert watch.record_report(root, payload, now=later) == []
    assert watch.record_report(root, payload, now=later + timedelta(minutes=1)) == []
    notices = watch.record_report(root, payload, now=later + timedelta(minutes=2))
    assert len(notices) == 1
    assert watch.record_report(root, payload, now=later + timedelta(minutes=3)) == []


@pytest.mark.parametrize("fault", ["unavailable", "unsupported", "expired", "future"])
def test_unusable_new_observations_do_not_generate_waiting_reminders(tmp_path, fault):
    source = thread()
    if fault == "unavailable":
        source["execution"] = None
    elif fault == "unsupported":
        source["execution"]["schema_version"] = 2
    else:
        source["execution"]["observed_at"] += -31 * 60 if fault == "expired" else 10 * 60
    root = str(tmp_path)
    assert watch.record_report(root, {"threads": [source]}, now=NOW) == []
    assert watch.record_report(root, {"threads": [source]}, now=NOW + timedelta(minutes=3)) == []
    assert "状态待核对" in watch.overview(root, now=NOW + timedelta(minutes=3))
    assert "状态待核对" in watch.brief(root, now=NOW + timedelta(minutes=3))


def test_status_overview_and_details_use_exactly_one_loaded_report(tmp_path, monkeypatch):
    root = str(tmp_path)
    watch.record_report(root, {"threads": [thread()]}, now=NOW)
    state = watch.load_state(root)
    reads = []
    def load(_root):
        reads.append(_root)
        return state if len(reads) == 1 else {"threads": [], "received_at": NOW.isoformat()}
    monkeypatch.setattr(watch, "load_state", load)
    monkeypatch.setattr(watch, "_now", lambda: NOW)
    result = watch.progress_snapshot(root)
    assert "Review" in result["overview"] and result["threads"][0]["name"] == "Review"
    assert reads == [root]


@pytest.mark.parametrize("received", ["not-a-date", "2026-10-23T09:00:00", "2026-10-24T09:00:00+00:00"])
async def test_unusable_receipt_times_do_not_become_current_progress_or_supervision(tmp_path, monkeypatch, received):
    source = thread(state="running", legacy=True)
    state = {"threads": [source], "received_at": received}
    monkeypatch.setattr(watch, "load_state", lambda _root: state)
    monkeypatch.setattr(watch, "_now", lambda: NOW)
    result = watch.progress_snapshot(str(tmp_path))
    assert "正在做" not in result["overview"]
    assert watch.brief(str(tmp_path), now=NOW) == ""
    calls = []
    async def notify(*args, **kwargs):
        calls.append(args)
    await supervisor.supervise_once(str(tmp_path), NOW, notify=notify)
    assert not calls and not watch.load_actions(str(tmp_path))


class RPC:
    def __init__(self):
        self.failure = None
        self.waiting = None

    async def call(self, method, params):
        if self.waiting is not None and method == "thread/read":
            ready, release = self.waiting
            ready.set()
            await release.wait()
        if self.failure == method:
            raise TimeoutError("offline")
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "status": {"type": "idle"}}}
        return {"data": [{"id": "t1", "status": "completed"}]}


@pytest.mark.parametrize("method", ["thread/read", "thread/turns/list"])
async def test_failed_refresh_cannot_leave_a_previous_finished_report(method):
    rpc = RPC()
    backend = CodexBackend(rpc)
    await backend.read_state("th1")
    assert backend.execution_report.state.value == "finished"
    rpc.failure = method
    with pytest.raises(TimeoutError):
        await backend.read_state("th1")
    assert backend.execution_report is None


async def test_pending_and_cancelled_refresh_do_not_present_prior_success():
    rpc = RPC()
    backend = CodexBackend(rpc)
    await backend.read_state("th1")
    ready, release = asyncio.Event(), asyncio.Event()
    rpc.waiting = ready, release
    task = asyncio.create_task(backend.read_state("th1"))
    await ready.wait()
    assert backend.execution_report is None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert backend.execution_report is None


@pytest.mark.parametrize("old_outcome", ["success", "failure", "cancelled"])
async def test_late_old_read_never_replaces_new_session_observation(old_outcome):
    ready, release = asyncio.Event(), asyncio.Event()
    class ConcurrentRPC:
        async def call(self, method, params):
            if method == "thread/read":
                if params["threadId"] == "old":
                    ready.set()
                    await release.wait()
                    if old_outcome == "failure":
                        raise BackendControlError("old read failed")
                return {"thread": {"id": params["threadId"], "status": {"type": "active"}}}
            return {"data": [{"id": f"turn-{params['threadId']}", "status": "inProgress"}]}
    backend = CodexBackend(ConcurrentRPC())
    old = asyncio.create_task(backend.read_state("old"))
    await ready.wait()
    latest = await backend.read_state("new")
    if old_outcome == "cancelled":
        old.cancel()
    release.set()
    if old_outcome == "success":
        assert (await old).session_id == "old"
    else:
        with pytest.raises(asyncio.CancelledError if old_outcome == "cancelled" else BackendControlError):
            await old
    assert backend.execution_report == latest.execution
    assert backend.execution_report.ref.session_id == "new"


@pytest.mark.parametrize("state", ["running", "finished"])
@pytest.mark.parametrize("fault", ["unavailable", "expired", "future"])
async def test_supervision_does_not_nudge_or_review_unusable_source_observations(tmp_path, state, fault):
    root, notices = str(tmp_path), []
    async def notify(text, **kwargs):
        notices.append(text)
        return True
    class NoLLM:
        async def chat(self, *args, **kwargs):
            pytest.fail("An unusable observation must not spend model budget")
    watch.record_report(root, {"threads": [thread(state="running")]}, now=NOW)
    await supervisor.supervise_once(root, NOW, notify=notify, llm=NoLLM())
    at = NOW + timedelta(minutes=5)
    source = thread(at, state=state)
    if fault == "unavailable":
        source["execution"] = None
    elif fault == "expired":
        source["execution"]["observed_at"] -= 60 * 60
    else:
        source["execution"]["observed_at"] += 60 * 60
    watch.record_report(root, {"threads": [source]}, now=at)
    await supervisor.supervise_once(root, NOW + timedelta(minutes=30), notify=notify, llm=NoLLM())
    assert not watch.load_actions(root)
    assert any("状态待核对" in text for text in notices)


async def test_review_from_prior_source_instance_is_discarded_even_for_same_native_turn(tmp_path):
    root, calls = str(tmp_path), []
    async def notify(*args, **kwargs):
        return True
    watch.record_report(root, {"threads": [thread(state="running")]}, now=NOW)
    await supervisor.supervise_once(root, NOW, notify=notify)
    at = NOW + timedelta(minutes=5)
    source = thread(at, state="finished")
    watch.record_report(root, {"threads": [source]}, now=at)
    class ReconnectedLLM:
        async def chat(self, *args, **kwargs):
            calls.append(True)
            next_source = deepcopy(source)
            next_source["execution"]["ref"]["instance_id"] = "observer-B"
            next_source["execution"]["observed_at"] = (NOW + timedelta(minutes=30)).timestamp()
            watch.record_report(root, {"threads": [next_source]}, now=NOW + timedelta(minutes=30))
            return {"content": '{"verdict":"continue","assessment":"More work","next":"Run tests"}'}
    await supervisor.supervise_once(root, NOW + timedelta(minutes=30), notify=notify, llm=ReconnectedLLM())
    assert calls == [True]
    assert not watch.load_actions(root)
