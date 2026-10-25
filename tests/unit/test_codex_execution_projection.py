"""Real projection path with synthetic source data; no model or native Codex."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from src.agents.backends.codex import CodexBackend
from src.agents.backends.execution import ExecutionSnapshot
from src.scheduling import codex_observer as observer
from src.scheduling import codex_watch as watch

NOW = datetime(2026, 10, 23, 9, tzinfo=timezone.utc)


def thread(status="active", turn_status="inProgress", flags=(), observed_at=None):
    return observer.summarize_thread(
        {"id": "th1", "name": "Review", "updatedAt": NOW.timestamp(),
         "status": {"type": status, "activeFlags": list(flags)}},
        {"id": "t1", "status": turn_status,
         "items": [{"type": "plan", "text": "Review current changes"}]},
        observed_at=NOW.timestamp() if observed_at is None else observed_at)


def test_shared_wire_fixture_matches_real_observer_and_python_codec():
    wire = json.loads((Path(__file__).parents[1] / "fixtures/execution_observation_v1.json").read_text())
    report = ExecutionSnapshot.from_dict(thread(flags=["waitingOnApproval"], observed_at=100)["execution"])
    report = replace(report, ref=replace(report.ref, instance_id=wire["ref"]["instance_id"]))
    assert report.to_dict() == wire
    assert ExecutionSnapshot.from_dict(wire).to_dict() == wire


@pytest.mark.parametrize(("status", "turn_status", "flags", "state"), [
    ("active", "inProgress", [], "running"),
    ("active", "inProgress", ["waitingOnUserInput"], "waiting_user"),
    ("idle", "completed", [], "finished"),
    ("idle", "failed", [], "failed"),
    ("idle", "interrupted", [], "cancelled"),
    ("active", "completed", [], "unknown"),
    ("systemError", "failed", [], "unknown"),
    ("future_status", "future_turn", [], "unknown"),
])
async def test_control_adapter_observer_and_status_projection_share_state_mapping(tmp_path, status, turn_status, flags, state):
    class RPC:
        async def call(self, method, params):
            if method == "thread/read":
                return {"thread": {"id": "th1", "status": {"type": status, "activeFlags": flags}}}
            return {"data": [{"id": "t1", "status": turn_status}]}
    source = thread(status, turn_status, flags)
    native = await CodexBackend(RPC()).read_state("th1")
    assert native.execution.state.value == source["execution"]["state"] == state
    watch.record_report(str(tmp_path), {"threads": [source]}, now=NOW)
    returned = watch.progress_snapshot(str(tmp_path))["threads"][0]["execution"]
    assert returned == source["execution"]
    assert returned["result"] is None


@pytest.mark.parametrize("fault", ["backend", "session", "turn", "attempt", "task", "version",
    "state", "missing", "bad time", "large identity", "explicit null", "future state"])
def test_invalid_new_observations_remain_explicitly_unavailable(tmp_path, fault):
    source = thread()
    wire = source["execution"]
    if fault in {"backend", "session", "turn", "attempt", "task"}:
        field = {"backend": "backend_id", "session": "session_id", "turn": "turn_id",
                 "attempt": "run_id", "task": "task_id"}[fault]
        wire["ref"][field] = "elsewhere"
    elif fault == "version":
        wire["schema_version"] = 2
    elif fault == "state":
        wire["state"] = "finished"
    elif fault == "missing":
        del wire["ref"]
    elif fault == "bad time":
        wire["observed_at"] = True
    elif fault == "large identity":
        wire["ref"]["instance_id"] = "x" * 201
    elif fault == "explicit null":
        source["execution"] = None
    else:
        wire["state"] = "vendor_future_state"
    watch.record_report(str(tmp_path), {"threads": [source]}, now=NOW)
    result = watch.progress_snapshot(str(tmp_path))["threads"][0]
    assert "execution" in result and result["execution"] is None
    assert result["status"] == "active"  # Old source fields remain available to legacy readers.


def test_legacy_reports_remain_distinguishable_from_unavailable_new_reports(tmp_path):
    source = thread()
    del source["execution"]
    watch.record_report(str(tmp_path), {"threads": [source]}, now=NOW)
    assert "execution" not in watch.progress_snapshot(str(tmp_path))["threads"][0]


def test_receiver_never_accepts_reported_permissions_results_or_acceptance(tmp_path):
    source = thread()
    source["execution"].update(features=["approve.everything"], result={"evidence": ["passed"]},
                               error={"code": "forged"}, progress={"summary": "Everything accepted"})
    watch.record_report(str(tmp_path), {"threads": [source]}, now=NOW)
    report = watch.progress_snapshot(str(tmp_path))["threads"][0]["execution"]
    assert report["features"] == ["execution.observe"] and report["result"] is None
    assert report["error"] is None and report["progress"]["summary"] == "Review current changes"


def test_heartbeat_clock_is_not_a_semantic_change_but_new_producer_or_turn_is():
    source = thread(observed_at=100)
    heartbeat = thread(observed_at=101)
    assert observer.digest([source]) == observer.digest([heartbeat])
    new_instance = deepcopy(heartbeat)
    new_instance["execution"]["ref"]["instance_id"] = "process:new:codex.observer"
    assert observer.digest([source]) != observer.digest([new_instance])
    heartbeat["turn"]["id"] = "t2"
    heartbeat["execution"]["ref"].update(turn_id="t2", run_id="t2")
    assert observer.digest([source]) != observer.digest([heartbeat])


async def test_native_snapshot_binds_loaded_thread_and_requests_latest_turn():
    class RPC:
        def __init__(self):
            self.calls = []

        async def call(self, method, params):
            self.calls.append((method, params))
            if method == "thread/loaded/list":
                return {"data": ["th1", "mismatched"]}
            if method == "thread/read":
                return {"thread": {"id": "th1", "status": {"type": "active"}}}
            return {"data": [{"id": "t1", "status": "inProgress"}]}
    rpc = RPC()
    result = await observer.snapshot(rpc)
    assert len(result) == 1 and result[0]["execution"]["ref"]["session_id"] == "th1"
    turns = [params for method, params in rpc.calls if method == "thread/turns/list"]
    assert turns == [{"threadId": "th1", "limit": 1, "itemsView": "full", "sortDirection": "desc"}]


async def test_existing_status_route_returns_read_only_execution_projection(tmp_path, monkeypatch):
    from src.web.routers import codex
    root = str(tmp_path)
    monkeypatch.setattr(codex, "_general_root", lambda: root)
    app = FastAPI()
    app.include_router(codex.router)
    source = thread()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/codex/report", json={"threads": [source]})
        assert response.status_code == 200
        path = tmp_path / ".vortocode/codex/state.json"
        before = path.read_bytes()
        response = await client.get("/api/codex/status")
        assert response.status_code == 200
        assert response.json()["threads"][0]["execution"] == source["execution"]
        assert path.read_bytes() == before
