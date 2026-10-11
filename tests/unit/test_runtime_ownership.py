"""Offline ownership and scope regressions for both Runtime hosts."""
import asyncio
from types import SimpleNamespace

import pytest

from src.gateway.process_lease import ProcessLease
from src.gateway.runtime import runtime_lifespan
from src.im.bridge import IMBridge
from src.im.telegram import TelegramAdapter


class Adapter:
    edits_supported = True
    def __init__(self):
        self.sent = []
    async def send_text(self, text):
        self.sent.append(text)


@pytest.mark.parametrize("scope", ["general", "scratch"])
@pytest.mark.asyncio
async def test_im_inherits_scope_and_rejects_direct_project_commands(tmp_path, monkeypatch, scope):
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", scope)
    adapter = Adapter()
    bridge = IMBridge(tmp_path, adapter, "42")
    assert bridge.workspace_scope == scope
    assert not bridge._with_dev
    assert "dev_auto" not in bridge.agent.tools
    if scope == "general":
        assert "read_file" not in bridge.agent.tools
        assert "write_file" not in bridge.agent.tools
        assert "shell" not in bridge.agent.tools
    def forbidden(*args, **kwargs):
        pytest.fail("Project command bypassed workspace scope")
    monkeypatch.setattr(bridge, "_get_runner", forbidden)
    monkeypatch.setattr(bridge, "_pipe_list", forbidden)
    monkeypatch.setattr(bridge, "_pipeline_status_line", forbidden)
    monkeypatch.setattr(bridge, "_recent_plan", forbidden)
    for command in ("/task change code", "/tasks", "/pipe", "/批", "/go", "/url example.com"):
        await bridge._handle_command(command)
        assert "Project" in adapter.sent[-1]
    await bridge._submit_task("change code")
    assert "Project" in adapter.sent[-1]
    await bridge._handle_command("/status")


def test_workspace_lease_excludes_a_second_owner_and_releases(tmp_path):
    path = tmp_path / "runtime.lock"
    first, second = ProcessLease(path), ProcessLease(path)
    with first:
        with pytest.raises(RuntimeError, match="Another Runtime"):
            second.acquire()
    with second:
        with pytest.raises(RuntimeError):
            first.acquire()
    with first:
        pass
    assert path.exists()  # unlinking would allow concurrent owners on different inodes


@pytest.mark.asyncio
async def test_bot_ownership_is_shared_across_token_rotations(tmp_path):
    first = TelegramAdapter("123:old-secret", "42")
    second = TelegramAdapter("123:new-secret", "42")
    first.claim_polling()
    with pytest.raises(RuntimeError, match="Another Runtime"):
        second.claim_polling()
    await first.close()
    second.claim_polling()
    await second.close()


@pytest.mark.asyncio
async def test_startup_failure_releases_workspace_before_retry(tmp_path, monkeypatch):
    monkeypatch.delenv("VORTOCODE_IM", raising=False)
    monkeypatch.delenv("VORTOCODE_SUPERVISOR_PID", raising=False)
    monkeypatch.delenv("VORTOCODE_CRON", raising=False)
    monkeypatch.delenv("VORTOCODE_HEARTBEAT", raising=False)
    calls = []
    async def shutdown():
        calls.append("shutdown")
    def recover():
        calls.append("recover")
        raise ValueError("broken ledger")
    runner = SimpleNamespace(recover=recover, shutdown=shutdown)
    kwargs = dict(runner=runner, scheduler=None, audit_dependencies=lambda: {},
                  make_run_manager=lambda: None, make_terminal_manager=lambda: None,
                  register_im_worker=lambda worker: None)
    with pytest.raises(ValueError, match="broken ledger"):
        async with runtime_lifespan(tmp_path, **kwargs):
            pytest.fail("Recovery failure must stop startup")
    assert calls == ["recover", "shutdown"]
    with ProcessLease(tmp_path / ".vortocode" / "runtime.lock"):
        with pytest.raises(RuntimeError, match="Another Runtime"):
            async with runtime_lifespan(tmp_path, **kwargs):
                pytest.fail("Second owner must not start")
    assert calls == ["recover", "shutdown"]


@pytest.mark.asyncio
async def test_runner_shutdown_drains_worker_before_releasing_ownership(tmp_path):
    from src.gateway.tasks import TaskRunner
    started, stopped = asyncio.Event(), asyncio.Event()
    async def worker(task, progress):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
    runner = TaskRunner(tmp_path, worker)
    task = await runner.submit("wait")
    await started.wait()
    await runner.shutdown()
    assert stopped.is_set()
    assert runner.ledger.load(task.id).status == "cancelled"


def test_workspace_lock_is_enforced_across_processes(tmp_path):
    import subprocess
    import sys
    path = tmp_path / "runtime.lock"
    script = """from src.gateway.process_lease import ProcessLease
import sys
try:
    ProcessLease(sys.argv[1]).acquire()
except RuntimeError:
    sys.exit(7)
"""
    with ProcessLease(path):
        result = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True)
        assert result.returncode == 7, result.stderr.decode()
    result = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


