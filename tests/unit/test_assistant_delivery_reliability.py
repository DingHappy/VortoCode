"""Exercise real persistent submission/recovery with controlled transport failures."""
import asyncio
import json

import pytest

from src.workflow.tasks import assistant_dispatch as dispatch
from src.workflow.tasks.submissions import submission_task_id, submit_once
from src.workflow.tasks.tasks import TaskRunner


@pytest.fixture
def root(tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"projects": [{"name": "demo", "repo_root": str(tmp_path / "project"), "port": 8082}]}))
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(registry))
    return str(tmp_path)


async def test_response_loss_retry_and_restart_do_not_execute_twice(root):
    calls = []
    release = asyncio.Event()

    async def worker(task, progress):
        calls.append(task.id)
        await release.wait()
        return "done"

    runner = TaskRunner(root + "/project", worker)
    entry = dispatch.propose(root, project="demo", title="fix", goal="repair")
    lose_response = True

    async def http(method, url, payload=None):
        nonlocal lose_response
        if method == "GET" and url.endswith("/api/tasks"):
            return {"submission_idempotency": 1, "submission_scope": {"repo_root": root + "/project"}}
        task = await submit_once(runner, payload["request_id"], payload["prompt"])
        if lose_response:
            lose_response = False
            raise TimeoutError("response lost after durable creation")
        return {"id": task.id, "status": task.status}

    try:
        with pytest.raises(dispatch.DispatchError, match="待核对"):
            await dispatch.approve(root, entry["id"], http=http)
        saved = dispatch.list_dispatches(root)[0]
        assert saved["status"] == "submission_unknown"
        assert saved["remote_task_id"] == submission_task_id(entry["request_id"])
        with pytest.raises(dispatch.DispatchError):
            dispatch.cancel(root, entry["id"])
        await dispatch.approve(root, entry["id"], http=http)
        await asyncio.sleep(0)
        assert calls == [saved["remote_task_id"]]
        # A fresh runner reads the same disk contract, never schedules a replay.
        restarted = TaskRunner(root + "/project", worker)
        assert (await submit_once(restarted, entry["request_id"], dispatch.task_prompt(entry))).id == saved["remote_task_id"]
        assert restarted.active_count == 0
        with pytest.raises(ValueError, match="不同的任务合同"):
            await submit_once(restarted, entry["request_id"], "different instruction")
    finally:
        release.set()
        await runner.shutdown()


async def test_parallel_approve_and_refresh_cannot_duplicate_or_regress(root):
    entry = dispatch.propose(root, project="demo", title="fix", goal="repair")
    entered, release = asyncio.Event(), asyncio.Event()
    posts = []

    async def http(method, url, payload=None):
        if method == "GET":
            return {"submission_idempotency": 1, "submission_scope": {"repo_root": root + "/project"}}
        posts.append(payload)
        entered.set()
        await release.wait()
        return {"id": submission_task_id(payload["request_id"]), "status": "done"}

    first = asyncio.create_task(dispatch.approve(root, entry["id"], http=http))
    await entered.wait()
    try:
        with pytest.raises(dispatch.DispatchError, match="未重复提交"):
            await dispatch.approve(root, entry["id"], http=http)
        # The in-flight send owns this task's refresh lease.
        assert (await dispatch.refresh(root, http=http))[0]["status"] == "sending"
    finally:
        release.set()
        await first
    assert len(posts) == 1
    assert dispatch.list_dispatches(root)[0]["status"] == "done"


async def test_legacy_server_is_checked_before_any_post(root):
    entry = dispatch.propose(root, project="demo", title="fix", goal="repair")
    methods = []

    async def legacy(method, url, payload=None):
        methods.append(method)
        return {"tasks": []}

    with pytest.raises(dispatch.DispatchError, match="不支持幂等"):
        await dispatch.approve(root, entry["id"], http=legacy)
    assert methods == ["GET"]
    assert dispatch.list_dispatches(root)[0]["status"] == "send_failed"


