"""Durable dev resumption, persistence failures and cancellation boundaries."""
import asyncio
import json

import httpx
import pytest

from src.agents import dev_plan
from src.gateway.handoffs import CompletionInbox
from src.gateway.task_recovery import RecoveryConflict, TaskRecovery, recovery_task_id, validate_recovery_plan
from src.gateway.tasks import TaskLedger, TaskRunner


def stopped(root, *, worker=None, kind="dev"):
    plan = dev_plan.DevPlan.new("交付配置", "vorto/recovery", "main", plan_id="recovery-plan")
    plan.blocks = [dev_plan.Block("done", "independent", "已有实现", status="landed"),
                   dev_plan.Block("todo", "dependent", "补充测试", deps=["done"])]
    assert dev_plan.save_plan(str(root), plan)
    calls, updates = [], []

    async def execute(task, progress):
        calls.append(task.id)
        validate_recovery_plan(task, dev_plan.load_plan(str(root), task.plan_id))
        return "恢复交付完成"

    runner = TaskRunner(str(root), worker or execute, max_concurrent=1,
                        on_update=lambda task: updates.append((task.id, task.status)))
    source = runner.ledger.create(kind, plan.task, plan_id=plan.plan_id,
                                  owner_session="sid-owner", goal_id="goal-owned")
    source.status, source.branch = "paused", plan.branch
    assert runner.ledger.save(source)
    service = TaskRecovery(runner, read_plan=lambda pid: dev_plan.load_plan(str(root), pid))
    return runner, service, source, plan, calls, updates


@pytest.mark.asyncio
async def test_resume_replay_before_and_after_execution_keeps_identity_and_handoff(tmp_path):
    from src.gateway.worktree_sessions import task_session_view
    runner, recovery, source, plan, calls, updates = stopped(tmp_path)
    resumed, replayed = recovery.resume(source.id)
    assert not replayed and resumed.id == recovery_task_id(source.id)
    assert resumed.parent_task_id == source.id and resumed.plan_id == plan.plan_id
    assert resumed.owner_session == source.owner_session and resumed.goal_id == source.goal_id
    assert recovery.resume(source.id)[1] and runner.active_count == 1
    assert task_session_view(str(tmp_path), source, worktrees=[])["can_resume"] is False
    assert task_session_view(str(tmp_path), source, worktrees=[])["resumed_task_id"] == resumed.id
    await runner.join(resumed.id)
    current, replayed = recovery.resume(source.id)
    assert replayed and current.status == "done" and calls == [resumed.id]
    assert [status for _, status in updates] == ["queued", "running", "done"]
    inbox = CompletionInbox(str(tmp_path), "sid-owner")
    item = next(item for item in inbox.pending() if item["task_id"] == resumed.id)
    assert item["kind"] == "dev-resume" and item["status"] == "done"
    inbox.acknowledge(resumed.id, item["revision"], "核对恢复交付")
    assert all(item["task_id"] != resumed.id for item in inbox.pending())


@pytest.mark.asyncio
async def test_recovery_lineage_resolves_old_attention_but_preserves_unmatched_failures(tmp_path):
    from src.gateway.dashboard import background_tasks_by_session
    from src.gateway.decisions import build_decision_queue
    from src.gateway.runtime_inbox import build_runtime_inbox
    runner, recovery, source, _, _, _ = stopped(tmp_path)
    child, _ = recovery.resume(source.id)
    assert build_decision_queue(tasks=runner.list()) == []
    inbox = build_runtime_inbox(scope="project", tasks=runner.list())
    assert inbox["counts"]["tasks_attention"] == 0 and inbox["counts"]["tasks_active"] == 1
    assert background_tasks_by_session(str(tmp_path))["owner"]["attention"] == 0
    await runner.join(child.id)
    child = runner.get(child.id)
    child.status, child.owner_session = "failed", "sid-other"
    runner.ledger.save(child)
    assert source.id in {item["target_id"] for item in build_decision_queue(tasks=runner.list())}
    assert background_tasks_by_session(str(tmp_path))["owner"]["attention"] == 1


@pytest.mark.asyncio
async def test_failed_resumption_requires_action_on_latest_task_not_old_source(tmp_path):
    calls = []

    async def fail(task, progress):
        calls.append(task.id)
        raise ValueError("测试失败")

    runner, recovery, source, _, _, _ = stopped(tmp_path, worker=fail)
    first, _ = recovery.resume(source.id)
    await runner.join(first.id)
    assert recovery.resume(source.id)[0].status == "failed" and calls == [first.id]
    second, repeated = recovery.resume(first.id)
    assert not repeated and second.id != first.id and second.parent_task_id == first.id
    await runner.join(second.id)
    assert calls == [first.id, second.id]


