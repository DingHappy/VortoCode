"""Goal contracts: persistence, task linkage, and evidence completion gate."""
import asyncio

import pytest

from src.gateway.goals import GoalLedger, evaluate_file_verifier, sync_goal_from_task
from src.gateway.tasks import BackgroundTask, TaskRunner


def test_goal_roundtrip_and_contract(tmp_path):
    ledger = GoalLedger(str(tmp_path))
    goal = ledger.create(
        "实现目标开发闭环",
        ["后端测试通过", "Desktop 能逐项验收"],
        constraints=["复用现有 TaskRunner"],
        non_goals=["不恢复无限自治循环"],
    )
    loaded = ledger.load(goal.id)
    assert loaded is not None
    assert loaded.objective == "实现目标开发闭环"
    assert [item.id for item in loaded.acceptance_criteria] == ["criterion-1", "criterion-2"]
    prompt = loaded.contract_prompt()
    assert "复用现有 TaskRunner" in prompt and "不恢复无限自治循环" in prompt
    assert ledger.list()[0].id == goal.id
    assert ledger.load("../../bad") is None


def test_goal_only_achieves_after_every_criterion_has_passing_evidence(tmp_path):
    ledger = GoalLedger(str(tmp_path))
    goal = ledger.create("交付功能", ["测试绿", "人工验收通过"])
    goal.status = "active"
    ledger.save(goal)

    first = ledger.record_evidence(
        goal.id, "criterion-1", kind="test", summary="pytest 通过", passed=True,
    )
    assert first.status == "active"
    assert first.acceptance_criteria[0].status == "passed"
    assert first.acceptance_criteria[1].status == "pending"

    failed = ledger.record_evidence(
        goal.id, "criterion-2", kind="manual", summary="交互仍有问题", passed=False,
    )
    assert failed.status == "blocked" and failed.blocker

    achieved = ledger.record_evidence(
        goal.id, "criterion-2", kind="manual", summary="修复后复验通过", passed=True,
    )
    assert achieved.status == "achieved"
    assert all(item.evidence_ids for item in achieved.acceptance_criteria)


def test_only_pristine_draft_contract_can_be_edited_or_deleted(tmp_path):
    ledger = GoalLedger(str(tmp_path))
    draft = ledger.create("旧目标", ["旧标准"])
    created = draft.created
    updated = ledger.update_contract(
        draft.id,
        objective="新目标",
        acceptance_criteria=["标准一", "标准二"],
        constraints=["保持兼容"],
        non_goals=["不改 runtime"],
    )
    assert updated.id == draft.id and updated.created == created
    assert updated.objective == "新目标"
    assert [item.text for item in updated.acceptance_criteria] == ["标准一", "标准二"]

    started = ledger.create("已开始", ["标准"])
    started.status = "active"
    started.task_ids.append("task-1")
    ledger.save(started)
    with pytest.raises(ValueError):
        ledger.update_contract(started.id, objective="篡改", acceptance_criteria=["标准"])
    with pytest.raises(ValueError):
        ledger.delete_draft(started.id)

    assert ledger.delete_draft(draft.id) is True
    assert ledger.load(draft.id) is None


def test_draft_verifier_roundtrip_and_contract_lock(tmp_path):
    ledger = GoalLedger(str(tmp_path))
    goal = ledger.create("自动验收", ["Lint 通过"])
    configured = ledger.set_verifier(goal.id, "criterion-1", {
        "kind": "lint", "command": "ruff check src", "timeout": 120,
    })
    verifier = configured.acceptance_criteria[0].verifier
    assert verifier is not None and verifier.kind == "lint" and verifier.timeout == 120
    loaded = ledger.load(goal.id)
    assert loaded is not None and "lint: ruff check src" in loaded.contract_prompt()

    loaded.status = "active"
    ledger.save(loaded)
    with pytest.raises(ValueError, match="draft"):
        ledger.set_verifier(goal.id, "criterion-1", {"kind": "manual"})


