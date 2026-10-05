"""Durable development questions and real isolated execution with a fake model."""
import asyncio
import json
import subprocess
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.agents.agent_loop import MainAgent
from src.agents.dev_plan import Block, DevPlan, load_plan, save_checkpoint
from src.agents.task_questions import bind_development_question
from src.gateway.collaboration import CollaborationConflict
from src.gateway.dev_questions import DevelopmentQuestions
from src.gateway.dispatch import DispatchBusy
from src.gateway.handoffs import CompletionInbox
from src.gateway.task_recovery import TaskRecovery, plan_revision
from src.gateway.tasks import TaskBlocked, TaskLedger, TaskRunner


def fixture(root, worker=None):
    async def execute(task, progress):
        return "fixture completed"
    runner = TaskRunner(str(root), worker or execute, max_concurrent=1)
    service = DevelopmentQuestions(runner, read_plan=lambda pid: load_plan(str(root), pid), validate_execution=lambda _: None)
    plan = DevPlan.new("实现配置", "vorto/questions", "main", plan_id="plan-questions")
    plan.blocks = [Block("done", "independent", "已有实现", status="landed"),
                   Block("todo", "dependent", "补充配置与测试", status="running", deps=["done"])]
    save_checkpoint(str(root), plan)
    task = runner.ledger.create("dev-resume", plan.task, owner_session="sid-owner", plan_id=plan.plan_id)
    task.status, task.branch = "running", plan.branch
    assert runner.ledger.save(task)
    return runner, service, task, plan


def blocked(root):
    runner, service, task, plan = fixture(root)
    service.stage(task, 1, plan.plan_id, "todo", plan_revision(plan), "目标环境？", ["测试", "生产"], "已定位 app.py", tainted=True)
    task.status = "blocked"
    assert runner.ledger.save(task)
    return runner, service, task, plan, task.development["questions"][0]["id"]


@pytest.mark.asyncio
async def test_durable_answer_same_task_replay_and_independent_handling(tmp_path):
    runner, service, task, plan, qid = blocked(tmp_path)
    assert runner.active_count == 0 and CompletionInbox(str(tmp_path), "sid-owner").pending() == []
    state = task.development
    assert state["questions"][0]["block_id"] == "todo" and state["questions"][0]["plan_revision"] == plan_revision(plan)
    resumed, replay = service.answer("owner", task.id, 1, qid, "测试环境")
    assert not replay and resumed.id == task.id and resumed.development["round"] == 2
    assert resumed.development["tainted"] and resumed.status == "queued"
    assert service.answer("owner", task.id, 1, qid, " 测试环境 ")[1]
    await runner.join(task.id)
    assert service.get("owner", task.id).status == "done"
    assert service.answer("owner", task.id, 1, qid, "测试环境")[1]
    with pytest.raises(CollaborationConflict):
        service.answer("owner", task.id, 1, qid, "生产环境")
    assert CompletionInbox(str(tmp_path), "sid-owner").pending()[0]["task_id"] == task.id