@pytest.mark.asyncio
async def test_crash_after_saved_resumption_never_requeues_on_retry(tmp_path, monkeypatch):
    runner, recovery, source, _, calls, _ = stopped(tmp_path)
    monkeypatch.setattr(runner, "enqueue_worker", lambda _: None)
    child, _ = recovery.resume(source.id)
    fresh = TaskRunner(str(tmp_path), runner._worker)
    restored = TaskRecovery(fresh, read_plan=recovery.read_plan)
    assert [task.id for task in fresh.recover()] == [child.id]
    replay, repeated = restored.resume(source.id)
    assert repeated and replay.status == "interrupted"
    await asyncio.sleep(0)
    assert calls == [] and fresh.active_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["kind", "running", "no_plan", "missing_plan", "done_plan", "branch", "capacity", "active", "save"])
async def test_invalid_or_unpersisted_resumption_never_starts(tmp_path, monkeypatch, boundary):
    runner, recovery, source, plan, calls, updates = stopped(tmp_path)
    if boundary == "kind":
        source.kind = "delegation"
    elif boundary == "running":
        source.status = "running"
    elif boundary == "no_plan":
        source.plan_id = ""
    elif boundary == "missing_plan":
        source.plan_id = "missing-plan"
    elif boundary == "done_plan":
        plan.status = "done"
        dev_plan.save_plan(str(tmp_path), plan)
    elif boundary == "branch":
        plan.branch = "main"
        dev_plan.save_plan(str(tmp_path), plan)
    elif boundary == "capacity":
        monkeypatch.setattr(TaskRunner, "active_count", property(lambda _: 100))
    elif boundary == "active":
        monkeypatch.setattr(runner, "is_active", lambda _: True)
    runner.ledger.save(source)
    if boundary == "save":
        monkeypatch.setattr(TaskLedger, "save", lambda *args: False)
    with pytest.raises((RecoveryConflict, OSError)):
        recovery.resume(source.id)
    assert not runner.ledger.has_record(recovery_task_id(source.id))
    await asyncio.sleep(0)
    assert calls == [] and updates == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner", "source_prompt", "child_identity", "corrupt_file"])
async def test_corrupt_or_mismatched_recovery_records_do_not_grant_another_run(tmp_path, change):
    runner, recovery, source, _, calls, _ = stopped(tmp_path)
    child, _ = recovery.resume(source.id)
    await runner.join(child.id)
    path = tmp_path / ".vortocode/tasks" / f"{child.id}.json"
    if change == "owner":
        child.owner_session = "sid-other"
        runner.ledger.save(child)
    elif change == "source_prompt":
        source.prompt = "变更任务范围"
        runner.ledger.save(source)
    else:
        data = json.loads(path.read_text())
        data["id"] = "task-other"
        path.write_text("broken" if change == "corrupt_file" else json.dumps(data))
    before = path.read_bytes()
    with pytest.raises(RecoveryConflict):
        recovery.resume(source.id)
    assert path.read_bytes() == before and calls == [child.id]


@pytest.mark.asyncio
async def test_plan_edited_while_queued_is_rejected_before_dev_tool_runs(tmp_path, monkeypatch):
    from src.agents.tool import Tool
    from src.agents import main_agent
    from src.web.routers import tasks
    calls = []

    async def resume(args):
        calls.append(args)
        current = dev_plan.load_plan(str(tmp_path), args["plan_id"])
        for block in current.blocks:
            block.status = "landed"
        current.status = "integrated"
        dev_plan.save_plan(str(tmp_path), current)
        return "unexpected"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_agent, "build_dev_tools", lambda *args, **kwargs: [Tool("dev_resume", "resume", {}, resume)])
    runner, recovery, source, plan, _, _ = stopped(tmp_path, worker=tasks._dev_worker)
    child, _ = recovery.resume(source.id)
    plan.blocks[-1].desc = "新范围必须重新审阅"
    dev_plan.save_plan(str(tmp_path), plan)
    await runner.join(child.id)
    assert runner.get(child.id).status == "failed" and "排队后已变化" in runner.get(child.id).error
    assert calls == []
    # Retry returns the failed record. Explicit recovery from it pins the new plan.
    assert recovery.resume(source.id)[0].id == child.id
    second, _ = recovery.resume(child.id)
    await runner.join(second.id)
    assert len(calls) == 1 and runner.get(second.id).status == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("result_state", ["no_plan", "integration_failed", "pending_block", "review_blocked"])