def test_file_verifier_is_repo_contained_and_checks_content(tmp_path):
    target = tmp_path / "build.txt"
    target.write_text("BUILD OK\n", encoding="utf-8")
    ledger = GoalLedger(str(tmp_path))
    goal = ledger.create("检查产物", ["产物存在"])
    configured = ledger.set_verifier(goal.id, "criterion-1", {
        "kind": "file", "path": "build.txt", "contains": "BUILD OK",
    })
    verifier = configured.acceptance_criteria[0].verifier
    assert verifier is not None
    assert evaluate_file_verifier(str(tmp_path), verifier)[0] is True

    verifier.path = "../outside.txt"
    passed, summary = evaluate_file_verifier(str(tmp_path), verifier)
    assert passed is False and "越界" in summary


def test_task_done_adds_execution_evidence_but_does_not_claim_goal_achieved(tmp_path):
    from src.agents.dev_plan import DevPlan, save_plan

    ledger = GoalLedger(str(tmp_path))
    goal = ledger.create("完成开发", ["行为验收通过"])
    plan = DevPlan.new("完成开发", "vorto/goal", "main", plan_id="goal-plan")
    plan.integration = {"ok": True, "cmd": "pytest", "output": "ok"}
    plan.status = "integrated"
    save_plan(str(tmp_path), plan)

    task = BackgroundTask.new("dev", goal.contract_prompt(), goal_id=goal.id, plan_id=plan.plan_id)
    task.status = "done"
    task.branch = plan.branch
    synced = sync_goal_from_task(str(tmp_path), task)

    assert synced is not None and synced.status == "active"
    assert synced.plan_id == plan.plan_id and synced.branch == plan.branch
    assert any(item.kind == "test" and item.passed for item in synced.evidence)
    assert synced.acceptance_criteria[0].status == "pending"


def test_task_done_with_failed_plan_blocks_goal(tmp_path):
    from src.agents.dev_plan import DevPlan, save_plan

    ledger = GoalLedger(str(tmp_path))
    goal = ledger.create("完成开发", ["验证通过"])
    plan = DevPlan.new("完成开发", "vorto/failed", "main", plan_id="failed-plan")
    plan.status = "failed"
    save_plan(str(tmp_path), plan)
    task = BackgroundTask.new("dev", goal.contract_prompt(), goal_id=goal.id, plan_id=plan.plan_id)
    task.status = "done"
    synced = sync_goal_from_task(str(tmp_path), task)
    assert synced is not None and synced.status == "blocked"
    assert any(item.source == "plan:failed-plan:status" and not item.passed for item in synced.evidence)


def test_failed_task_blocks_goal_and_keeps_it_retryable(tmp_path):
    ledger = GoalLedger(str(tmp_path))
    goal = ledger.create("完成开发", ["验证通过"])
    task = BackgroundTask.new("dev", goal.contract_prompt(), goal_id=goal.id)
    task.status = "failed"
    task.error = "集成测试失败"
    synced = sync_goal_from_task(str(tmp_path), task)
    assert synced is not None and synced.status == "blocked"
    assert "集成测试失败" in synced.blocker
    assert task.id in synced.task_ids


def test_runner_persists_goal_and_resume_plan_links(tmp_path):
    async def worker(task, on_progress):
        return "ok"

    async def run():
        runner = TaskRunner(str(tmp_path), worker)
        task = await runner.submit("继续", kind="dev-resume", goal_id="goal-1", plan_id="plan-1")
        await runner.join(task.id)
        loaded = runner.get(task.id)
        assert loaded is not None
        assert loaded.goal_id == "goal-1" and loaded.plan_id == "plan-1"

    asyncio.run(run())