async def test_restart_reconciliation_only_reads_and_preserves_result(root):
    entry = dispatch.propose(root, project="demo", title="fix", goal="repair")
    dispatch._update(root, entry["id"], lambda item: item.update(status="sending",
                     remote_task_id=submission_task_id(item["request_id"])))
    methods = []

    async def read(method, url, payload=None):
        methods.append(method)
        return {"id": dispatch.submission_task_id(entry["request_id"]), "execution_scope": {"repo_root": root + "/project"}, "status": "done", "branch": "vorto/fix", "result": "tests passed"}

    item = (await dispatch.refresh(root, http=read))[0]
    assert set(methods) == {"GET"} and item["status"] == "done" and item["last_checked_at"]
    assert item["sync_state"] == "current" and item["branch"] == "vorto/fix"


def test_corrupt_ledger_never_becomes_an_empty_ledger(root):
    path = dispatch._ledger_path(root)
    path.parent.mkdir(parents=True)
    path.write_text("{broken")
    with pytest.raises(dispatch.DispatchError, match="保留原文件"):
        dispatch.propose(root, project="demo", title="fix", goal="repair")
    assert path.read_text() == "{broken"


def test_archive_keeps_old_active_entries_and_full_terminal_records(root, monkeypatch):
    monkeypatch.setattr(dispatch, "_MAX_ENTRIES", 2)
    active = dispatch.propose(root, project="demo", title="old active", goal="repair")
    for n in range(4):
        entry = dispatch.propose(root, project="demo", title=str(n), goal="repair")
        dispatch._update(root, entry["id"], lambda item: item.update(status="done", result="evidence"))
    assert active["id"] in {item["id"] for item in dispatch.list_dispatches(root)}
    archived = list((dispatch._ledger_path(root).parent / "archive").glob("*.json"))
    assert len(archived) == 2
    assert all(json.loads(path.read_text())["result"] == "evidence" for path in archived)


async def test_corrupt_remote_submission_does_not_schedule_a_replacement(root):
    async def worker(task, progress):
        raise AssertionError("must not execute")

    runner = TaskRunner(root, worker)
    path = runner.ledger._path(submission_task_id("key"))
    path.parent.mkdir(parents=True)
    path.write_text("{broken")
    with pytest.raises(OSError, match="未重新执行"):
        await submit_once(runner, "key", "repair")
    assert runner.active_count == 0 and path.read_text() == "{broken"


async def test_http_submission_contract_and_conflict(root, monkeypatch):
    import httpx
    from src.web.routers import tasks as routes
    from src.web.server import app

    monkeypatch.chdir(root)
    release = asyncio.Event()
    executions = []

    async def worker(task, progress):
        executions.append(task.id)
        await release.wait()
        return "done"

    runner = TaskRunner(root, worker)
    monkeypatch.setattr(routes, "_RUNNER", runner)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            capability = (await client.get("/api/tasks")).json()
            assert capability["submission_idempotency"] == 1
            assert capability["submission_scope"]["repo_root"] == root
            payload = {"request_id": "same-key", "prompt": "repair", "session": "owner", "expected_repo_root": root}
            first = await client.post("/api/tasks", json=payload)
            second = await client.post("/api/tasks", json=payload)
            assert first.status_code == second.status_code == 200
            assert first.json()["id"] == second.json()["id"] == submission_task_id("same-key")
            assert (await client.post("/api/tasks", json={**payload, "prompt": "another"})).status_code == 409
            assert (await client.post("/api/tasks", json={**payload, "request_id": "../../escape"})).status_code == 409
            assert (await client.post("/api/tasks", json={**payload, "request_id": "wrong-root", "expected_repo_root": root + "/other"})).status_code == 409
            detail = (await client.get(f"/api/tasks/{first.json()['id']}")).json()
            assert detail["execution_scope"]["repo_root"] == root
            assert runner.ledger.load(submission_task_id("wrong-root")) is None
            await asyncio.sleep(0)
            assert executions == [submission_task_id("same-key")]
    finally:
        release.set()
        await runner.shutdown()