async def test_dev_worker_uses_plan_evidence_and_keeps_failed_result(tmp_path, monkeypatch, result_state):
    from src.agents import main_agent
    from src.agents.tool import Tool
    from src.web.routers import tasks

    async def execute(args):
        if result_state != "no_plan":
            plan = dev_plan.DevPlan.new("实现配置", "vorto/result", "main", plan_id=args["plan_id"])
            plan.status = "integration_failed" if result_state == "integration_failed" else "integrated"
            plan.blocks = [dev_plan.Block("b1", "independent", "实现", status="pending" if result_state == "pending_block" else "landed")]
            if result_state == "review_blocked":
                plan.review = {"blocked": True, "note": "审查有缺项"}
            dev_plan.save_plan(str(tmp_path), plan)
        return "测试/实现失败的具体证据，不能当作成功"

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main_agent, "build_dev_tools", lambda *args, **kwargs: [Tool("dev_auto", "dev", {}, execute)])
    runner = TaskRunner(str(tmp_path), tasks._dev_worker)
    task = await runner.submit("实现配置")
    await runner.join(task.id)
    failed = runner.get(task.id)
    assert failed.status == "failed" and "具体证据" in failed.result and "具体证据" in failed.error


@pytest.mark.asyncio
async def test_http_resume_replays_finished_child_and_reports_save_failure(tmp_path, monkeypatch):
    from src.web.routers import tasks
    from src.web.server import app
    from src.web import task_events
    runner, _, source, _, calls, _ = stopped(tmp_path)
    source_updates = []
    monkeypatch.setattr(task_events, "broadcast_task_update", source_updates.append)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tasks, "_RUNNER", runner)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "recovery-test")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        endpoint = f"/api/tasks/{source.id}/resume"
        assert (await client.post(endpoint)).status_code == 401
        headers = {"Authorization": "Bearer recovery-test"}
        with monkeypatch.context() as patch:
            patch.setattr(TaskLedger, "save", lambda *args: False)
            assert (await client.post(endpoint, headers=headers)).status_code == 503
            assert (await client.post("/api/tasks", json={"prompt": "失败不执行"}, headers=headers)).status_code == 503
        first = await client.post(endpoint, headers=headers)
        assert first.status_code == 200 and not first.json()["replayed"]
        await runner.join(first.json()["id"])
        repeat = await client.post(endpoint, headers=headers)
        assert repeat.json()["replayed"] and repeat.json()["status"] == "done"
        assert calls == [first.json()["id"]]
        assert len(source_updates) == 1 and source_updates[0]["id"] == source.id
        assert source_updates[0]["can_resume"] is False and source_updates[0]["resumed_task_id"] == first.json()["id"]


@pytest.mark.asyncio
async def test_submit_save_failure_never_notifies_or_executes(tmp_path, monkeypatch):
    calls, updates = [], []

    async def worker(task, progress):
        calls.append(task.id)
        return "done"

    runner = TaskRunner(str(tmp_path), worker, on_update=updates.append)
    monkeypatch.setattr(TaskLedger, "save", lambda *args: False)
    with pytest.raises(OSError):
        await runner.submit("未持久提交")
    assert runner.active_count == 0 and calls == updates == []


@pytest.mark.asyncio
async def test_running_save_failure_never_invokes_worker(tmp_path, monkeypatch):
    calls, updates = [], []

    async def worker(task, progress):
        calls.append(task.id)
        return "done"

    runner = TaskRunner(str(tmp_path), worker, on_update=lambda task: updates.append(task.status))
    task = await runner.submit("未持久运行")
    save = TaskLedger.save
    monkeypatch.setattr(TaskLedger, "save", lambda self, task: False if task.status == "running" else save(self, task))
    await runner.join(task.id)
    assert calls == [] and runner.get(task.id).status == "failed"
    assert updates == ["queued", "failed"]


@pytest.mark.asyncio
async def test_terminal_save_failure_never_emits_success_and_can_recover(tmp_path, monkeypatch):
    updates = []

    async def worker(task, progress):
        return "真实执行结果，但终态未落盘"

    runner = TaskRunner(str(tmp_path), worker, on_update=lambda task: updates.append(task.status))
    task = await runner.submit("结果落盘失败")
    save = TaskLedger.save
    with monkeypatch.context() as patch:
        patch.setattr(TaskLedger, "save", lambda self, task: False if task.status == "done" else save(self, task))
        with pytest.raises(OSError):
            await runner.join(task.id)
    assert runner.get(task.id).status == "running" and updates == ["queued", "running"]
    runner.recover()
    assert runner.get(task.id).status == "interrupted" and updates[-1] == "interrupted"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "pause"])
