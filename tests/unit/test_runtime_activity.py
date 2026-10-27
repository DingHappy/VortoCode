"""Activity reads reuse actual owners and remain diagnostic, never a shutdown lease."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src.gateway.runtime_activity import runtime_write_count


@pytest.mark.asyncio
async def test_write_tracking_covers_inflight_requests_and_releases_after_failure():
    from src.web.server import runtime_activity_middleware
    app = FastAPI()
    app.middleware("http")(runtime_activity_middleware)
    started, release = asyncio.Event(), asyncio.Event()

    @app.post("/write")
    async def write():
        started.set()
        await release.wait()
        raise ValueError("simulated failed write")

    @app.get("/count")
    async def count():
        return {"writes": runtime_write_count()}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        pending = asyncio.create_task(client.post("/write"))
        await started.wait()
        assert (await client.get("/count")).json() == {"writes": 1}
        release.set()
        with pytest.raises(ValueError, match="failed write"):
            await pending
        assert (await client.get("/count")).json() == {"writes": 0}


@pytest.mark.asyncio
async def test_activity_counts_queued_workers_and_does_not_scan_historical_ledgers(tmp_path, monkeypatch):
    from src.workflow.tasks.tasks import TaskRunner
    from src.workflow.workbench.runs import RunManager
    from src.workflow.workbench.terminals import TerminalManager
    from src.web.routers import ops, realtime, tasks, runs, terminals
    from src.gateway.configuration_receipt import begin_configuration_receipt, end_configuration_receipt

    started, release = asyncio.Event(), asyncio.Event()
    async def worker(task, progress):
        started.set()
        await release.wait()
        return "done"

    runner = TaskRunner(str(tmp_path), worker, max_concurrent=1)
    manager = RunManager(str(tmp_path))
    terminal = TerminalManager(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    monkeypatch.setenv("VORTOCODE_MODEL_CONFIGURATION_REVISION", "revision-test")
    monkeypatch.setenv("VORTOCODE_CONFIGURATION_LAUNCH_ID", "launch-test")
    monkeypatch.setattr(tasks, "get_runner", lambda: runner)
    monkeypatch.setattr(runs, "get_run_manager", lambda: manager)
    monkeypatch.setattr(terminals, "get_terminal_manager", lambda: terminal)
    def no_scan(*args, **kwargs):
        pytest.fail("Activity must not infer idle from a bounded history scan")
    monkeypatch.setattr(runner.ledger, "list", no_scan)
    monkeypatch.setattr(manager.ledger, "list", no_scan)
    monkeypatch.setattr(realtime, "_SESSIONS", {"sid-test": {"prompt_queue": [{"text": "private-prompt"}]}})
    monkeypatch.setattr(realtime, "_PENDING_CONFIRMS", {"confirm-test": {"text": "private-confirmation"}})
    begin_configuration_receipt()
    first = await runner.submit("first")
    second = await runner.submit("waiting")
    try:
        await started.wait()
        snapshot = await ops.runtime_activity()
        assert snapshot["ready"] is True
        assert snapshot["launch_id"] == "launch-test"
        assert snapshot["counts"]["tasks"] == 2
        assert snapshot["counts"]["queued_inputs"] >= 1
        assert snapshot["counts"]["confirmations"] == 1
        assert "private-prompt" not in str(snapshot)
        assert "private-confirmation" not in str(snapshot)
        release.set()
        await runner.join(first.id)
        await runner.join(second.id)
        assert (await ops.runtime_activity())["counts"]["tasks"] == 0
    finally:
        release.set()
        await runner.shutdown()
        end_configuration_receipt()


def test_active_owner_counts_include_cleanup_and_open_terminals(tmp_path):
    from src.gateway.session_actor import SessionActors
    from src.workflow.workbench.runs import RunManager
    from src.workflow.workbench.terminals import TerminalManager
    actors = SessionActors(clean_item=lambda value: value)
    actors.tasks["sid-cleanup"] = SimpleNamespace(done=lambda: True)
    actors._operation_kind["sid-cleanup"] = "background_cleanup"
    assert actors.active_count() == 1
    actors.tasks.clear()
    assert actors.active_count() == 0
    runs = RunManager(str(tmp_path))
    runs._processes["run-a"] = "process-a"
    runs._monitors["run-b"] = SimpleNamespace(done=lambda: False)
    assert runs.active_count() == 2
    terminal = TerminalManager(str(tmp_path))
    terminal._items["term-a"] = SimpleNamespace(process=SimpleNamespace(poll=lambda: None))
    terminal._items["term-b"] = SimpleNamespace(process=SimpleNamespace(poll=lambda: 0))
    assert terminal.active_count() == 1


@pytest.mark.asyncio
async def test_activity_endpoint_keeps_auth_and_general_scope_boundaries(monkeypatch, tmp_path):
    from src.web.server import app
    from src.gateway.configuration_receipt import end_configuration_receipt
    end_configuration_receipt()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "test-activity-token")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/runtime-activity")).status_code in (401, 403)
        headers = {"Authorization": "Bearer test-activity-token"}
        response = await client.get("/api/runtime-activity", headers=headers)
        assert response.status_code == 200
        assert response.json()["ready"] is False
        assert response.json()["counts"] is None
        assert (await client.get("/api/runtime-activity/anything", headers=headers)).status_code == 409
        assert (await client.get("/api/runs", headers=headers)).status_code == 409
