"""Explicit dependency release, durable inputs and the original shared pool."""
import asyncio
import hashlib
import json

import httpx
import pytest

from src.gateway.collaboration import CollaborationConflict
from src.gateway.dispatch import DispatchBusy, DispatchService
from src.gateway.handoffs import CompletionInbox, revision
from src.gateway.task_dependencies import dependency_view, normalize_requirements
from src.gateway.tasks import TaskLedger, TaskRunner


def fixture(root, *, execute=None):
    calls = []

    async def worker(task, progress):
        return "dev done"

    async def run(task, collaboration):
        calls.append(task.to_dict())
        collaboration.start(task.id, task.collaboration["round"])
        collaboration.finish(task.id, task.collaboration["round"], "已提供配置入口 app.py:1 的证据", tainted=True)

    runner = TaskRunner(str(root), worker, max_concurrent=1)
    service = DispatchService(str(root), runner, execute=execute or run, validate_agent=lambda _: None)
    return service, runner, calls


async def source(service, runner, request_id="source", *, accept=False):
    task, _ = service.submit(session="owner", request_id=request_id, prompt="研究配置入口")
    await runner.join(task.id)
    if accept:
        task = service.review("owner", task.id, 1, "accept", "已核对 app.py:1 证据")
    return task


def submit(service, *parents, request_id="child"):
    return service.submit(session="owner", request_id=request_id, prompt="基于前置结果研究迁移步骤",
                          max_steps=4, timeout_seconds=30,
                          depends_on=[{"task_id": p.id, "round": p.collaboration["round"]} for p in parents])


@pytest.mark.asyncio
async def test_waiting_does_not_hold_slot_and_requires_all_acceptance(tmp_path):
    service, runner, calls = fixture(tmp_path)
    a = await source(service, runner, "a", accept=True)
    b = await source(service, runner, "b")
    task, replayed = submit(service, b, a)
    assert not replayed and task.status == "waiting" and runner.active_count == 0
    assert service.submit(session="owner", request_id="child", prompt=task.prompt, max_steps=4, timeout_seconds=30,
                          depends_on=[{"task_id": a.id, "round": 1}, {"task_id": b.id, "round": 1}])[1]
    assert CompletionInbox(str(tmp_path), "sid-owner").pending() and all(item["task_id"] != task.id for item in CompletionInbox(str(tmp_path), "sid-owner").pending())
    with pytest.raises(CollaborationConflict, match="尚未完成并验收"):
        service.release("owner", task.id, 1)
    assert runner.active_count == 0 and len(calls) == 2
    service.review("owner", b.id, 1, "accept", "证据已核对")
    assert dependency_view(service.get("owner", task.id), service.collaboration("owner").ledger)["ready"]
    released, replayed = service.release("owner", task.id, 1)
    assert not replayed and released.status == "queued" and released.id == task.id
    assert released.dependencies["inputs"][0]["revision"] in {revision(a), revision(b)}
    assert released.collaboration["tainted"] and len(released.dependencies["inputs"]) == 2
    assert service.release("owner", task.id, 1)[1]
    await runner.join(task.id)
    assert len(calls) == 3 and service.get("owner", task.id).status == "done"
    assert service.release("owner", task.id, 1)[0].status == "done" and len(calls) == 3
    assert any(item["task_id"] == task.id for item in CompletionInbox(str(tmp_path), "sid-owner").pending())
    assert not CompletionInbox(str(tmp_path), "sid-owner").ledger.load(a.id).handoff_receipt