def test_goal_rest_create_list_and_evidence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    from src.web.routers import tasks as tasks_router
    from src.web.server import app

    async def worker(task, on_progress):
        return "ok"

    runner = TaskRunner(
        str(tmp_path), worker,
        on_update=lambda task: sync_goal_from_task(str(tmp_path), task),
    )
    monkeypatch.setattr(tasks_router, "_RUNNER", runner)
    client = TestClient(app)

    assert client.post("/api/goals", json={}).status_code == 400
    response = client.post("/api/goals", json={
        "objective": "交付目标面板",
        "acceptance_criteria": ["后端契约通过", "桌面可验收"],
        "constraints": ["复用 runtime"],
    })
    assert response.status_code == 200
    goal = response.json()
    assert goal["status"] == "draft" and goal["progress"]["total"] == 2
    assert client.get("/api/goals").json()["goals"][0]["id"] == goal["id"]

    edited = client.patch(f"/api/goals/{goal['id']}", json={
        "objective": "交付可编辑目标面板",
        "acceptance_criteria": ["后端契约通过", "草稿可编辑", "桌面可验收"],
    })
    assert edited.status_code == 200
    assert edited.json()["objective"] == "交付可编辑目标面板"
    assert edited.json()["progress"]["total"] == 3

    started = client.post(f"/api/goals/{goal['id']}/run", json={})
    assert started.status_code == 200
    assert started.json()["task"]["goal_id"] == goal["id"]
    assert client.patch(f"/api/goals/{goal['id']}", json={"objective": "执行后篡改"}).status_code == 409
    assert client.delete(f"/api/goals/{goal['id']}").status_code == 409

    evidence = client.post(
        f"/api/goals/{goal['id']}/criteria/criterion-1/evidence",
        json={"passed": True, "kind": "test", "summary": "契约测试通过"},
    )
    assert evidence.status_code == 200
    assert evidence.json()["status"] == "active"
    bad = client.post(
        f"/api/goals/{goal['id']}/criteria/nope/evidence",
        json={"passed": True, "summary": "不存在"},
    )
    assert bad.status_code == 400

    disposable = client.post("/api/goals", json={
        "objective": "临时目标",
        "acceptance_criteria": ["确认后再跑"],
    }).json()
    deleted = client.delete(f"/api/goals/{disposable['id']}")
    assert deleted.status_code == 200 and deleted.json()["ok"] is True
    assert client.get(f"/api/goals/{disposable['id']}").status_code == 404


def test_goal_rest_configures_and_runs_file_verifier(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifact.txt").write_text("READY\n", encoding="utf-8")
    from fastapi.testclient import TestClient
    from src.gateway.runs import RunManager
    from src.web.routers import runs as runs_router
    from src.web.routers import tasks as tasks_router
    from src.web.server import app

    async def worker(task, on_progress):
        return "ok"

    monkeypatch.setattr(tasks_router, "_RUNNER", TaskRunner(str(tmp_path), worker))
    monkeypatch.setattr(runs_router, "_RUN_MANAGER", RunManager(str(tmp_path)))
    client = TestClient(app)
    goal = client.post("/api/goals", json={
        "objective": "验收文件产物",
        "acceptance_criteria": ["产物内容正确"],
    }).json()
    assert client.post(f"/api/goals/{goal['id']}/verify").status_code == 409

    configured = client.put(
        f"/api/goals/{goal['id']}/criteria/criterion-1/verifier",
        json={"kind": "file", "path": "artifact.txt", "contains": "READY"},
    )
    assert configured.status_code == 200
    assert configured.json()["acceptance_criteria"][0]["verifier"]["kind"] == "file"

    ledger = GoalLedger(str(tmp_path))
    active = ledger.load(goal["id"])
    assert active is not None
    active.status = "active"
    ledger.save(active)
    verified = client.post(f"/api/goals/{goal['id']}/verify")
    assert verified.status_code == 200
    assert verified.json()["goal"]["status"] == "achieved"
    assert verified.json()["runs"] == []
