import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.agents.agent_loop import MainAgent
from src.agents.delegation import build_delegation_executor
from src.agents.tool import Tool
from src.gateway.collaboration import CollaborationConflict, CollaborationService
from src.gateway.dispatch import DispatchService
from src.gateway.handoffs import CompletionInbox, completion_state
from src.gateway.task_questions import question_views
from src.gateway.tasks import TaskLedger, TaskRunner


def blocked(root, owner="sid-owner"):
    service = CollaborationService(str(root), owner)
    task = service.create("研究部署配置", dispatch={"source": "api", "max_steps": 4, "timeout_seconds": 30})
    service.start(task.id, 1)
    task = service.ask_question(task.id, 1, "应检查哪个环境？", ["测试环境", "生产环境"], "已定位 config.py", tainted=True)
    return service, task, task.collaboration["questions"][0]["id"]


def test_question_and_answer_persist_with_separate_execution_review_and_handling(tmp_path):
    service, task, qid = blocked(tmp_path)
    assert task.status == "blocked" and task.collaboration["review"] == "not_submitted"
    assert completion_state(task) is None
    assert CompletionInbox(str(tmp_path), "sid-owner").pending() == []
    service = CollaborationService(str(tmp_path), "sid-owner")
    assert service.get(task.id).collaboration["questions"][0]["context"] == "已定位 config.py"
    answered, replayed = service.answer_question(task.id, 1, qid, "测试环境")
    assert not replayed and answered.status == "queued" and answered.collaboration["round"] == 2
    assert answered.collaboration["tainted"] is True
    before = answered.to_dict()
    replay, repeated = service.answer_question(task.id, 1, qid, "测试环境")
    assert repeated and replay.to_dict() == before
    with pytest.raises(CollaborationConflict):
        service.answer_question(task.id, 1, qid, "生产环境")
    service.start(task.id, 2)
    finished = service.finish(task.id, 2, "已完成研究")
    assert finished.collaboration["review"] == "pending"
    assert service.answer_question(task.id, 1, qid, "测试环境")[1]
    with pytest.raises(CollaborationConflict):
        service.finish(task.id, 1, "迟到结果")


def test_waiting_task_is_visible_in_decisions_and_runtime_inbox(tmp_path):
    from src.gateway.decisions import build_decision_queue
    from src.gateway.runtime_inbox import build_runtime_inbox
    from src.gateway.worktree_sessions import task_session_view
    service, task, qid = blocked(tmp_path)
    decisions = build_decision_queue(tasks=[task])
    assert len(decisions) == 1 and decisions[0]["action"] == "open_task"
    assert qid in decisions[0]["id"] and decisions[0]["tainted"]
    inbox = build_runtime_inbox(scope="project", tasks=[task])
    assert inbox["counts"]["tasks_attention"] == 1 and inbox["counts"]["tasks_active"] == 0
    assert inbox["tasks"][0]["detail"] == "应检查哪个环境？"
    view = task_session_view(str(tmp_path), task, worktrees=[])
    assert view["handoff"]["handling"] is None and not view["can_resume"]
    service.answer_question(task.id, 1, qid, "测试")
    assert build_decision_queue(tasks=[service.get(task.id)]) == []