@pytest.mark.parametrize("change", ["failed", "cancelled", "interrupted", "rework", "round", "owner", "missing", "corrupt", "bad_status", "bad_review"])
@pytest.mark.asyncio
async def test_front_failure_propagates_on_explicit_release_without_execution(tmp_path, change):
    service, runner, calls = fixture(tmp_path)
    parent = await source(service, runner)
    task, _ = submit(service, parent)
    if change in {"failed", "cancelled", "interrupted"}:
        parent.status = change
    elif change == "rework":
        parent.collaboration["review"] = "rework_requested"
    elif change == "round":
        parent.collaboration["round"] = 2
    elif change == "owner":
        parent.owner_session = "sid-other"
    elif change == "missing":
        service.collaboration("owner").ledger._path(parent.id).unlink()
    elif change == "bad_status":
        parent.status = []
    elif change == "bad_review":
        parent.collaboration["review"] = []
    else:
        parent.collaboration["tainted"] = "false"
    if change != "missing":
        assert runner.ledger.save(parent)
    failed, replayed = service.release("owner", task.id, 1)
    assert failed.status == "failed" and failed.error and not replayed
    assert failed.dependencies["resolution"] == "failed" and failed.dependencies["inputs"] == []
    assert runner.active_count == 0 and len(calls) == 1
    assert service.release("owner", task.id, 1)[1]
    with pytest.raises(CollaborationConflict):
        service.followup("owner", task.id, 1, "直接重试")
    if change == "failed":
        parent.status = "done"
        parent.collaboration["review"] = "accepted"
        runner.ledger.save(parent)
        view = dependency_view(failed, runner.ledger)
        assert view["failure"] == failed.error and not view["ready"] and not view["pending"]
        assert "新的依赖合同" in view["next_action"]
        assert service.release("owner", task.id, 1)[0].status == "failed" and len(calls) == 1


@pytest.mark.parametrize("change", ["result", "review", "owner", "input", "round", "missing_contract", "bad_resolution"])
@pytest.mark.asyncio
async def test_changed_consumed_version_is_rejected_before_model(tmp_path, change):
    service, runner, calls = fixture(tmp_path)
    parent = await source(service, runner, accept=True)
    task, _ = submit(service, parent)
    service.release("owner", task.id, 1)
    if change in {"input", "missing_contract", "bad_resolution"}:
        current = runner.get(task.id)
        if change == "missing_contract":
            current.dependencies = {}
        elif change == "bad_resolution":
            current.dependencies["resolution"] = []
        else:
            current.dependencies["inputs"][0]["result"] = "替换上下文"
        runner.ledger.save(current)
    else:
        if change == "result":
            parent.result = "不同结果"
        elif change == "review":
            parent.collaboration["review"] = "pending"
        elif change == "owner":
            parent.owner_session = "sid-other"
        else:
            parent.collaboration["round"] = 2
        runner.ledger.save(parent)
    await runner.join(task.id)
    final = runner.get(task.id)
    assert final.status == "failed" and final.error and len(calls) == 1
    if change in {"missing_contract", "bad_resolution"}:
        assert dependency_view(final, runner.ledger)["failure"]
        from src.gateway.worktree_sessions import task_session_view
        assert task_session_view(str(tmp_path), final, worktrees=[])["dependencies"]["failure"]
        with pytest.raises(CollaborationConflict):
            service.release("owner", task.id, 1)
    else:
        assert "前置结果版本" in final.error
        assert service.release("owner", task.id, 1)[1]  # An old release never creates another execution.


@pytest.mark.parametrize("boundary", ["owner", "round", "capacity", "storage", "role", "active", "scope"])
@pytest.mark.asyncio
async def test_release_failure_preserves_waiting_contract(tmp_path, monkeypatch, boundary):
    service, runner, calls = fixture(tmp_path)
    parent = await source(service, runner, accept=True)
    task, _ = submit(service, parent)
    owner, number, error = "owner", 1, CollaborationConflict
    if boundary == "owner":
        owner = "other"
    elif boundary == "round":
        number = True
    elif boundary == "capacity":
        monkeypatch.setattr(TaskRunner, "active_count", property(lambda _: 100))
        error = DispatchBusy
    elif boundary == "storage":
        monkeypatch.setattr(TaskLedger, "save", lambda *args: False)
        error = OSError
    elif boundary == "role":
        def deny(_):
            raise ValueError("role is no longer read-only")
        service.validate_agent = deny
        error = ValueError
    elif boundary == "active":
        monkeypatch.setattr(runner, "is_active", lambda _: True)
    else:
        task.dependencies["owner_session"] = "sid-other"
        runner.ledger.save(task)
    before = runner.get(task.id).to_dict()
    with pytest.raises(error):
        service.release(owner, task.id, number)
    assert runner.get(task.id).to_dict() == before and len(calls) == 1


