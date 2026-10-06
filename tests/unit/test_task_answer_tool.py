"""Foreground answers use the existing durable service and shared background pool."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.agents.agent_loop import MainAgent
from src.agents.main_agent import build_research_tools
from src.agents.permissions import Permissions
from src.gateway.collaboration import CollaborationConflict, CollaborationService
from src.gateway.dispatch import DispatchService
from src.gateway.tasks import TaskLedger, TaskRunner


def setup(root, owner="sid-owner", execute=None):
    service = CollaborationService(str(root), owner)
    task = service.create("核对配置", dispatch={"source": "api", "max_steps": 2, "timeout_seconds": 30})
    service.start(task.id, 1)
    task = service.ask_question(task.id, 1, "应检查哪个环境？", context="config.py:3 已定位配置", tainted=True)
    qid = task.collaboration["questions"][0]["id"]
    executions = []

    async def worker(task, progress):
        return "dev completed"

    async def research(task, collaboration):
        executions.append(task.id)
        collaboration.start(task.id, task.collaboration["round"])
        collaboration.finish(task.id, task.collaboration["round"], "核对完成")

    runner = TaskRunner(str(root), worker, max_concurrent=1)
    dispatch = DispatchService(str(root), runner, execute=execute or research, validate_agent=lambda _: None)
    by = {tool.name: tool for tool in build_research_tools(
        str(root), collaboration=service, answer_task_question=dispatch.answer_question)}
    args = {"task_id": task.id, "round": 1, "question_id": qid, "answer": "用户已明确选择测试环境"}
    return service, task, dispatch, runner, by, args, executions


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_parent_reads_question_and_answers_without_waiting_for_background(tmp_path, native):
    release = asyncio.Event()
    prompts = []

    async def execute(task, service):
        service.start(task.id, task.collaboration["round"])
        prompts.append(task.to_dict())
        await release.wait()
        service.finish(task.id, task.collaboration["round"], "已按测试环境核对配置")

    service, task, dispatch, runner, by, args, _ = setup(tmp_path, execute=execute)

    class ParentLLM:
        calls = 0

        async def chat(self, messages, tools=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                name, arguments = "task_status", {"task_id": task.id}
            elif self.calls == 2:
                assert all(text in str(messages) for text in ["应检查哪个环境", "config.py:3", args["question_id"]])
                name, arguments = "task_answer", args
            else:
                assert "answered_question_id" in str(messages) and "queued" in str(messages)
                assert service.get(task.id).collaboration["review"] == "not_submitted"
                return {"content": "已补充测试环境信息，后台继续核对，尚待结果与验收。"}
            if native:
                assert tools is not None
                return {"content": "", "tool_calls": [{"id": f"call-{self.calls}", "name": name,
                                                            "arguments": json.dumps(arguments)}]}
            return {"content": json.dumps({"tool": name, "args": arguments})}

    parent = MainAgent(list(by.values()), llm=ParentLLM(), native=native)
    try:
        result = await asyncio.wait_for(parent.run_turn("只检查测试环境，继续研究", mode="plan"), timeout=2)
        assert "尚待结果与验收" in result and runner.active_count == 1
        replay = json.loads(await by["task_answer"].handler(args))
        assert replay["replayed"] and replay["round"] == 2
        assert replay["status"] in {"queued", "running"}
    finally:
        release.set()
        await runner.join(task.id)
    assert len(prompts) == 1 and service.get(task.id).collaboration["review"] == "pending"
    assert prompts[0]["collaboration"]["questions"][0]["answer"] == args["answer"]
    terminal_replay = json.loads(await by["task_answer"].handler(args))
    assert terminal_replay["replayed"] and terminal_replay["status"] == "done"
    await asyncio.sleep(0)
    assert len(prompts) == 1 and runner.active_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["owner", "round", "question", "expired", "cancelled", "storage",
                                     "capacity", "active", "role", "extra_owner", "boolean_round", "empty_answer"])
async def test_model_answer_preserves_service_preconditions(tmp_path, monkeypatch, boundary):
    service, task, dispatch, runner, by, args, executions = setup(tmp_path)
    if boundary == "owner":
        service._owner = "sid-other"
    elif boundary == "round":
        args["round"] = 2
    elif boundary == "question":
        args["question_id"] = "question-other"
    elif boundary == "expired":
        task.collaboration["questions"][0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        service.ledger.save(task)
    elif boundary == "cancelled":
        service.abort(task.id, 1, cancelled=True)
    elif boundary == "storage":
        monkeypatch.setattr(TaskLedger, "save", lambda *args: False)
    elif boundary == "capacity":
        monkeypatch.setattr(TaskRunner, "active_count", property(lambda _: 100))
    elif boundary == "active":
        monkeypatch.setattr(runner, "is_active", lambda _: True)
    elif boundary == "role":
        def deny(_):
            raise ValueError("role changed to dev")
        dispatch.validate_agent = deny
    elif boundary == "extra_owner":
        args["session"] = "sid-owner"
    elif boundary == "boolean_round":
        args["round"] = True
    else:
        args["answer"] = " "
    before = service.ledger.load(task.id).to_dict()
    with pytest.raises((ValueError, OSError)):
        await by["task_answer"].handler(args)
    await asyncio.sleep(0)
    assert service.ledger.load(task.id).to_dict() == before and executions == []


@pytest.mark.asyncio
async def test_dynamic_owner_is_resolved_each_time_and_inline_tasks_are_rejected(tmp_path):
    _, task, dispatch, runner, _, args, executions = setup(tmp_path)
    owner = ["sid-other"]
    service = CollaborationService(str(tmp_path), lambda: owner[0])
    tool = next(t for t in build_research_tools(str(tmp_path), collaboration=service,
                    answer_task_question=dispatch.answer_question) if t.name == "task_answer")
    with pytest.raises(CollaborationConflict):
        await tool.handler(args)
    owner[0] = "sid-owner"
    assert not json.loads(await tool.handler(args))["replayed"]
    await runner.join(task.id)
    owner[0] = "sid-other"
    with pytest.raises(CollaborationConflict):
        await tool.handler(args)
    owner[0] = "sid-owner"
    inline = service.create("普通回合内委派")
    with pytest.raises(CollaborationConflict, match="入口"):
        await tool.handler({**args, "task_id": inline.id})
    assert len(executions) == 1


@pytest.mark.asyncio
async def test_permissions_and_capability_gate_precede_model_answer(tmp_path):
    service, task, _, _, by, args, executions = setup(tmp_path)
    events = []
    agent = MainAgent([by["task_answer"]], permissions=Permissions([("task_answer", None)]),
                      on_tool_event=lambda kind, data: events.append((kind, data)))
    assert "权限拦截" in await agent._run_tool("task_answer", args, "plan", lambda _: None)

    class DeniedCapability:
        def denied(self, *args, **kwargs):
            return "answer disallowed"

    agent._capabilities = DeniedCapability()
    assert "能力拦截" in await agent._run_tool("task_answer", args, "build", lambda _: None)
    assert service.get(task.id).status == "blocked" and executions == []
    assert all(data["status"] == "blocked" for kind, data in events if kind == "finish")


def test_answer_tool_is_only_exposed_when_injected_and_inside_workspace(tmp_path):
    from src.gateway.agent_session import build_session
    service = CollaborationService(str(tmp_path), "sid-owner")
    assert "task_answer" not in {t.name for t in build_research_tools(str(tmp_path), collaboration=service)}
    callback = lambda *args: None
    for scope in ["project", "scratch", "general"]:
        agent = build_session(str(tmp_path), kind="web", workspace_scope=scope,
                              task_owner="sid-owner", answer_task_question=callback)
        assert ("task_answer" in agent.tools) == (scope != "general")
        if scope != "general":
            schema = next(item for item in agent._tools_schema() if item["function"]["name"] == "task_answer")
            properties = schema["function"]["parameters"]["properties"]
            assert properties["round"]["type"] == "integer" and properties["round"]["maximum"] == 2
            assert "session" not in properties and properties["answer"]["maxLength"] == 2000


@pytest.mark.asyncio
async def test_web_assembly_and_http_share_runner_updates_and_handoff(tmp_path, monkeypatch):
    from src.agents import dispatch as adapter
    from src.web import task_dispatch, task_events
    from src.web.routers import delegations, realtime, tasks
    from src.web.server import app
    service, task, _, runner, _, args, executions = setup(tmp_path)
    updates, handoffs = [], []

    async def execute(root, task, collaboration):
        assert root == str(tmp_path)
        executions.append(task.id)
        collaboration.start(task.id, task.collaboration["round"])
        collaboration.finish(task.id, task.collaboration["round"], "shared runtime result")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "answer-test-token")
    monkeypatch.setattr(tasks, "_RUNNER", runner)
    monkeypatch.setattr(adapter, "execute_dispatch", execute)
    monkeypatch.setattr(task_events, "broadcast_task_update", updates.append)
    monkeypatch.setattr(task_events, "publish_task_handoff", lambda owner, view: handoffs.append((owner, view)))
    assert delegations.get_dispatch_service is task_dispatch.get_dispatch_service
    assert task_dispatch.get_dispatch_service().runner is runner
    agent = realtime._new_agent()
    agent._web_audit_holder["session"] = "sid-other"
    with pytest.raises(CollaborationConflict):
        await agent.tools["task_answer"].handler(args)
    agent._web_audit_holder["session"] = "sid-owner"
    answer = json.loads(await agent.tools["task_answer"].handler(args))
    assert answer["status"] == "queued" and handoffs == []
    await runner.join(task.id)
    assert [view["status"] for view in updates] == ["queued", "running", "done"]
    assert len(handoffs) == 1 and handoffs[0][0] == "sid-owner"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local",
                                 headers={"Authorization": "Bearer answer-test-token"}) as client:
        response = await client.post(f"/api/delegations/{task.id}/answer", json={"session": "owner", **{k: v for k, v in args.items() if k != "task_id"}})
    assert response.status_code == 202 and response.json()["replayed"]
    assert len(executions) == 1 and len(handoffs) == 1
    assert service.get(task.id).collaboration["review"] == "pending"


@pytest.mark.asyncio
async def test_real_service_child_asks_parent_answers_and_bounded_child_resumes(tmp_path, monkeypatch):
    from src.agents import dispatch as adapter
    from src.web import task_dispatch
    from src.web.routers import realtime, tasks
    from src.agents.taint import is_tainted
    prompts, children = [], []

    class ChildLLM:
        async def chat(self, messages, **kwargs):
            prompts.append(str(messages))
            assert is_tainted()
            if len(prompts) == 1:
                return {"content": json.dumps({"tool": "ask_task_question", "args": {
                    "question": "检查哪个环境？", "context": "已定位 config.py:3"}})}
            assert all(text in str(messages) for text in ["用户已明确选择测试环境", "config.py:3"])
            return {"content": "已根据测试环境完成调查"}

    def child_factory(*args, **kwargs):
        agent = MainAgent(*args, **{**kwargs, "llm": ChildLLM(), "native": False})
        children.append(agent)
        return agent

    async def worker(task, progress):
        return "dev completed"

    runner = TaskRunner(str(tmp_path), worker, max_concurrent=1)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    monkeypatch.setenv("VORTOCODE_MAX_STEPS", "100")
    monkeypatch.setattr(tasks, "_RUNNER", runner)
    monkeypatch.setattr(adapter, "MainAgent", child_factory)
    dispatch = task_dispatch.get_dispatch_service()
    task, _ = dispatch.submit(session="owner", request_id="full-loop", prompt="研究配置", max_steps=2)
    await runner.join(task.id)
    assert dispatch.get("owner", task.id).status == "blocked" and runner.active_count == 0
    parent = realtime._new_agent()
    parent._web_audit_holder["session"] = "sid-owner"
    task = json.loads(await parent.tools["task_status"].handler({"task_id": task.id}))["task"]
    args = {"task_id": task["task_id"], "round": task["questions"][0]["round"],
            "question_id": task["questions"][0]["id"], "answer": "用户已明确选择测试环境"}
    assert json.loads(await parent.tools["task_answer"].handler(args))["status"] == "queued"
    await runner.join(task["task_id"])
    finished = dispatch.get("owner", task["task_id"])
    assert finished.status == "done" and finished.collaboration["review"] == "pending"
    assert len(prompts) == len(children) == 2
    assert all(child.max_steps == child.build_max_steps == 2 for child in children)
    assert all("task_answer" not in child.tools and "dev_isolated" not in child.tools for child in children)


def test_next_user_turn_hint_is_owner_scoped_and_does_not_read_question_text(tmp_path):
    from src.web.task_dispatch import task_turn_hint
    service, task, _, runner, _, _, executions = setup(tmp_path)
    hint = task_turn_hint(str(tmp_path), "sid-owner", {"task_answer": object()})
    assert "task_status" in hint and "task_answer" in hint and "未知偏好" in hint
    assert "config.py" not in hint and "应检查哪个环境" not in hint
    assert task_turn_hint(str(tmp_path), "sid-other", {"task_answer": object()}) == ""
    assert task_turn_hint(str(tmp_path), "sid-owner", {}) == ""
    task.collaboration["questions"][0]["expires_at"] = "invalid"
    service.ledger.save(task)
    assert task_turn_hint(str(tmp_path), "sid-owner", {"task_answer": object()}) == ""
    assert runner.active_count == 0 and executions == []