def test_ledger_cross_process_lock_prevents_overlapping_writes(root):
    import subprocess
    import sys
    from src.workflow.pipeline.pipeline_storage import pipeline_lock

    code = """
import sys
from src.workflow.tasks.assistant_dispatch import propose, DispatchError
try:
    propose(sys.argv[1], project='demo', title='child', goal='repair')
except DispatchError:
    print('blocked')
else:
    raise SystemExit('unexpected writer')
"""
    with pipeline_lock(root, "assistant-ledger", kind="dispatch"):
        result = subprocess.run([sys.executable, "-c", code, root], text=True, capture_output=True, timeout=20)
    assert result.returncode == 0 and result.stdout.strip() == "blocked", result.stderr
    assert not dispatch._ledger_path(root).exists()


async def test_changed_registry_never_retargets_an_ambiguous_submission(root):
    import os
    entry = dispatch.propose(root, project="demo", title="fix", goal="repair")

    async def lost(method, url, payload=None):
        if method == "GET":
            return {"submission_idempotency": 1, "submission_scope": {"repo_root": root + "/project"}}
        raise TimeoutError("lost")

    with pytest.raises(dispatch.DispatchError):
        await dispatch.approve(root, entry["id"], http=lost)
    registry = json.loads(open(os.environ["VORTOCODE_RESIDENT_PROJECTS"]).read())
    registry["projects"][0]["port"] = 8083
    from pathlib import Path
    Path(os.environ["VORTOCODE_RESIDENT_PROJECTS"]).write_text(json.dumps(registry))
    calls = []

    async def forbidden(*args):
        calls.append(args)
        raise AssertionError("changed target must never be contacted")

    with pytest.raises(dispatch.DispatchError, match="登记已变化"):
        await dispatch.approve(root, entry["id"], http=forbidden)
    await dispatch.refresh(root, http=forbidden)
    assert calls == []
    assert dispatch.list_dispatches(root)[0]["sync_state"] == "unreachable"


async def test_routine_refresh_is_bounded_and_rotates_even_when_offline(root):
    for n in range(25):
        entry = dispatch.propose(root, project="demo", title=str(n), goal="repair")
        dispatch._update(root, entry["id"], lambda item: item.update(status="running", remote_task_id=item["id"]))
    calls = []

    async def offline(method, url, payload=None):
        calls.append(url)
        raise OSError("offline")

    await dispatch.refresh(root, http=offline)
    assert len(calls) == 20
    first = set(calls)
    calls.clear()
    await dispatch.refresh(root, http=offline)
    assert len(calls) == 20 and len(first | set(calls)) == 25


@pytest.mark.parametrize("scope", [None, {"repo_root": "/wrong/project"}])
async def test_runtime_directory_is_verified_before_any_post(root, scope):
    entry = dispatch.propose(root, project="demo", title="fix", goal="repair")
    methods = []

    async def wrong_runtime(method, url, payload=None):
        methods.append(method)
        return {"submission_idempotency": 1, "submission_scope": scope}

    with pytest.raises(dispatch.DispatchError, match="目录未确认或与登记不匹配"):
        await dispatch.approve(root, entry["id"], http=wrong_runtime)
    assert methods == ["GET"]
    saved = dispatch.list_dispatches(root)[0]
    assert saved["status"] == "send_failed" and saved["submission_attempted"] is False