async def test_stop_before_first_timeslice_is_durable(tmp_path, action):
    calls, updates = [], []

    async def worker(task, progress):
        calls.append(task.id)
        return "unexpected"

    runner = TaskRunner(str(tmp_path), worker, on_update=lambda task: updates.append(task.status))
    task = await runner.submit("首时隙前停止")
    if action == "pause":
        assert (await runner.pause(task.id)).status == "paused"
    else:
        assert runner.cancel(task.id)
        await runner.join(task.id)
    await asyncio.sleep(0)
    assert runner.get(task.id).status == ("paused" if action == "pause" else "cancelled")
    assert calls == [] and updates == ["queued", runner.get(task.id).status]


@pytest.mark.asyncio
async def test_pause_save_failure_is_not_reported_as_paused(tmp_path, monkeypatch):
    started = asyncio.Event()
    updates = []

    async def worker(task, progress):
        started.set()
        await asyncio.Event().wait()

    runner = TaskRunner(str(tmp_path), worker, on_update=lambda task: updates.append(task.status))
    task = await runner.submit("暂停落盘失败")
    await started.wait()
    save = TaskLedger.save
    monkeypatch.setattr(TaskLedger, "save", lambda self, task: False if task.status == "paused" else save(self, task))
    with pytest.raises(OSError):
        await runner.pause(task.id)
    assert runner.get(task.id).status == "running" and "paused" not in updates and runner.active_count == 0


@pytest.mark.parametrize("bad_id", [None, 42, "other-plan", "../other-plan"])
def test_corrupt_plan_identity_is_not_resumable_or_listed(tmp_path, bad_id):
    plan = dev_plan.DevPlan.new("不重定向计划", "vorto/recovery", "main")
    dev_plan.save_plan(str(tmp_path), plan)
    path = tmp_path / ".vortocode/dev_plans" / f"{plan.plan_id}.json"
    path.write_text(json.dumps({**plan.to_dict(), "plan_id": bad_id}))
    before = path.read_bytes()
    assert dev_plan.load_plan(str(tmp_path), plan.plan_id) is None
    assert dev_plan.list_plans(str(tmp_path)) == [] and path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_checkpoint", [1, 2])
async def test_dev_resume_checkpoint_failure_stops_before_implementation(tmp_path, monkeypatch, failed_checkpoint):
    from src.agents.dev_tools import build_dev_tools
    runner, _, _, plan, _, _ = stopped(tmp_path)
    plan.blocks[-1].kind = "independent"
    dev_plan.save_plan(str(tmp_path), plan)
    saved = dev_plan.save_plan
    count = 0

    def save(root, plan):
        nonlocal count
        count += 1
        return False if count == failed_checkpoint else saved(root, plan)

    monkeypatch.setattr(dev_plan, "save_plan", save)
    tool = next(t for t in build_dev_tools(str(tmp_path)) if t.name == "dev_resume")
    with pytest.raises(OSError, match="检查点"):
        await tool.handler({"plan_id": plan.plan_id})
    persisted = dev_plan.load_plan(str(tmp_path), plan.plan_id)
    assert persisted.blocks[0].status == "landed" and persisted.blocks[1].status == "pending"
    assert count == failed_checkpoint and runner.active_count == 0


@pytest.mark.asyncio
async def test_fresh_plan_save_failure_stops_after_decomposition_before_child_execution(tmp_path, monkeypatch):
    from src.agents import decompose, dev_tools
    calls = []

    async def plan(task):
        calls.append(task)
        return {"descriptions": ["实现配置"], "deferred": [], "independent": [], "total": 1}

    monkeypatch.setattr(decompose, "decompose_for_parallel", plan)
    monkeypatch.setattr(dev_tools, "preflight_dev", lambda *args, **kwargs: [])
    monkeypatch.setattr(dev_plan, "save_plan", lambda *args: False)
    tool = next(t for t in dev_tools.build_dev_tools(str(tmp_path)) if t.name == "dev_auto")
    with pytest.raises(OSError, match="检查点"):
        await tool.handler({"task": "实现配置"})
    assert calls == ["实现配置"] and dev_plan.list_plans(str(tmp_path)) == []


@pytest.mark.asyncio
async def test_direct_dev_resume_rejects_nonisolated_branch(tmp_path):
    from src.agents.dev_tools import build_dev_tools
    plan = dev_plan.DevPlan.new("不得写主分支", "main", "main")
    dev_plan.save_plan(str(tmp_path), plan)
    tool = next(t for t in build_dev_tools(str(tmp_path)) if t.name == "dev_resume")
    with pytest.raises(ValueError, match="隔离分支"):
        await tool.handler({"plan_id": plan.plan_id})
