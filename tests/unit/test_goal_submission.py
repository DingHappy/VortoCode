"""Goal intent identity, durable association, and failure recovery; no real workers/models."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from src.workflow.tasks.goal_submission import GoalSubmissionConflict, goal_contract, goal_task_id, submit_goal
from src.workflow.tasks.tasks import TaskRunner
from src.workflow.workbench.goals import GoalLedger


@pytest.fixture
def setup(tmp_path):
    ledger = GoalLedger(str(tmp_path))
    goal = ledger.create("交付功能", ["行为通过验收"])
    calls = []

    async def worker(task, _progress):
        saved = ledger.load(task.goal_id)
        assert task.id in saved.task_ids  # Work never precedes the durable Goal association.
        calls.append(task.id)
        return "offline result"

    return ledger, goal, TaskRunner(str(tmp_path), worker), calls


async def test_response_loss_replays_one_task_even_after_it_finishes(setup):
    ledger, goal, runner, calls = setup
    task, replayed = submit_goal(runner, goal.id)
    assert not replayed
    await runner.join(task.id)
    same, replayed = submit_goal(runner, goal.id)
    assert replayed and same.id == task.id and same.status == "done"
    assert calls == [task.id] and ledger.load(goal.id).task_ids == [task.id]
    assert ledger.load(goal.id).status != "achieved"


async def test_new_round_requires_a_refreshed_predecessor_and_old_replays_stay_old(setup):
    ledger, goal, runner, calls = setup
    first, _ = submit_goal(runner, goal.id)
    await runner.join(first.id)
    second, replayed = submit_goal(runner, goal.id, previous_task_id=first.id)
    await runner.join(second.id)
    assert not replayed and second.id != first.id
    old, replayed = submit_goal(runner, goal.id)
    assert replayed and old.id == first.id
    assert ledger.load(goal.id).task_ids == [first.id, second.id]
    assert calls == [first.id, second.id]
    with pytest.raises(GoalSubmissionConflict, match="刷新"):
        submit_goal(runner, goal.id, previous_task_id="unrelated-task")


async def test_resume_keeps_plan_and_conflicting_modes_cannot_fork_one_snapshot(setup):
    ledger, goal, runner, calls = setup
    goal.plan_id = "plan-1"
    ledger.save_required(goal)
    task, _ = submit_goal(runner, goal.id, resume=True)
    await runner.join(task.id)
    assert task.kind == "dev-resume" and task.plan_id == "plan-1"
    with pytest.raises(GoalSubmissionConflict, match="新的任务"):
        submit_goal(runner, goal.id, resume=False)
    assert calls == [task.id]


@pytest.mark.parametrize("after_write", [False, True])
async def test_goal_save_failure_never_creates_or_schedules_work(setup, monkeypatch, after_write):
    ledger, goal, runner, calls = setup
    save = GoalLedger.save

    def failed(self, value):
        if after_write:
            assert save(self, value)
        return False

    monkeypatch.setattr(GoalLedger, "save", failed)
    with pytest.raises(OSError):
        submit_goal(runner, goal.id)
    assert not runner.ledger.list() and not calls and not runner._running
    monkeypatch.setattr(GoalLedger, "save", save)
    if after_write:
        with pytest.raises(GoalSubmissionConflict, match="缺失"):
            submit_goal(runner, goal.id)
        assert not runner.ledger.list() and not calls


@pytest.mark.parametrize("after_write", [False, True])
async def test_task_create_failure_retains_fixed_identity_and_never_replays(setup, monkeypatch, after_write):
    ledger, goal, runner, calls = setup
    create = runner.ledger.create

    def failed(*args, **kwargs):
        if after_write:
            create(*args, **kwargs)
        raise OSError("private storage path")

    monkeypatch.setattr(runner.ledger, "create", failed)
    with pytest.raises(OSError):
        submit_goal(runner, goal.id)
    tid = goal_task_id(goal.id, "", False)
    assert ledger.load(goal.id).task_ids == [tid] and not calls
    monkeypatch.setattr(runner.ledger, "create", create)
    with pytest.raises(GoalSubmissionConflict):
        submit_goal(runner, goal.id)
    assert not calls and not runner._running
    if after_write:
        assert runner.get(tid).status == "interrupted"


async def test_enqueue_claim_write_failure_never_starts_worker(setup, monkeypatch):
    ledger, goal, runner, calls = setup
    save = runner.ledger.save
    writes = 0

    def failed_claim(task):
        nonlocal writes
        writes += 1
        return save(task) if writes == 1 else False

    monkeypatch.setattr(runner.ledger, "save", failed_claim)
    with pytest.raises(OSError):
        submit_goal(runner, goal.id)
    assert not calls and not runner._running
    monkeypatch.setattr(runner.ledger, "save", save)
    with pytest.raises(GoalSubmissionConflict, match="提交未完成"):
        submit_goal(runner, goal.id)
    assert runner.get(ledger.load(goal.id).task_ids[-1]).status == "interrupted"


async def test_enqueue_response_failure_cannot_submit_a_second_worker(setup, monkeypatch):
    _ledger, goal, runner, calls = setup
    enqueue = runner.enqueue_worker

    def lost_reply(tid):
        enqueue(tid)
        raise RuntimeError("response lost")

    monkeypatch.setattr(runner, "enqueue_worker", lost_reply)
    with pytest.raises(GoalSubmissionConflict, match="排队结果无法确认"):
        submit_goal(runner, goal.id)
    tid = goal_task_id(goal.id, "", False)
    await runner.join(tid)
    same, replayed = submit_goal(runner, goal.id)
    assert replayed and same.id == tid and calls == [tid]


async def test_restart_marks_unfinished_queue_interrupted_without_enqueuing(setup, monkeypatch):
    _ledger, goal, runner, calls = setup
    monkeypatch.setattr(runner, "enqueue_worker", lambda tid: None)
    task, _ = submit_goal(runner, goal.id)
    recovered = runner.recover()
    assert [item.id for item in recovered] == [task.id] and recovered[0].status == "interrupted"
    same, replayed = submit_goal(runner, goal.id)
    assert replayed and same.status == "interrupted" and not calls


async def test_concurrent_callers_share_one_durable_task_and_enqueue(setup, monkeypatch):
    ledger, goal, runner, _calls = setup
    queued = []
    monkeypatch.setattr(runner, "enqueue_worker", lambda tid: queued.append(tid))
    monkeypatch.setattr(runner, "is_active", lambda tid: tid in queued)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: submit_goal(runner, goal.id), range(4)))
    assert sum(not replayed for _task, replayed in results) == 1
    assert len(queued) == 1 and ledger.load(goal.id).task_ids == queued


async def test_corrupt_task_record_and_changed_contract_do_not_grant_new_work(setup):
    ledger, goal, runner, calls = setup
    task, _ = submit_goal(runner, goal.id)
    await runner.join(task.id)
    changed = ledger.load(goal.id)
    changed.objective = "different contract"
    ledger.save_required(changed)
    with pytest.raises(GoalSubmissionConflict, match="合同不一致"):
        submit_goal(runner, goal.id)
    runner.ledger._path(task.id).write_text("{broken")
    with pytest.raises(GoalSubmissionConflict, match="无法读取"):
        submit_goal(runner, goal.id)
    assert runner.ledger._path(task.id).read_text() == "{broken" and calls == [task.id]


@pytest.mark.parametrize("previous", [None, False, [], "../outside", "x" * 121])
async def test_invalid_predecessor_does_not_create_work(setup, previous):
    _ledger, goal, runner, calls = setup
    with pytest.raises(GoalSubmissionConflict):
        submit_goal(runner, goal.id, previous_task_id=previous)
    assert not runner.ledger.list() and not calls


@pytest.mark.parametrize("operation", ["create", "update", "verifier", "evidence"])
def test_goal_mutations_do_not_report_success_after_failed_save(setup, monkeypatch, operation):
    ledger, goal, _runner, _calls = setup
    before = ledger._path(goal.id).read_bytes()
    monkeypatch.setattr(GoalLedger, "save", lambda *args: False)
    with pytest.raises(OSError):
        if operation == "create":
            ledger.create("new", ["saved"])
        elif operation == "update":
            ledger.update_contract(goal.id, objective="changed", acceptance_criteria=["saved"])
        elif operation == "verifier":
            ledger.set_verifier(goal.id, "criterion-1", {"kind": "manual"})
        else:
            ledger.record_evidence(goal.id, "criterion-1", kind="manual", summary="done", passed=True)
    assert ledger._path(goal.id).read_bytes() == before


def test_creation_identity_never_overwrites_progress_or_a_conflicting_contract(tmp_path):
    ledger = GoalLedger(str(tmp_path))
    goal = ledger.create("deliver", ["accept"], request_id="create-1")
    goal.status, goal.blocker = "blocked", "needs owner"
    ledger.save_required(goal)
    replayed = ledger.create("deliver", ["accept"], request_id="create-1")
    assert replayed.id == goal.id and replayed.blocker == "needs owner"
    with pytest.raises(ValueError, match="不同的目标"):
        ledger.create("changed", ["accept"], request_id="create-1")
    ledger._path(goal.id).write_text("{broken")
    with pytest.raises(OSError, match="未覆盖"):
        ledger.create("deliver", ["accept"], request_id="create-1")
    assert ledger._path(goal.id).read_text() == "{broken"


@pytest.mark.parametrize("operation", ["create", "start", "update", "verifier", "evidence"])
async def test_api_returns_503_on_failed_goal_writes_without_executing(setup, monkeypatch, operation):
    from fastapi import HTTPException
    from src.web.routers import goals as api, tasks as tasks_api

    ledger, goal, runner, calls = setup
    monkeypatch.chdir(ledger.repo_root)
    monkeypatch.setattr(tasks_api, "get_runner", lambda: runner)
    monkeypatch.setattr(GoalLedger, "save", lambda *args: False)
    with pytest.raises(HTTPException) as error:
        if operation == "create":
            await api.create_goal({"objective": "new", "acceptance_criteria": ["saved"]})
        elif operation == "start":
            await api.run_goal(goal.id)
        elif operation == "update":
            await api.update_goal(goal.id, {"objective": "changed"})
        elif operation == "verifier":
            await api.configure_goal_verifier(goal.id, "criterion-1", {"kind": "manual"})
        else:
            await api.record_goal_evidence(goal.id, "criterion-1", {"passed": True, "summary": "done"})
    assert error.value.status_code == 503 and not calls and not runner._running
    assert ledger.load(goal.id).status == "draft"


async def test_api_create_and_start_requires_identity_and_response_retries_reuse_task(setup, monkeypatch):
    from fastapi import HTTPException
    from src.web.routers import goals as api, tasks as tasks_api

    ledger, _goal, runner, calls = setup
    monkeypatch.chdir(ledger.repo_root)
    monkeypatch.setattr(tasks_api, "get_runner", lambda: runner)
    body = {"objective": "new", "acceptance_criteria": ["saved"], "start": True}
    with pytest.raises(HTTPException) as error:
        await api.create_goal(body)
    assert error.value.status_code == 400 and not calls
    first = await api.create_goal({**body, "request_id": "intent-1"})
    await runner.join(first["started_task_id"])
    replayed = await api.create_goal({**body, "request_id": "intent-1"})
    assert first["id"] == replayed["id"] and first["started_task_id"] == replayed["started_task_id"]
    assert calls == [first["started_task_id"]]


async def test_api_new_round_is_explicit_and_invalid_resume_is_not_coerced(setup, monkeypatch):
    from fastapi import HTTPException
    from src.web.routers import goals as api, tasks as tasks_api

    ledger, goal, runner, calls = setup
    monkeypatch.chdir(ledger.repo_root)
    monkeypatch.setattr(tasks_api, "get_runner", lambda: runner)
    with pytest.raises(HTTPException) as error:
        await api.run_goal(goal.id, {"resume": "false"})
    assert error.value.status_code == 400 and not calls
    first = await api.run_goal(goal.id, {"expected_task_id": ""})
    await runner.join(first["task"]["id"])
    replayed = await api.run_goal(goal.id, {"expected_task_id": ""})
    assert replayed["replayed"] and replayed["task"]["id"] == first["task"]["id"]
    second = await api.run_goal(goal.id, {"expected_task_id": first["task"]["id"]})
    await runner.join(second["task"]["id"])
    assert second["replayed"] is False and len(calls) == 2


@pytest.mark.parametrize("change", ["objective", "verifier"])
async def test_old_creation_request_cannot_start_an_edited_goal(setup, monkeypatch, change):
    from fastapi import HTTPException
    from src.web.routers import goals as api, tasks as tasks_api

    ledger, _goal, runner, calls = setup
    monkeypatch.chdir(ledger.repo_root)
    monkeypatch.setattr(tasks_api, "get_runner", lambda: runner)
    body = {"objective": "original", "acceptance_criteria": ["accept"], "request_id": "create-1"}
    goal = await api.create_goal(body)
    if change == "objective":
        ledger.update_contract(goal["id"], objective="edited", acceptance_criteria=["accept"])
    else:
        ledger.set_verifier(goal["id"], "criterion-1", {"kind": "file", "path": "artifact"})
    with pytest.raises(HTTPException) as error:
        await api.create_goal({**body, "start": True})
    assert error.value.status_code == 409 and not runner.ledger.list() and not calls


async def test_contract_change_between_read_and_submission_does_not_run_new_contract(setup):
    ledger, goal, runner, calls = setup
    original = goal_contract(goal)
    ledger.update_contract(goal.id, objective="edited", acceptance_criteria=["accept"])
    with pytest.raises(GoalSubmissionConflict, match="合同已变化"):
        submit_goal(runner, goal.id, expected_contract=original)
    assert not runner.ledger.list() and not calls