@pytest.mark.parametrize("change", ["owner", "round", "question", "cancelled", "expired", "corrupt_expiry", "identity"])
def test_invalid_answers_do_not_resume(tmp_path, change):
    service, task, qid = blocked(tmp_path)
    number = 1
    if change == "owner":
        service = CollaborationService(str(tmp_path), "sid-other")
    elif change == "round":
        number = 2
    elif change == "question":
        qid = "question-other"
    elif change == "cancelled":
        service.abort(task.id, 1, cancelled=True)
    else:
        question = task.collaboration["questions"][0]
        if change == "expired":
            question["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        elif change == "corrupt_expiry":
            question["expires_at"] = "broken"
        else:
            question["task_id"] = "task-other"
        service.ledger.save(task)
    before = service.ledger.load(task.id).to_dict()
    with pytest.raises(CollaborationConflict):
        service.answer_question(task.id, number, qid, "测试环境")
    assert service.ledger.load(task.id).to_dict() == before


def test_expiry_projection_cancel_and_question_round_limit(tmp_path):
    service, task, qid = blocked(tmp_path)
    service.answer_question(task.id, 1, qid, "测试")
    service.start(task.id, 2)
    task = service.ask_question(task.id, 2, "目标版本？")
    second = task.collaboration["questions"][-1]["id"]
    service.answer_question(task.id, 2, second, "v2")
    service.start(task.id, 3)
    with pytest.raises(CollaborationConflict, match="轮次"):
        service.ask_question(task.id, 3, "再问一次？")
    other, task, _ = blocked(tmp_path)
    task.collaboration["questions"][0]["expires_at"] = "invalid"
    other.ledger.save(task)
    assert question_views(task)[0]["status"] == "expired"
    cancelled = other.abort(task.id, 1, cancelled=True)
    assert cancelled.status == "cancelled" and cancelled.collaboration["questions"][0]["status"] == "expired"


@pytest.mark.parametrize("field,value", [("question", " "), ("question", True), ("question", "x" * 2001),
                                         ("options", ["x"] * 4), ("options", [False]), ("context", {})])
def test_question_validation_leaves_running_state(tmp_path, field, value):
    service = CollaborationService(str(tmp_path), "sid-owner")
    task = service.create("研究", dispatch={"source": "api"})
    service.start(task.id, 1)
    args = {"question": "环境？", "options": [], "context": ""}
    args[field] = value
    with pytest.raises(CollaborationConflict):
        service.ask_question(task.id, 1, **args)
    assert service.get(task.id).status == "running"


def test_failed_question_and_answer_save_never_publish_success(tmp_path, monkeypatch):
    updates = []
    service, task, qid = blocked(tmp_path)
    service._on_update = updates.append
    with monkeypatch.context() as patch:
        patch.setattr(TaskLedger, "save", lambda *args: False)
        with pytest.raises(OSError):
            service.answer_question(task.id, 1, qid, "测试")
    assert service.get(task.id).status == "blocked" and updates == []
    service.answer_question(task.id, 1, qid, "测试")
    service.start(task.id, 2)
    updates.clear()
    with monkeypatch.context() as patch:
        patch.setattr(TaskLedger, "save", lambda *args: False)
        with pytest.raises(OSError):
            service.ask_question(task.id, 2, "版本？")
    assert service.get(task.id).status == "running" and updates == []


def pool(root, execute, validator=lambda _: None):
    async def dev(task, progress):
        return "other work completed"
    runner = TaskRunner(str(root), dev, max_concurrent=1)
    dispatch = DispatchService(str(root), runner, execute=execute, validate_agent=validator)
    return dispatch, runner


@pytest.mark.asyncio
async def test_real_child_yields_before_later_tools_and_answer_rebuilds_under_same_pool(tmp_path, monkeypatch):
    from src.agents import delegation
    calls, prompts, events, agents = [], [], [], []

    async def before(args):
        calls.append("before")
        return "已定位 config.py"

    async def after(args):
        calls.append("after")
        return "must not execute after a question"

    monkeypatch.setattr(delegation, "build_read_tools", lambda root: [Tool("before", "read", {}, before, untrusted_source=True), Tool("after", "read", {}, after)])

    class LLM:
        async def chat(self, messages, **kwargs):
            prompts.append(str(messages))
            if len(prompts) == 1:
                return {"content": json.dumps([
                    {"tool": "before", "args": {}},
                    {"tool": "ask_task_question", "args": {"question": "环境？", "options": ["测试", "生产"], "context": "已定位 config.py"}},
                    {"tool": "after", "args": {}},
                ])}
            return {"content": "根据补充信息完成检查"}

    def factory(*args, **kwargs):
        agent = MainAgent(*args, **kwargs, on_tool_event=lambda kind, data: events.append((kind, data)))
        agents.append(agent)
        return agent

    async def execute(task, service):
        spawn, _ = build_delegation_executor(str(tmp_path), llm=LLM(), collaboration=service, agent_factory=factory)
        await spawn(json.dumps(task.to_dict(), ensure_ascii=False), task_id=task.id)

    dispatch, runner = pool(tmp_path, execute)
    task, _ = dispatch.submit(session="owner", request_id="ask", prompt="检查配置")
    await runner.join(task.id)
    task = dispatch.get("owner", task.id)
    assert task.status == "blocked" and runner.active_count == 0
    assert calls == ["before"] and len(prompts) == 1
    assert task.collaboration["tainted"] is True
    assert any(kind == "finish" and data["name"] == "ask_task_question" and data["status"] == "blocked" for kind, data in events)
    other = await runner.submit("独立工作")
    await asyncio.wait_for(runner.join(other.id), timeout=1)
    qid = task.collaboration["questions"][0]["id"]
    next_task, replayed = dispatch.answer_question("owner", task.id, 1, qid, "测试环境；不要访问生产")
    assert not replayed and next_task.collaboration["round"] == 2
    assert dispatch.answer_question("owner", task.id, 1, qid, "测试环境；不要访问生产")[1]
    await runner.join(task.id)
    assert dispatch.get("owner", task.id).status == "done" and len(prompts) == 2
    assert all(word in prompts[-1] for word in ["config.py", "测试环境", "检查配置"])
    assert len(agents) == 2 and agents[0] is not agents[1]


@pytest.mark.asyncio
async def test_restart_never_reruns_blocked_or_answered_queued_work(tmp_path):
    service, task, qid = blocked(tmp_path)
    executions = []

    async def execute(task, service):
        executions.append(task.id)
        service.start(task.id, task.collaboration["round"])
        service.finish(task.id, task.collaboration["round"], "done")

    dispatch, runner = pool(tmp_path, execute)
    runner.recover()
    assert dispatch.get("owner", task.id).status == "blocked" and executions == []
    # Simulate a crash after the durable answer but before queue registration.
    service.answer_question(task.id, 1, qid, "测试")
    runner.recover()
    assert dispatch.get("owner", task.id).status == "interrupted"
    assert dispatch.answer_question("owner", task.id, 1, qid, "测试")[1]
    await asyncio.sleep(0)
    assert executions == []


@pytest.mark.asyncio
async def test_http_answer_contract_auth_scope_and_replay(tmp_path, monkeypatch):
    from src.web.routers import delegations
    from src.web.server import app
    service, task, qid = blocked(tmp_path)

    async def execute(task, service):
        service.start(task.id, task.collaboration["round"])
        service.finish(task.id, task.collaboration["round"], "已检查")

    dispatch, runner = pool(tmp_path, execute)
    monkeypatch.setattr(delegations, "get_dispatch_service", lambda: dispatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "question-test-token")
    endpoint = f"/api/delegations/{task.id}/answer"
    body = {"session": "owner", "round": 1, "question_id": qid, "answer": "测试环境"}
    headers = {"Authorization": "Bearer question-test-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        assert (await client.post(endpoint, json=body)).status_code == 401
        for change in ({"round": True}, {"answer": False}, {"question_id": "../wrong"}, {"permissions": ["shell"]}):
            assert (await client.post(endpoint, json={**body, **change}, headers=headers)).status_code == 422
        assert (await client.post(endpoint, json={**body, "session": "other"}, headers=headers)).status_code == 409
        monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
        assert (await client.post(endpoint, json=body, headers=headers)).status_code == 409
        monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "scratch")
        response = await client.post(endpoint, json=body, headers=headers)
        assert response.status_code == 202 and response.json()["answered_question_id"] == qid
        assert response.json()["questions"][0]["status"] == "answered"
        await runner.join(task.id)
        replay = await client.post(endpoint, json=body, headers=headers)
        assert replay.status_code == 202 and replay.json()["replayed"] is True
        assert (await client.post(endpoint, json={**body, "answer": "生产"}, headers=headers)).status_code == 409
    assert service.get(task.id).collaboration["review"] == "pending"


@pytest.mark.asyncio
async def test_real_dispatch_rebuilds_contract_and_keeps_role_and_budget_boundaries(tmp_path, monkeypatch):
    import src.agents.dispatch as adapter
    from src.agents.capabilities import SENSITIVE_FILES
    from src.agents.taint import is_tainted
    agents, prompts = [], []

    class LLM:
        async def chat(self, messages, **kwargs):
            prompts.append(str(messages))
            assert is_tainted()
            if len(agents) < 3:
                return {"content": json.dumps({"tool": "ask_task_question", "args": {
                    "question": "请确认环境或版本", "options": [], "context": "证据 config.py:3"}})}
            assert "ask_task_question" not in agents[-1].tools
            return {"content": "依据回答完成调查"}

    def factory(*args, **kwargs):
        agent = MainAgent(*args, llm=LLM(), **{k: v for k, v in kwargs.items() if k != "llm"})
        agents.append(agent)
        return agent

    monkeypatch.setattr(adapter, "MainAgent", factory)
    monkeypatch.setenv("VORTOCODE_MAX_STEPS", "100")
    dispatch, runner = pool(tmp_path, lambda task, service: adapter.execute_dispatch(str(tmp_path), task, service))
    task, _ = dispatch.submit(session="owner", request_id="research", prompt="核对配置", max_steps=2, timeout_seconds=30)
    await runner.join(task.id)
    for number, answer in [(1, "测试环境"), (2, "v2.1")]:
        pending = dispatch.get("owner", task.id)
        assert pending.status == "blocked"
        qid = pending.collaboration["questions"][-1]["id"]
        dispatch.answer_question("owner", task.id, number, qid, answer)
        await runner.join(task.id)
    assert dispatch.get("owner", task.id).status == "done"
    assert all(text in prompts[-1] for text in ["测试环境", "v2.1", "config.py:3"])
    assert len(agents) == 3
    for agent in agents:
        assert agent.max_steps == agent.build_max_steps == 2
        assert SENSITIVE_FILES not in agent._capabilities.allowed
        assert not any(name.startswith("dev_") or name == "task" for name in agent.tools)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["storage", "role", "capacity", "active"])