@pytest.mark.parametrize("change", ["owner", "round", "question", "expired", "expiry", "plan", "branch", "block", "cancelled", "policy", "capacity", "active", "other_active", "corrupt_scope"])
@pytest.mark.asyncio
async def test_answer_rejections_preserve_question_and_do_not_enqueue(tmp_path, monkeypatch, change):
    runner, service, task, plan, qid = blocked(tmp_path)
    owner, number = "owner", 1
    error = CollaborationConflict
    if change == "owner":
        owner = "other"
    elif change == "round":
        number = 2
    elif change == "question":
        qid = "question-other"
    elif change in {"expired", "expiry"}:
        task.development["questions"][0]["expires_at"] = "invalid" if change == "expiry" else (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        runner.ledger.save(task)
    elif change in {"plan", "branch", "block"}:
        if change == "plan":
            plan.task = "已变更目标"
        elif change == "branch":
            plan.branch = "main"
        else:
            plan.blocks[1].status = "landed"
        save_checkpoint(str(tmp_path), plan)
    elif change == "cancelled":
        service.cancel_waiting(task.id)
    elif change == "policy":
        def deny(_):
            raise CollaborationConflict("permission denied")
        service.validate_execution = deny
    elif change == "capacity":
        monkeypatch.setattr(TaskRunner, "active_count", property(lambda _: 100))
        error = DispatchBusy
    elif change in {"active", "other_active"}:
        monkeypatch.setattr(runner, "is_active", lambda tid: tid == task.id if change == "active" else tid != task.id)
        if change == "other_active":
            runner.ledger.create("dev", "other", plan_id=plan.plan_id)
    else:
        task.development["owner_session"] = "sid-other"
        runner.ledger.save(task)
    before = runner.get(task.id).to_dict()
    with pytest.raises(error):
        service.answer(owner, task.id, number, qid, "测试")
    assert runner.get(task.id).to_dict() == before and runner.active_count == (100 if change == "capacity" else 0)


@pytest.mark.parametrize("field,value", [("question", " "), ("question", True), ("question", "x" * 2001),
                                          ("options", ["x"] * 4), ("options", [False]), ("context", {})])
def test_question_payload_rejection_does_not_stop_task(tmp_path, field, value):
    runner, service, task, plan = fixture(tmp_path)
    args = {"question": "环境？", "options": [], "context": ""}
    args[field] = value
    with pytest.raises(CollaborationConflict):
        service.stage(task, 1, plan.plan_id, "todo", plan_revision(plan), **args)
    assert runner.get(task.id).status == "running" and not runner.get(task.id).development


@pytest.mark.asyncio
async def test_save_failures_do_not_publish_or_execute(tmp_path, monkeypatch):
    runner, service, task, plan = fixture(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(TaskLedger, "save", lambda *args: False)
        with pytest.raises(OSError):
            service.stage(task, 1, plan.plan_id, "todo", plan_revision(plan), "环境？", [], "")
    assert not runner.get(task.id).development
    runner, service, task, plan, qid = blocked(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(TaskLedger, "save", lambda *args: False)
        with pytest.raises(OSError):
            service.answer("owner", task.id, 1, qid, "测试")
        with pytest.raises(OSError):
            service.cancel_waiting(task.id)
    assert runner.get(task.id).status == "blocked" and runner.active_count == 0
    assert runner.get(task.id).development["questions"][0]["status"] == "open"


@pytest.mark.asyncio
async def test_restart_preserves_blocked_and_does_not_replay_saved_answer(tmp_path, monkeypatch):
    runner, service, task, plan, qid = blocked(tmp_path)
    runner.recover()
    assert runner.get(task.id).status == "blocked"
    monkeypatch.setattr(runner, "enqueue_worker", lambda _: None)
    service.answer("owner", task.id, 1, qid, "测试")
    runner.recover()
    assert service.answer("owner", task.id, 1, qid, "测试")[0].status == "interrupted"
    assert runner.active_count == 0


def test_staged_question_crash_closes_pending_request_without_claiming_cleanup(tmp_path):
    runner, service, task, plan = fixture(tmp_path)
    service.stage(task, 1, plan.plan_id, "todo", plan_revision(plan), "环境？", [], "")
    assert runner.get(task.id).status == "running"
    runner.recover()
    current = runner.get(task.id)
    assert current.status == "interrupted" and current.development["questions"][0]["status"] == "cancelled"
    assert not current.development["waiting"]


@pytest.mark.asyncio
async def test_two_questions_three_rounds_and_manual_recovery_keeps_history(tmp_path):
    runner, service, task, plan, qid = blocked(tmp_path)
    service.answer("owner", task.id, 1, qid, "测试")
    await runner.join(task.id)
    task = runner.get(task.id)
    task.status = "running"
    runner.ledger.save(task)
    service.stage(task, 2, plan.plan_id, "todo", plan_revision(plan), "版本？", [], "第二次发现")
    task.status = "blocked"
    runner.ledger.save(task)
    service.answer("owner", task.id, 2, task.development["questions"][-1]["id"], "v2")
    await runner.join(task.id)
    task = runner.get(task.id)
    task.status = "failed"
    runner.ledger.save(task)
    binding = bind_development_question(service, task, plan, plan.blocks[1])
    assert binding.tool is None and "测试" in binding.context and "v2" in binding.context and binding.tainted
    with pytest.raises(CollaborationConflict):
        service.stage(task, 3, plan.plan_id, "todo", plan_revision(plan), "第三问？", [], "")
    recovery = TaskRecovery(runner, read_plan=service.read_plan)
    child, _ = recovery.resume(task.id)
    assert child.development["round"] == 3 and len(child.development["questions"]) == 2
    assert child.development["root_task_id"] == task.id and child.development["tainted"]
    await runner.join(child.id)


def init_repo(root):
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-b", "main")
    git("config", "user.name", "Local Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (root / "tests").mkdir()
    (root / "app.py").write_text("VALUE = 0\n")
    (root / "tests/test_app.py").write_text("from app import VALUE\n\ndef test_nonnegative():\n    assert VALUE >= 0\n")
    (root / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
    (root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    git("add", "-A")
    git("commit", "-m", "Fixture")
    git("branch", "vorto/questions")
    return git


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.asyncio
async def test_real_development_loop_cleans_partial_work_answers_and_finishes(tmp_path, monkeypatch, native):
    from src.agents import main_agent
    from src.web.routers import tasks
    git = init_repo(tmp_path)
    initial = git("rev-parse", "main")
    runner, service, source, plan = fixture(tmp_path, tasks._dev_worker)
    source.status = "paused"
    runner.ledger.save(source)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tasks, "_RUNNER", runner)
    monkeypatch.setenv("VORTOCODE_SANDBOX", "off")
    monkeypatch.setenv("VORTOCODE_BUILD_MAX_STEPS", "999")
    monkeypatch.setenv("VORTOCODE_BUILD_AUTO_CONTINUES", "999")
    prompts, agents, events = [], [], []

    class LLM:
        async def chat(self, messages, **kwargs):
            prompts.append(str(messages))
            if len(prompts) == 1:
                calls = [("write_file", {"path": "scratch.py", "content": "PARTIAL = True\n"}),
                         ("ask_task_question", {"question": "目标环境？", "context": "已定位 app.py", "options": ["测试", "生产"]}),
                         ("write_file", {"path": "must_not_run.py", "content": "BAD = True\n"})]
            elif len(prompts) == 2:
                assert "测试环境" in prompts[-1] and "app.py" in prompts[-1]
                calls = [("write_file", {"path": "app.py", "content": "VALUE = 1\n"}),
                         ("write_file", {"path": "tests/test_app.py", "content": "from app import VALUE\n\ndef test_nonnegative():\n    assert VALUE >= 0\n\ndef test_target():\n    assert VALUE == 1\n"})]
            else:
                assert len(prompts) == 3
                return {"content": "已完成当前块"}
            if native:
                return {"content": None, "tool_calls": [{"id": f"call-{len(prompts)}-{i}", "name": name, "arguments": json.dumps(args)} for i, (name, args) in enumerate(calls)]}
            return {"content": json.dumps([{"tool": name, "args": args} for name, args in calls])}

    def factory(*args, **kwargs):
        kwargs.update(llm=LLM(), native=native, on_tool_event=lambda event, data: events.append((event, data)))
        agent = MainAgent(*args, **kwargs)
        agents.append(agent)
        return agent

    monkeypatch.setattr(main_agent, "MainAgent", factory)
    child, _ = TaskRecovery(runner, read_plan=service.read_plan).resume(source.id)
    await runner.join(child.id)
    waiting = runner.get(child.id)
    assert waiting.status == "blocked" and runner.active_count == 0 and len(prompts) == 1, waiting.error
    assert git("rev-parse", "vorto/questions") == initial
    assert git("worktree", "list", "--porcelain").count("worktree ") == 1
    assert not CompletionInbox(str(tmp_path), "sid-owner").pending() or all(item["task_id"] != child.id for item in CompletionInbox(str(tmp_path), "sid-owner").pending())
    assert not any(event == "finish" and data.get("args", {}).get("path") == "must_not_run.py" for event, data in events)
    qid = waiting.development["questions"][0]["id"]
    assert not service.answer("owner", child.id, 1, qid, "测试环境")[1]
    assert service.answer("owner", child.id, 1, qid, "测试环境")[1]
    await runner.join(child.id)
    finished = runner.get(child.id)
    final_plan = load_plan(str(tmp_path), plan.plan_id)
    assert finished.status == "done" and final_plan.status == "integrated"
    assert all(block.landed for block in final_plan.blocks) and final_plan.blocks[0].attempts == 0
    assert "2 passed" in final_plan.integration["output"] and len(prompts) == 3
    assert agents[0] is not agents[1] and all(agent.build_max_steps <= 16 and agent.build_auto_continues == 0 for agent in agents)
    assert git("diff", "--name-only", "main..vorto/questions").splitlines() == ["app.py", "tests/test_app.py"]
    assert git("rev-parse", "main") == initial and git("status", "--porcelain") == ""


@pytest.mark.asyncio
async def test_http_answer_auth_replay_owner_and_cancel(tmp_path, monkeypatch):
    from src.web.routers import tasks
    from src.web.server import app
    runner, service, task, plan, qid = blocked(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tasks, "_RUNNER", runner)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "dev-question-test")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    body = {"session": "owner", "round": 1, "question_id": qid, "answer": "测试"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        path = f"/api/tasks/{task.id}/answer"
        assert (await client.post(path, json=body)).status_code == 401
        headers = {"Authorization": "Bearer dev-question-test"}
        assert (await client.post(path, json={**body, "session": "other"}, headers=headers)).status_code == 409
        assert (await client.post(path, json={**body, "grant": True}, headers=headers)).status_code == 422
        first = await client.post(path, json=body, headers=headers)
        assert first.status_code == 202 and not first.json()["replayed"]
        await runner.join(task.id)
        replay = await client.post(path, json=body, headers=headers)
        assert replay.status_code == 202 and replay.json()["replayed"] and replay.json()["status"] == "done"
        assert replay.json()["questions"][0]["resumed_round"] == 2
    runner, service, task, plan, qid = blocked(tmp_path)
    assert service.cancel_waiting(task.id) and runner.get(task.id).status == "cancelled"
    with pytest.raises(CollaborationConflict):
        service.answer("owner", task.id, 1, qid, "测试")


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.asyncio
async def test_waiting_state_is_published_only_after_cleanup(tmp_path, cancel):
    staged, release = asyncio.Event(), asyncio.Event()
    updates = []
    runner, service, task, plan = fixture(tmp_path)

    async def execute(live, progress):
        service.stage(live, 1, plan.plan_id, "todo", plan_revision(plan), "环境？", [], "")
        staged.set()
        await release.wait()  # Equivalent to the leaf executor's cleanup boundary.
        raise TaskBlocked()

    runner._worker = execute
    runner._on_update = lambda task: updates.append(task.status)
    task.status = "queued"
    runner.ledger.save(task)
    runner.enqueue_worker(task.id)
    await staged.wait()
    current = runner.get(task.id)
    qid = current.development["questions"][0]["id"]
    assert current.status == "running" and runner.active_count == 1 and "blocked" not in updates
    with pytest.raises(CollaborationConflict):
        service.answer("owner", task.id, 1, qid, "测试")
    if cancel:
        runner.cancel(task.id)
    release.set()
    await runner.join(task.id)
    final = runner.get(task.id)
    assert final.status == ("cancelled" if cancel else "blocked") and runner.active_count == 0
    assert final.development["questions"][0]["status"] == ("cancelled" if cancel else "open")


@pytest.mark.asyncio
async def test_plan_change_after_answer_before_execution_fails_without_model(tmp_path, monkeypatch):
    from src.web.routers import tasks
    runner, service, task, plan, qid = blocked(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tasks, "_RUNNER", runner)
    runner._worker = tasks._dev_worker
    service.answer("owner", task.id, 1, qid, "测试")
    plan.blocks[1].desc = "改变已回答的计划块"
    save_checkpoint(str(tmp_path), plan)
    await runner.join(task.id)
    assert runner.get(task.id).status == "failed" and "回答后计划已变化" in runner.get(task.id).error


@pytest.mark.asyncio
async def test_direct_dev_resume_cannot_bypass_open_question(tmp_path):
    from src.agents.dev_tools import build_dev_tools
    runner, service, task, plan, qid = blocked(tmp_path)
    tool = next(tool for tool in build_dev_tools(str(tmp_path)) if tool.name == "dev_resume")
    with pytest.raises(ValueError, match="等待回答"):
        await tool.handler({"plan_id": plan.plan_id})


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.asyncio
async def test_parent_reads_and_answers_development_under_shared_pool(tmp_path, monkeypatch, native):
    from src.agents.main_agent import build_research_tools
    from src.gateway.collaboration import CollaborationService
    from src.web import task_dispatch
    from src.web.routers import tasks
    runner, service, task, plan, qid = blocked(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tasks, "_RUNNER", runner)
    release = asyncio.Event()
    executions, prompts = [], []

    async def worker(task, progress):
        executions.append(task.id)
        await release.wait()
        return "完成"

    runner._worker = worker
    tools = build_research_tools(str(tmp_path), collaboration=CollaborationService(str(tmp_path), "sid-owner"),
                                answer_task_question=lambda *args: task_dispatch.answer_task_question(str(tmp_path), *args))
    assert "会话问题提示" in task_dispatch.task_turn_hint(str(tmp_path), "sid-owner", {t.name for t in tools})
    assert task_dispatch.task_turn_hint(str(tmp_path), "sid-other", {t.name for t in tools}) == ""

    class LLM:
        async def chat(self, messages, **kwargs):
            prompts.append(str(messages))
            if len(prompts) == 1:
                name, args = "task_status", {"task_id": task.id}
            elif len(prompts) == 2:
                assert "目标环境" in prompts[-1] and "todo" in prompts[-1] and qid in prompts[-1]
                name, args = "task_answer", {"task_id": task.id, "round": 1, "question_id": qid, "answer": "测试环境"}
            else:
                assert "answered_question_id" in prompts[-1]
                return {"content": "已回答，等待后台完成"}
            if native:
                return {"content": "", "tool_calls": [{"id": f"call-{name}", "name": name, "arguments": json.dumps(args)}]}
            return {"content": json.dumps({"tool": name, "args": args})}

    parent = MainAgent(tools, llm=LLM(), native=native, max_steps=3)
    assert await parent.run_turn("用户明确选择测试环境，请继续", mode="plan") == "已回答，等待后台完成"
    assert runner.active_count == 1 and not release.is_set() and len(prompts) == 3
    assert task_dispatch.answer_task_question(str(tmp_path), "sid-owner", task.id, 1, qid, "测试环境")[1]
    release.set()
    await runner.join(task.id)
    assert executions == [task.id] and runner.get(task.id).status == "done"


def test_blocked_development_projections_keep_exact_question(tmp_path):
    from src.gateway.decisions import build_decision_queue
    from src.gateway.runtime_inbox import build_runtime_inbox
    from src.gateway.worktree_sessions import task_session_view
    runner, service, task, plan, qid = blocked(tmp_path)
    view = task_session_view(str(tmp_path), task, worktrees=[])
    assert view["questions"][0]["id"] == qid and not view["can_resume"] and not view["can_pause"]
    assert view["session"] == "sid-owner" and view["handoff"]["handling"] is None
    decisions = build_decision_queue(tasks=[task])
    assert len(decisions) == 1 and qid in decisions[0]["id"] and decisions[0]["tainted"]
    inbox = build_runtime_inbox(scope="project", tasks=[task])
    assert inbox["counts"]["tasks_attention"] == 1 and inbox["tasks"][0]["detail"] == "目标环境？"


@pytest.mark.asyncio
async def test_failed_blocked_save_keeps_interrupted_evidence_without_success_event(tmp_path, monkeypatch):
    runner, service, task, plan = fixture(tmp_path)
    updates = []
    save = TaskLedger.save

    async def worker(live, progress):
        service.stage(live, 1, plan.plan_id, "todo", plan_revision(plan), "环境？", [], "")
        raise TaskBlocked()

    monkeypatch.setattr(TaskLedger, "save", lambda ledger, current: False if current.status == "blocked" else save(ledger, current))
    runner._worker = worker
    runner._on_update = lambda task: updates.append(task.status)
    task.status = "queued"
    runner.ledger.save(task)
    runner.enqueue_worker(task.id)
    with pytest.raises(OSError):
        await runner.join(task.id)
    assert "blocked" not in updates and runner.get(task.id).status == "running"
    runner.recover()
    assert runner.get(task.id).status == "interrupted" and runner.get(task.id).development["questions"][0]["status"] == "cancelled"