async def test_wrong_runtime_or_task_does_not_replace_last_known_result(root):
    entry = dispatch.propose(root, project="demo", title="fix", goal="repair")
    dispatch._update(root, entry["id"], lambda item: item.update(status="running", remote_task_id="task-original", result="original"))
    for response in [
        {"id": "task-original", "status": "done", "execution_scope": {"repo_root": "/wrong/project"}},
        {"id": "task-other", "status": "done", "execution_scope": {"repo_root": root + "/project"}},
    ]:
        async def http(method, url, payload=None):
            return response
        item = (await dispatch.refresh(root, http=http))[0]
        assert item["status"] == "running" and item["result"] == "original"
        assert item["sync_state"] == "unreachable" and "不匹配" in item["sync_error"]


async def test_restart_before_submission_enqueue_marks_interrupted_without_replay(root, monkeypatch):
    calls = []
    async def worker(task, progress):
        calls.append(task.id)
        return 'done'

    runner = TaskRunner(root + '/project', worker)
    def crash_before_enqueue(_):
        raise RuntimeError('crash after durable create')
    monkeypatch.setattr(runner, 'enqueue_worker', crash_before_enqueue)
    entry = dispatch.propose(root, project='demo', title='fix', goal='repair')
    with pytest.raises(RuntimeError, match='durable'):
        await submit_once(runner, entry['request_id'], dispatch.task_prompt(entry))
    tid = submission_task_id(entry['request_id'])
    assert runner.get(tid).status == 'queued'
    restarted = TaskRunner(root + '/project', worker)
    recovered = restarted.recover()
    assert [task.id for task in recovered] == [tid]
    assert restarted.get(tid).status == 'interrupted'
    assert '未自动重放' in restarted.get(tid).error
    assert (await submit_once(restarted, entry['request_id'], dispatch.task_prompt(entry))).status == 'interrupted'
    await asyncio.sleep(0)
    assert calls == [] and restarted.active_count == 0
    from src.workflow.tasks.task_recovery import RecoveryConflict, TaskRecovery
    with pytest.raises(RecoveryConflict, match='持久 plan_id'):
        TaskRecovery(restarted, read_plan=lambda _: None).resume(tid)
    assert len(restarted.ledger.list()) == 1
    # The assistant reconciles the original task; no POST or replacement task.
    dispatch._update(root, entry['id'], lambda item: item.update(status='sending', remote_task_id=tid))
    methods = []
    async def read(method, url, payload=None):
        methods.append(method)
        task = restarted.get(tid)
        return {'id': task.id, 'status': task.status, 'execution_scope': {'repo_root': root + '/project'}}
    saved = (await dispatch.refresh(root, http=read))[0]
    assert saved['status'] == 'stopped' and saved['sync_state'] == 'current'
    assert methods == ['GET'] and saved['remote_task_id'] == tid


def test_recovery_does_not_reclassify_other_queued_or_terminal_tasks(root):
    async def worker(task, progress):
        raise AssertionError('must not execute')
    runner = TaskRunner(root, worker)
    queued = runner.ledger.create('dev', 'legacy queued')
    prefix_only = runner.ledger.create('dev', 'unreserved', tid='task-submit-not-a-digest')
    other_kind = runner.ledger.create('research', 'other', tid=submission_task_id('other-kind'))
    terminal = runner.ledger.create('dev', 'done', tid=submission_task_id('terminal'))
    terminal.status = 'done'
    assert runner.ledger.save(terminal)
    assert runner.recover() == []
    assert all(runner.get(task.id).status == 'queued' for task in (queued, prefix_only, other_kind))
    assert runner.get(terminal.id).status == 'done'


def test_interrupted_submission_recovery_requires_durable_save(root, monkeypatch):
    async def worker(task, progress):
        raise AssertionError('must not execute')
    runner = TaskRunner(root, worker)
    task = runner.ledger.create('dev', 'repair', tid=submission_task_id('save-fails'))
    monkeypatch.setattr(runner.ledger, 'save', lambda _: False)
    with pytest.raises(OSError, match='中断状态未能持久化'):
        runner.recover()
    assert runner.get(task.id).status == 'queued'
    assert runner.active_count == 0