@pytest.mark.asyncio
async def test_restart_retains_waiting_and_never_replays_released_queue(tmp_path, monkeypatch):
    service, runner, calls = fixture(tmp_path)
    parent = await source(service, runner, accept=True)
    task, _ = submit(service, parent)
    runner.recover()
    assert runner.get(task.id).status == "waiting" and runner.active_count == 0
    with monkeypatch.context() as patch:
        patch.setattr(service, "_schedule", lambda *args: None)
        service.release("owner", task.id, 1)
    runner.recover()
    assert service.release("owner", task.id, 1)[0].status == "interrupted" and len(calls) == 1
    original_inputs = runner.get(task.id).dependencies["inputs"]
    service.followup("owner", task.id, 1, "核对中断证据后继续原研究")
    await runner.join(task.id)
    assert runner.get(task.id).collaboration["round"] == 2
    assert runner.get(task.id).dependencies["inputs"] == original_inputs and len(calls) == 2


@pytest.mark.asyncio
async def test_cancel_waiting_does_not_cancel_parent_or_release_slot(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()

    async def execute(task, collaboration):
        collaboration.start(task.id, 1)
        started.set()
        await release.wait()
        collaboration.finish(task.id, 1, "证据")

    service, runner, _ = fixture(tmp_path, execute=execute)
    parent, _ = service.submit(session="owner", request_id="parent", prompt="前置调查")
    await started.wait()
    child, _ = submit(service, parent)
    assert runner.active_count == 1 and not runner.is_active(child.id)
    assert (await service.cancel("owner", child.id, 1)).status == "cancelled"
    assert runner.get(parent.id).status == "running" and runner.active_count == 1
    release.set()
    await runner.join(parent.id)
    with pytest.raises(CollaborationConflict):
        service.release("owner", child.id, 1)


@pytest.mark.parametrize("refs", [None, {}, [{"task_id": "../task-a", "round": 1}], [{"task_id": "task-a", "round": True}],
                                  [{"task_id": "task-a", "round": 4}], [{"task_id": "task-a", "round": 1, "permission": True}],
                                  [{"task_id": "task-a", "round": 1}] * 2, [{"task_id": f"task-{i}", "round": 1} for i in range(9)]])
def test_invalid_dependency_contract_rejected(refs):
    with pytest.raises(CollaborationConflict):
        normalize_requirements(refs)


@pytest.mark.asyncio
async def test_graph_limits_cycles_corrupt_identity_and_waiting_capacity(tmp_path, monkeypatch):
    service, runner, calls = fixture(tmp_path)
    parent = await source(service, runner, accept=True)
    self_id = "task-dispatch-" + hashlib.sha256(json.dumps(["sid-owner", "self"]).encode()).hexdigest()[:32]
    with pytest.raises(CollaborationConflict, match="形成环"):
        service.submit(session="owner", request_id="self", prompt="cycle", depends_on=[{"task_id": self_id, "round": 1}])
    ledger = runner.ledger
    with monkeypatch.context() as patch:
        patch.setattr(TaskLedger, "list", lambda *args, **kwargs: [type("Waiting", (), {"status": "waiting"})()] * 100)
        with pytest.raises(DispatchBusy):
            submit(service, parent)
    previous = parent
    for index in range(8):
        current, _ = submit(service, previous, request_id=f"chain-{index}")
        previous = current
    with pytest.raises(CollaborationConflict, match="深度"):
        submit(service, previous, request_id="too-deep")
    corrupt_id = "task-dispatch-" + hashlib.sha256(json.dumps(["sid-owner", "corrupt"]).encode()).hexdigest()[:32]
    ledger._path(corrupt_id).write_text("broken")
    with pytest.raises(CollaborationConflict, match="损坏"):
        submit(service, parent, request_id="corrupt")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_real_read_agent_consumes_bounded_untrusted_inputs_and_can_ask(tmp_path, monkeypatch):
    from src.agents import dispatch as adapter
    from src.agents.agent_loop import MainAgent
    service, runner, calls = fixture(tmp_path)
    parent = await source(service, runner, accept=True)
    parent.result = "配置入口 app.py:1\n" + "证据" * 2000
    runner.ledger.save(parent)
    child, _ = submit(service, parent)
    prompts, agents = [], []

    class LLM:
        async def chat(self, messages, **kwargs):
            prompts.append(str(messages))
            assert "dependency_inputs" in prompts[-1] and "app.py:1" in prompts[-1] and parent.id in prompts[-1]
            if len(prompts) == 1:
                return {"content": json.dumps({"tool": "ask_task_question", "args": {"question": "迁移目标版本？", "context": "已读取前置配置证据"}})}
            assert "v2" in prompts[-1]
            return {"content": "基于前置证据与 v2 完成迁移建议"}

    def factory(*args, **kwargs):
        agent = MainAgent(*args, **{**kwargs, "llm": LLM()})
        agents.append(agent)
        return agent

    monkeypatch.setattr(adapter, "MainAgent", factory)
    monkeypatch.setenv("VORTOCODE_MAX_STEPS", "999")
    service.execute = lambda task, collaboration: adapter.execute_dispatch(str(tmp_path), task, collaboration)
    service.release("owner", child.id, 1)
    await runner.join(child.id)
    waiting = runner.get(child.id)
    assert waiting.status == "blocked" and runner.active_count == 0
    assert waiting.dependencies["inputs"][0]["truncated"] and len(waiting.dependencies["inputs"][0]["result"]) <= 2000
    qid = waiting.collaboration["questions"][0]["id"]
    service.answer_question("owner", child.id, 1, qid, "v2")
    await runner.join(child.id)
    assert runner.get(child.id).status == "done" and len(prompts) == 2
    assert all(agent.max_steps <= 4 and agent._untrusted_input for agent in agents)
    assert len(calls) == 1


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.asyncio
async def test_parent_release_tool_uses_live_owner_and_does_not_wait(tmp_path, native):
    from src.agents.agent_loop import MainAgent
    from src.agents.main_agent import build_research_tools
    service, runner, calls = fixture(tmp_path)
    parent = await source(service, runner, accept=True)
    child, _ = submit(service, parent)
    owner = ["sid-owner"]
    collaboration = service.collaboration("owner")
    collaboration._owner = lambda: owner[0]
    tools = build_research_tools(str(tmp_path), collaboration=collaboration, release_task_dependencies=service.release)
    by = {tool.name: tool for tool in tools}
    assert "task_release" not in {tool.name for tool in build_research_tools(str(tmp_path), collaboration=collaboration)}
    owner[0] = "sid-other"
    with pytest.raises(CollaborationConflict):
        await by["task_release"].handler({"task_id": child.id, "round": 1})
    owner[0] = "sid-owner"
    gate = asyncio.Event()

    async def execute(task, collaboration):
        await gate.wait()
        collaboration.start(task.id, 1)
        collaboration.finish(task.id, 1, "依赖后续结果")

    service.execute = execute

    class LLM:
        calls = 0
        async def chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                name, args = "task_status", {"task_id": child.id}
            elif self.calls == 2:
                assert parent.id in str(messages) and "waiting" in str(messages)
                name, args = "task_release", {"task_id": child.id, "round": 1}
            else:
                return {"content": "已固定前置结果，后台执行，尚待验收"}
            if native:
                return {"content": "", "tool_calls": [{"id": f"call-{name}", "name": name, "arguments": json.dumps(args)}]}
            return {"content": json.dumps({"tool": name, "args": args})}

    parent_agent = MainAgent(tools, llm=LLM(), native=native, max_steps=3)
    result = await parent_agent.run_turn("已核对前置结果，请推进后续任务", mode="plan")
    assert "尚待验收" in result and runner.active_count == 1 and not gate.is_set()
    assert json.loads(await by["task_release"].handler({"task_id": child.id, "round": 1}))["replayed"]
    gate.set()
    await runner.join(child.id)


@pytest.mark.asyncio
async def test_http_dependency_submission_release_auth_and_view(tmp_path, monkeypatch):
    from src.web.routers import tasks
    from src.web.server import app
    service, runner, calls = fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tasks, "_RUNNER", runner)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "local-dependency-test")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    parent = await source(service, runner, accept=True)
    headers = {"Authorization": "Bearer local-dependency-test"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        body = {"session": "owner", "request_id": "http-child", "prompt": "基于结果继续研究", "depends_on": [{"task_id": parent.id, "round": 1}]}
        assert (await client.post("/api/delegations", json=body)).status_code == 401
        response = await client.post("/api/delegations", json=body, headers=headers)
        assert response.status_code == 202 and response.json()["status"] == "waiting" and response.json()["dependencies"]["ready"]
        child = response.json()["id"]
        assert runner.active_count == 0
        path = f"/api/delegations/{child}/release"
        assert (await client.post(path, json={"session": "other", "round": 1}, headers=headers)).status_code == 409
        assert (await client.post(path, json={"session": "owner", "round": 1, "grant": True}, headers=headers)).status_code == 422
        first = await client.post(path, json={"session": "owner", "round": 1}, headers=headers)
        assert first.status_code == 202 and not first.json()["replayed"]
        await runner.join(child)
        replay = await client.post(path, json={"session": "owner", "round": 1}, headers=headers)
        assert replay.status_code == 202 and replay.json()["replayed"]
        assert replay.json()["dependencies"]["inputs"][0]["revision"] == revision(parent)
        assert (await client.get("/api/delegations/capabilities", headers=headers)).json()["dependencies"]["automatic_release"] is False


def test_waiting_projects_as_attention_without_completion(tmp_path):
    from src.gateway.decisions import build_decision_queue
    from src.gateway.runtime_inbox import build_runtime_inbox
    from src.gateway.worktree_sessions import task_session_view
    service, runner, calls = fixture(tmp_path)
    parent = service.collaboration("owner").create("前置", dispatch={"source": "api", "max_steps": 4, "timeout_seconds": 30})
    child, _ = submit(service, parent)
    assert build_decision_queue(tasks=[child])[0]["title"] == "任务等待前置结果验收"
    inbox = build_runtime_inbox(scope="project", tasks=[child])
    assert inbox["counts"]["tasks_active"] == 0 and inbox["counts"]["tasks_attention"] == 1
    view = task_session_view(str(tmp_path), child, worktrees=[])
    assert not view["can_pause"] and view["handoff"]["handling"] is None


@pytest.mark.asyncio
async def test_transitive_changed_result_blocks_consumption(tmp_path):
    service, runner, calls = fixture(tmp_path)
    a = await source(service, runner, accept=True)
    b, _ = submit(service, a, request_id="b")
    service.release("owner", b.id, 1)
    await runner.join(b.id)
    b = service.review("owner", b.id, 1, "accept", "已核对派生研究结果")
    c, _ = submit(service, b, request_id="c")
    a.result = "祖先结果已替换"
    runner.ledger.save(a)
    failed, _ = service.release("owner", c.id, 1)
    assert failed.status == "failed" and "所消费的依赖结果版本" in failed.error and len(calls) == 2


@pytest.mark.asyncio
async def test_graph_task_limit_and_non_service_parent_are_rejected(tmp_path):
    service, runner, calls = fixture(tmp_path)
    parents = [await source(service, runner, f"s-{i}", accept=True) for i in range(32)]
    groups = [submit(service, *parents[i:i+4], request_id=f"g-{i}")[0] for i in range(0, 32, 4)]
    with pytest.raises(CollaborationConflict, match="32 个"):
        submit(service, *groups, request_id="over-graph")
    raw = service.collaboration("owner").create("同回合研究")
    with pytest.raises(CollaborationConflict, match="只读下派服务"):
        submit(service, raw, request_id="raw")
    dev = runner.ledger.create("dev", "开发", owner_session="sid-owner")
    dev.collaboration = {"round": 1}
    with pytest.raises(CollaborationConflict, match="只读下派服务"):
        submit(service, dev, request_id="dev")
