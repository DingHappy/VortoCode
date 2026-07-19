"""Desktop command/test/preview run contracts."""
import asyncio

import pytest

from src.gateway.runs import (
    RunLedger,
    RunManager,
    detect_preview_url,
    normalize_preview_url,
    parse_test_results,
)
from src.gateway.goals import GoalLedger


def test_run_ledger_persists_structured_result_and_recovers(tmp_path):
    ledger = RunLedger(str(tmp_path))
    run = ledger.create("pytest -q", "test")
    run.status = "running"
    run.output = "collecting"
    ledger.save(run)

    loaded = ledger.load(run.id)
    assert loaded is not None and loaded.command == "pytest -q"
    recovered = ledger.recover_interrupted()
    assert [item.id for item in recovered] == [run.id]
    assert ledger.load(run.id).status == "interrupted"
    assert ledger.load("../../bad") is None


def test_preview_urls_are_local_only_and_can_be_detected():
    assert normalize_preview_url("http://localhost:5173") == "http://localhost:5173/"
    assert detect_preview_url("ready\nLocal: http://127.0.0.1:1420/app") == (
        "http://127.0.0.1:1420/app"
    )
    with pytest.raises(ValueError, match="只允许本机"):
        normalize_preview_url("https://example.com")
    with pytest.raises(ValueError):
        normalize_preview_url("http://localhost:99999")


def test_test_result_parser_keeps_summary_and_named_cases():
    pytest_result = parse_test_results(
        "pytest -q -rA",
        "PASSED tests/test_api.py::test_health\n"
        "FAILED tests/test_api.py::test_auth - assert 401 == 200\n"
        "1 failed, 1 passed, 2 skipped in 0.12s",
    )
    assert pytest_result["framework"] == "pytest"
    assert pytest_result["summary"] == {
        "passed": 1, "failed": 1, "skipped": 2, "errors": 0, "total": 4,
    }
    assert [case["status"] for case in pytest_result["cases"]] == ["passed", "failed"]
    assert pytest_result["complete"] is False

    rust_result = parse_test_results(
        "cargo test",
        "test api::health ... ok\ntest api::auth ... FAILED\n"
        "test result: FAILED. 1 passed; 1 failed; 0 ignored",
    )
    assert rust_result["framework"] == "rust"
    assert rust_result["summary"]["total"] == 2
    assert rust_result["complete"] is True


@pytest.mark.asyncio
async def test_run_manager_streams_exit_code_and_test_result(tmp_path, monkeypatch):
    import src.agents.shell as shell

    reads = iter([
        {"ok": True, "status": "running", "code": None, "output": "1 passed", "dropped": 0},
        {"ok": True, "status": "exited", "code": 0, "output": "done", "dropped": 0},
    ])
    monkeypatch.setattr(shell, "run_command_background", lambda *_a, **_k: {
        "ok": True, "id": "bg-test", "pid": 42, "sandbox": {"isolated": True}, "warning": "",
    })
    monkeypatch.setattr(shell, "read_background", lambda _bid: next(reads))

    manager = RunManager(str(tmp_path), poll_interval=0.01)
    run = await manager.submit("pytest -q", kind="test")
    await manager.wait(run.id)
    finished = manager.get(run.id)
    assert finished is not None
    assert finished.status == "done" and finished.code == 0
    assert "1 passed" in finished.output and "done" in finished.output
    assert finished.to_dict()["passed"] is True


@pytest.mark.asyncio
async def test_linked_verifier_run_records_goal_evidence(tmp_path, monkeypatch):
    import src.agents.shell as shell

    ledger = GoalLedger(str(tmp_path))
    goal = ledger.create("通过自动验收", ["Lint 通过"])
    ledger.set_verifier(goal.id, "criterion-1", {
        "kind": "lint", "command": "ruff check src", "timeout": 30,
    })
    goal = ledger.load(goal.id)
    assert goal is not None
    goal.status = "active"
    ledger.save(goal)

    isolation = []
    reads = iter([
        {"ok": True, "status": "exited", "code": 0, "output": "All checks passed", "dropped": 0},
    ])
    monkeypatch.setattr(shell, "run_command_background", lambda *_a, **kwargs: (
        isolation.append(kwargs.get("require_isolation")) or {
            "ok": True, "id": "bg-verifier", "pid": 44,
            "sandbox": {"isolated": True}, "warning": "",
        }
    ))
    monkeypatch.setattr(shell, "read_background", lambda _bid: next(reads))

    manager = RunManager(str(tmp_path), poll_interval=0.01)
    run = await manager.submit(
        "ruff check src",
        kind="test",
        goal_id=goal.id,
        criterion_id="criterion-1",
        evidence_kind="lint",
        require_isolation=True,
        timeout_seconds=30,
    )
    await manager.wait(run.id)

    achieved = ledger.load(goal.id)
    assert isolation == [True]
    assert achieved is not None and achieved.status == "achieved"
    assert achieved.acceptance_criteria[0].status == "passed"
    assert achieved.evidence[-1].source == f"run:{run.id}"
    assert achieved.evidence[-1].kind == "lint"


@pytest.mark.asyncio
async def test_run_manager_stops_the_process_group(tmp_path, monkeypatch):
    import src.agents.shell as shell

    monkeypatch.setattr(shell, "run_command_background", lambda *_a, **_k: {
        "ok": True, "id": "bg-long", "pid": 43, "sandbox": {}, "warning": "",
    })
    monkeypatch.setattr(shell, "read_background", lambda _bid: {
        "ok": True, "status": "running", "code": None, "output": "server ready", "dropped": 0,
    })
    stopped = []
    monkeypatch.setattr(shell, "stop_background", lambda bid: (
        stopped.append(bid) or {"ok": True, "stopped": True, "code": -15}
    ))

    manager = RunManager(str(tmp_path), poll_interval=0.01)
    run = await manager.submit("npm run dev", kind="preview", preview_url="http://localhost:5173")
    await asyncio.sleep(0.02)
    cancelled = await manager.cancel(run.id)
    assert stopped == ["bg-long"]
    assert cancelled is not None and cancelled.status == "cancelled"


@pytest.mark.asyncio
async def test_run_manager_rejects_dangerous_command_before_spawn(tmp_path, monkeypatch):
    import src.agents.shell as shell

    spawned = []
    monkeypatch.setattr(shell, "run_command_background", lambda *_a, **_k: spawned.append(True))
    manager = RunManager(str(tmp_path))
    with pytest.raises(ValueError, match="危险"):
        await manager.submit("rm -rf /")
    assert spawned == []


def test_run_rest_validation_and_listing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_ENABLE_SHELL", "1")  # 过执行闸，专测校验/列举逻辑
    from fastapi.testclient import TestClient
    from src.web.routers import runs as runs_router
    from src.web.server import app

    manager = RunManager(str(tmp_path), poll_interval=0.01)
    monkeypatch.setattr(runs_router, "_RUN_MANAGER", manager)
    client = TestClient(app)

    assert client.post("/api/runs", json={}).status_code == 400
    assert client.post("/api/runs", json={"command": "echo ok", "kind": "unknown"}).status_code == 400
    record = manager.ledger.create("pytest -q", "test")
    listing = client.get("/api/runs").json()["runs"]
    assert listing[0]["id"] == record.id
    assert client.get(f"/api/runs/{record.id}").status_code == 200
    assert client.get("/api/runs/run-nope").status_code == 404