async def test_answer_preconditions_do_not_consume_question_or_start_work(tmp_path, monkeypatch, boundary):
    service, task, qid = blocked(tmp_path)
    executions = []

    async def execute(*args):
        executions.append(True)

    dispatch, runner = pool(tmp_path, execute)
    if boundary == "storage":
        monkeypatch.setattr(TaskLedger, "save", lambda *args: False)
    elif boundary == "role":
        def forbidden(_):
            raise ValueError("role changed to dev")
        dispatch.validate_agent = forbidden
    elif boundary == "capacity":
        monkeypatch.setattr(TaskRunner, "active_count", property(lambda _: 100))
    else:
        monkeypatch.setattr(runner, "is_active", lambda _: True)
    with pytest.raises((ValueError, OSError)):
        dispatch.answer_question("owner", task.id, 1, qid, "测试")
    await asyncio.sleep(0)
    current = service.get(task.id)
    assert current.status == "blocked" and current.collaboration["questions"][0]["status"] == "open"
    assert executions == []


@pytest.mark.asyncio
async def test_cancel_answered_work_before_first_timeslice_and_replay(tmp_path):
    _, task, qid = blocked(tmp_path)
    executions = []

    async def execute(*args):
        executions.append(True)

    dispatch, runner = pool(tmp_path, execute)
    dispatch.answer_question("owner", task.id, 1, qid, "测试")
    cancelled = await dispatch.cancel("owner", task.id, 2)
    assert cancelled.status == "cancelled" and runner.active_count == 0
    replay, repeated = dispatch.answer_question("owner", task.id, 1, qid, "测试")
    assert repeated and replay.status == "cancelled" and executions == []


@pytest.mark.asyncio
async def test_native_question_yields_and_permission_denial_never_writes(tmp_path):
    from src.agents.permissions import Permissions
    from src.agents.task_questions import build_question_tool
    from src.agents.tool import ToolTurnYield
    service = CollaborationService(str(tmp_path), "sid-owner")
    task = service.create("研究", dispatch={"source": "api"})
    task = service.start(task.id, 1)
    tool = build_question_tool(service, task)
    args = {"question": "环境？", "options": [], "context": "已有证据"}

    class LLM:
        calls = 0

        async def chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            assert tools is not None
            return {"content": "", "tool_calls": [{"id": "question-call", "name": tool.name, "arguments": json.dumps(args)}]}

    denied = MainAgent([tool], permissions=Permissions([(tool.name, None)]))
    result = await denied._run_tool(tool.name, args, "plan", lambda _: None)
    assert "权限拦截" in result and service.get(task.id).status == "running"
    llm = LLM()
    agent = MainAgent([tool], llm=llm, native=True)
    with pytest.raises(ToolTurnYield):
        await agent.run_turn("请研究")
    assert llm.calls == 1 and service.get(task.id).status == "blocked"