@pytest.mark.asyncio
async def test_failed_im_startup_cleans_managers_and_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_IM", "telegram")
    for key in ("VORTOCODE_SUPERVISOR_PID", "VORTOCODE_CRON", "VORTOCODE_HEARTBEAT",
                "VORTOCODE_TG_TOKEN", "VORTOCODE_TG_OWNER_ID"):
        monkeypatch.delenv(key, raising=False)
    calls = []
    async def shutdown():
        calls.append("runner stopped")
    async def stop_runs():
        calls.append("runs stopped")
    runner = SimpleNamespace(recover=lambda: [], shutdown=shutdown)
    runs = SimpleNamespace(recover=lambda: [], shutdown=stop_runs)
    terminals = SimpleNamespace(shutdown=lambda: calls.append("terminals stopped"))
    from src.gateway.im_service import IMConfigError
    with pytest.raises(IMConfigError):
        async with runtime_lifespan(
                tmp_path, runner=runner, scheduler=None, audit_dependencies=lambda: {},
                make_run_manager=lambda: runs, make_terminal_manager=lambda: terminals,
                register_im_worker=lambda worker: None):
            pytest.fail("Invalid explicit IM configuration must prevent startup")
    assert calls == ["runner stopped", "runs stopped", "terminals stopped"]
    with ProcessLease(tmp_path / ".vortocode" / "runtime.lock"):
        pass


@pytest.mark.asyncio
async def test_dingtalk_application_has_one_owner_across_workspaces():
    from src.im.dingtalk import DingTalkAdapter
    first = DingTalkAdapter("one-app", "old-secret", "42")
    second = DingTalkAdapter("one-app", "new-secret", "42")
    other = DingTalkAdapter("another-app", "secret", "42")
    try:
        first.claim_polling()
        other.claim_polling()
        with pytest.raises(RuntimeError, match="Another Runtime"):
            second.claim_polling()
        await first.close()
        second.claim_polling()
    finally:
        await first.close()
        await second.close()
        await other.close()


@pytest.mark.asyncio
async def test_stopped_runner_cannot_accept_or_schedule_more_work(tmp_path):
    from src.gateway.tasks import TaskRunner
    async def worker(task, progress):
        pytest.fail("A stopped Runtime must not execute work")
    runner = TaskRunner(tmp_path, worker)
    queued = runner.ledger.create("dev", "already queued")
    await runner.shutdown()
    with pytest.raises(RuntimeError, match="shutting down"):
        await runner.submit("new task")
    with pytest.raises(RuntimeError, match="shutting down"):
        runner.enqueue_worker(queued.id)
    with pytest.raises(RuntimeError, match="shutting down"):
        runner.enqueue_existing(queued.id, lambda: None, on_cancel=lambda: None,
                                on_failure=lambda error: None)
    assert len(runner.list()) == 1
    assert runner.get(queued.id).status == "queued"


@pytest.mark.asyncio
async def test_cleanup_failure_still_closes_other_managers_and_releases_lock(tmp_path, monkeypatch):
    for key in ("VORTOCODE_IM", "VORTOCODE_SUPERVISOR_PID", "VORTOCODE_CRON", "VORTOCODE_HEARTBEAT"):
        monkeypatch.delenv(key, raising=False)
    calls = []
    async def shutdown():
        calls.append("runner stopped")
    async def stop_runs():
        raise ValueError("run shutdown failed")
    runner = SimpleNamespace(recover=lambda: [], shutdown=shutdown)
    runs = SimpleNamespace(recover=lambda: [], shutdown=stop_runs)
    terminals = SimpleNamespace(shutdown=lambda: calls.append("terminals stopped"))
    with pytest.raises(ValueError, match="run shutdown failed"):
        async with runtime_lifespan(
                tmp_path, runner=runner, scheduler=None, audit_dependencies=lambda: {},
                make_run_manager=lambda: runs, make_terminal_manager=lambda: terminals,
                register_im_worker=lambda worker: None):
            pass
    assert calls == ["runner stopped", "terminals stopped"]
    with ProcessLease(tmp_path / ".vortocode" / "runtime.lock"):
        pass


def test_desktop_pinned_switches_survive_dotenv_loading(tmp_path, monkeypatch):
    pytest.importorskip("dotenv")
    import src.cli as cli
    (tmp_path / ".env").write_text(
        "VORTOCODE_IM=dingtalk\nVORTOCODE_CRON=1\nVORTOCODE_HEARTBEAT=1\n")
    monkeypatch.setattr(cli, "__file__", str(tmp_path / "src" / "cli.py"))
    pinned = {"VORTOCODE_IM": "", "VORTOCODE_CRON": "0", "VORTOCODE_HEARTBEAT": "0"}
    for key, value in pinned.items():
        monkeypatch.setenv(key, value)
    cli._setup_logging()
    import os
    assert {key: os.environ[key] for key in pinned} == pinned
