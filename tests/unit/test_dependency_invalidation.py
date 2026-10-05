"""In-flight checkpoints, durable invalidation and explicit stop coordination."""
import asyncio
import json

import httpx
import pytest

from src.gateway.collaboration import CollaborationConflict
from src.gateway.dispatch import DispatchService
from src.gateway.handoffs import CompletionInbox, revision
from src.gateway.task_chain_budget import budget_view
from src.gateway.task_dependencies import dependency_view, validate_consumed
from src.gateway.tasks import TaskLedger, TaskRunner


async def setup(root, execute=None):
    calls = []

    async def worker(task, _):
        return "unused"

    async def run(task, collaboration):
        if task.dependencies and execute is not None:
            await execute(task, collaboration)
            return
        calls.append(task.id)
        collaboration.start(task.id, task.collaboration["round"])
        collaboration.finish(task.id, task.collaboration["round"], "config.py:1 的只读证据")

    runner = TaskRunner(str(root), worker, max_concurrent=1)
    service = DispatchService(str(root), runner, execute=run, validate_agent=lambda _: None)
    parent, _ = service.submit(session="owner", request_id="source", prompt="研究前置",
                               max_steps=4, timeout_seconds=30,
                               chain_limits={"tasks": 4, "rounds": 4, "steps": 16, "timeout_seconds": 120})
    await runner.join(parent.id)
    parent = service.review("owner", parent.id, 1, "accept", "已核对文件证据")
    child, _ = service.submit(session="owner", request_id="child", prompt="根据证据研究差异",
                              max_steps=4, timeout_seconds=30, depends_on=[{"task_id": parent.id, "round": 1}])
    return service, runner, parent, child, calls


def change_source(runner, parent, change="result"):
    current = runner.get(parent.id)
    if change == "result":
        current.result = "前置证据已发生变化"
    elif change == "review":
        current.collaboration["review"] = "rework_requested"
    elif change == "owner":
        current.owner_session = "sid-other"
    elif change == "round":
        current.collaboration["round"] = 2
    elif change == "missing":
        runner.ledger._path(parent.id).unlink()
        return
    assert runner.ledger.save(current)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("boundary", ["model", "tool"])
@pytest.mark.asyncio
async def test_real_agent_rejects_changes_during_request_or_tool(tmp_path, monkeypatch, native, boundary):
    from src.agents.agent_loop import MainAgent
    from src.agents.dispatch import execute_dispatch
    from src.agents.tool import Tool
    import src.agents.dispatch as adapter
    import src.agents.delegation as delegation
    reached, finish = asyncio.Event(), asyncio.Event()
    tool_calls, model_calls = [], []

    async def read(args):
        tool_calls.append(args)
        reached.set()
        await finish.wait()
        return "迟到工具证据"

    class LLM:
        async def chat(self, messages, **kwargs):
            model_calls.append(messages)
            if boundary == "model":
                reached.set()
                await finish.wait()
                return {"content": "迟到成功结论"}
            if native:
                return {"content": "", "tool_calls": [{"id": "call-read", "name": "read_probe", "arguments": "{}"}]}
            return {"content": json.dumps({"tool": "read_probe", "args": {}})}

    def factory(*args, **kwargs):
        return MainAgent(*args, **{**kwargs, "llm": LLM(), "native": native})

    monkeypatch.setattr(adapter, "MainAgent", factory)
    monkeypatch.setattr(delegation, "build_read_tools", lambda _: [Tool("read_probe", "read", {}, read)])
    service, runner, parent, child, _ = await setup(tmp_path, lambda task, collaboration: execute_dispatch(str(tmp_path), task, collaboration))
    service.release("owner", child.id, 1)
    await asyncio.wait_for(reached.wait(), 3)
    original_inputs = runner.get(child.id).dependencies["inputs"]
    charge = budget_view(runner.get(child.id), runner.ledger)["used"]
    change_source(runner, parent)
    finish.set()
    await runner.join(child.id)
    final = runner.get(child.id)
    assert final.status == "failed" and not final.result
    assert final.dependencies["resolution"] == "invalidated"
    assert final.dependencies["inputs"] == original_inputs
    assert len(model_calls) == 1 and len(tool_calls) == (boundary == "tool")
    assert budget_view(final, runner.ledger)["used"] == charge and runner.active_count == 0
    assert dependency_view(final, runner.ledger)["result_valid"] is False
    assert service.release("owner", child.id, 1)[1]


@pytest.mark.parametrize("change", ["result", "review", "owner", "round", "missing"])
@pytest.mark.asyncio
async def test_finish_gate_also_protects_injected_executors(tmp_path, change):
    reached, finish = asyncio.Event(), asyncio.Event()

    async def execute(task, collaboration):
        collaboration.start(task.id, 1)
        reached.set()
        await finish.wait()
        collaboration.finish(task.id, 1, "迟到结论")

    service, runner, parent, child, _ = await setup(tmp_path, execute)
    service.release("owner", child.id, 1)
    await reached.wait()
    change_source(runner, parent, change)
    finish.set()
    await runner.join(child.id)
    final = runner.get(child.id)
    assert final.status == "failed" and not final.result and final.dependencies["invalidation"]["round"] == 1


@pytest.mark.asyncio
async def test_stop_is_durable_and_repeated_stop_keeps_cleanup_slot(tmp_path):
    started, cleaning, finish_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def execute(task, collaboration):
        collaboration.start(task.id, 1)
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await finish_cleanup.wait()
            # Even a cancellation-swallowing executor cannot submit success.
            collaboration.finish(task.id, 1, "取消后迟到结论")

    service, runner, parent, child, calls = await setup(tmp_path, execute)
    service.release("owner", child.id, 1)
    await started.wait()
    change_source(runner, parent)
    marked, changed, stopped = service.reconcile_dependencies("owner", child.id, 1)
    assert changed and stopped and marked.status == "running"
    assert dependency_view(marked, runner.ledger)["stop_pending"]
    await cleaning.wait()
    repeated, changed, stopped = service.reconcile_dependencies("owner", child.id, 1)
    assert not changed and stopped and repeated.dependencies == marked.dependencies
    other, _ = service.submit(session="owner", request_id="other", prompt="独立研究")
    await asyncio.sleep(0)
    assert runner.get(other.id).status == "queued" and runner.active_count == 2 and len(calls) == 1
    finish_cleanup.set()
    await runner.join(child.id)
    await runner.join(other.id)
    assert runner.get(child.id).status == "failed" and not runner.get(child.id).result
    assert runner.get(other.id).status == "done" and runner.active_count == 0


@pytest.mark.asyncio
async def test_queued_stop_before_first_timeslice_never_executes(tmp_path):
    service, runner, parent, child, calls = await setup(tmp_path)
    service.release("owner", child.id, 1)
    charge = budget_view(runner.get(child.id), runner.ledger)["used"]
    change_source(runner, parent)
    task, changed, stopped = service.reconcile_dependencies("owner", child.id, 1)
    assert task.status == "queued" and changed and stopped
    await runner.join(child.id)
    assert runner.get(child.id).status == "failed" and len(calls) == 1
    assert budget_view(runner.get(child.id), runner.ledger)["used"] == charge


@pytest.mark.asyncio
async def test_storage_failure_does_not_request_stop_or_claim_invalidation(tmp_path, monkeypatch):
    started = asyncio.Event()

    async def execute(task, collaboration):
        collaboration.start(task.id, 1)
        started.set()
        await asyncio.Event().wait()

    service, runner, parent, child, _ = await setup(tmp_path, execute)
    service.release("owner", child.id, 1)
    await started.wait()
    change_source(runner, parent)
    original_save = TaskLedger.save
    monkeypatch.setattr(TaskLedger, "save", lambda self, task: False if task.dependencies.get("resolution") == "invalidated" else original_save(self, task))
    with pytest.raises(OSError):
        service.reconcile_dependencies("owner", child.id, 1)
    assert runner.get(child.id).dependencies["resolution"] == "consumed"
    assert runner._running[child.id].cancelling() == 0
    monkeypatch.setattr(TaskLedger, "save", original_save)
    assert service.reconcile_dependencies("owner", child.id, 1)[1:]==(True, True)
    await runner.join(child.id)
    assert runner.get(child.id).status == "failed"


@pytest.mark.asyncio
async def test_failed_terminal_save_is_unconfirmed_and_reconcile_only_repairs_receipt(tmp_path, monkeypatch):
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def execute(task, collaboration):
        collaboration.start(task.id, 1)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    service, runner, parent, child, _ = await setup(tmp_path, execute)
    service.release("owner", child.id, 1)
    await started.wait()
    change_source(runner, parent)
    original_save = TaskLedger.save
    monkeypatch.setattr(TaskLedger, "save", lambda self, task: False if task.status == "failed" else original_save(self, task))
    service.reconcile_dependencies("owner", child.id, 1)
    with pytest.raises(OSError):
        await runner.join(child.id)
    assert cleaned.is_set() and runner.active_count == 0
    view = dependency_view(runner.get(child.id), runner.ledger)
    assert view["stop_pending"] and "停止尚未确认" in view["next_action"]
    charge = budget_view(runner.get(child.id), runner.ledger)["used"]
    monkeypatch.setattr(TaskLedger, "save", original_save)
    final, changed, stopped = service.reconcile_dependencies("owner", child.id, 1)
    assert final.status == "failed" and not changed and not stopped
    assert budget_view(final, runner.ledger)["used"] == charge


@pytest.mark.asyncio
async def test_completed_history_receipt_and_acceptance_remain_distinct(tmp_path):
    service, runner, parent, child, _ = await setup(tmp_path)
    service.release("owner", child.id, 1)
    await runner.join(child.id)
    child = service.review("owner", child.id, 1, "accept", "已核对原结果")
    inbox = CompletionInbox(str(tmp_path), "sid-owner")
    old_revision = revision(child)
    inbox.acknowledge(child.id, old_revision, "已汇报旧结果")
    old_inputs = child.dependencies["inputs"]
    change_source(runner, parent)
    final, changed, stopped = service.reconcile_dependencies("owner", child.id, 1)
    assert changed and not stopped and final.status == "done" and final.result == child.result
    assert final.collaboration["review"] == "accepted" and final.dependencies["inputs"] == old_inputs
    assert revision(final) != old_revision
    item = next(item for item in inbox.pending() if item["task_id"] == child.id)
    assert item["dependencies"]["result_valid"] is False and "失效" in item["next_action"]
    with pytest.raises(CollaborationConflict):
        service.review("owner", child.id, 1, "accept", "重复旧接受")
    assert not service.reconcile_dependencies("owner", child.id, 1)[1]
    runner.ledger.save(parent)  # Returning the source to its old version is not recovery.
    with pytest.raises(CollaborationConflict):
        validate_consumed(runner.get(child.id), runner.ledger)
    with pytest.raises(CollaborationConflict):
        service.submit(session="owner", request_id="descendant", prompt="下一级",
                       depends_on=[{"task_id": child.id, "round": 1}])


@pytest.mark.asyncio
async def test_blocked_invalidation_closes_question_without_new_round(tmp_path):
    async def execute(task, collaboration):
        collaboration.start(task.id, 1)
        collaboration.ask_question(task.id, 1, "哪个环境？")

    service, runner, parent, child, _ = await setup(tmp_path, execute)
    service.release("owner", child.id, 1)
    await runner.join(child.id)
    previous = runner.get(child.id)
    qid = previous.collaboration["questions"][0]["id"]
    charge = budget_view(previous, runner.ledger)["used"]
    change_source(runner, parent)
    final, changed, stopped = service.reconcile_dependencies("owner", child.id, 1)
    assert changed and not stopped and final.status == "failed"
    assert final.collaboration["questions"][0]["status"] == "cancelled" and final.collaboration["round"] == 1
    with pytest.raises(CollaborationConflict):
        service.answer_question("owner", child.id, 1, qid, "测试")
    assert budget_view(final, runner.ledger)["used"] == charge and runner.active_count == 0


@pytest.mark.asyncio
async def test_read_projection_and_review_gate_catch_stale_history(tmp_path):
    from src.gateway.worktree_sessions import task_session_view
    service, runner, parent, child, _ = await setup(tmp_path)
    service.release("owner", child.id, 1)
    await runner.join(child.id)
    assert not service.reconcile_dependencies("owner", child.id, 1)[1]
    change_source(runner, parent)
    view = task_session_view(str(tmp_path), runner.get(child.id), worktrees=[])
    assert view["dependencies"]["result_valid"] is False and view["dependencies"]["can_reconcile"]
    assert "失效" in view["handoff"]["next_action"]
    with pytest.raises(CollaborationConflict):
        service.review("owner", child.id, 1, "accept", "不能接受失效结果")
    assert runner.get(child.id).collaboration["review"] == "pending"
    assert runner.get(child.id).dependencies["resolution"] == "invalidated"


@pytest.mark.asyncio
async def test_http_auth_scope_round_and_replay(tmp_path, monkeypatch):
    from src.web.routers import tasks
    from src.web.server import app
    service, runner, parent, child, _ = await setup(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tasks, "_RUNNER", runner)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "local-invalidation-fixture")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    service.release("owner", child.id, 1)
    await runner.join(child.id)
    change_source(runner, parent)
    path = f"/api/delegations/{child.id}/reconcile"
    headers = {"Authorization": "Bearer local-invalidation-fixture"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        body = {"session": "owner", "round": 1}
        assert (await client.post(path, json=body)).status_code == 401
        assert (await client.post(path, headers=headers, json={**body,"grant":True})).status_code == 422
        assert (await client.post(path, headers=headers, json={**body,"round":True})).status_code == 422
        assert (await client.post(path, headers=headers, json={**body,"session":"other"})).status_code == 409
        assert (await client.post(path, headers=headers, json={**body,"round":2})).status_code == 409
        first = (await client.post(path, headers=headers, json=body)).json()
        assert first["invalidation_recorded"] and not first["stop_requested"] and first["status"] == "done"
        replay = (await client.post(path, headers=headers, json=body)).json()
        assert not replay["invalidation_recorded"] and replay["handling"]["revision"] == first["handling"]["revision"]


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.asyncio
async def test_main_agent_reconcile_tool_uses_dynamic_owner_and_shared_service(tmp_path, native):
    from src.agents.agent_loop import MainAgent
    from src.agents.main_agent import build_research_tools
    service, runner, parent, child, _ = await setup(tmp_path)
    service.release("owner", child.id, 1)
    await runner.join(child.id)
    change_source(runner, parent)
    owner = ["sid-other"]
    collaboration = service.collaboration("owner")
    collaboration._owner = lambda: owner[0]
    tools = build_research_tools(str(tmp_path), collaboration=collaboration, reconcile_task_dependencies=service.reconcile_dependencies)
    reconcile = next(tool for tool in tools if tool.name == "task_reconcile")
    assert "task_reconcile" not in {tool.name for tool in build_research_tools(str(tmp_path), collaboration=collaboration)}
    with pytest.raises(CollaborationConflict):
        await reconcile.handler({"task_id": child.id, "round": 1})
    owner[0] = "sid-owner"
    with pytest.raises(CollaborationConflict):
        await reconcile.handler({"task_id": child.id, "round": 1,"session":"other"})

    class LLM:
        calls = 0
        async def chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls > 1:
                assert '"result_valid": false' in str(messages)
                return {"content": "已记录旧结果失效，请重新下派"}
            args = {"task_id": child.id, "round": 1}
            if native:
                return {"content": "", "tool_calls": [{"id": "call-reconcile", "name": "task_reconcile", "arguments": json.dumps(args)}]}
            return {"content": json.dumps({"tool": "task_reconcile", "args": args})}

    llm = LLM()
    result = await MainAgent(tools, llm=llm, native=native, max_steps=3).run_turn("核对旧结果的依赖", mode="plan")
    assert "失效" in result and llm.calls == 2 and runner.active_count == 0


@pytest.mark.parametrize("method", ["prompt", "native", "summary", "summary_error", "stream", "native_stream"])
@pytest.mark.asyncio
async def test_all_model_paths_check_after_response_before_output(method):
    from src.agents.agent_loop import MainAgent
    state = {"valid": True, "calls": 0}
    displayed = []

    def check():
        if not state["valid"]:
            raise CollaborationConflict("已消费的前置结果版本变化")

    class LLM:
        async def chat(self, messages, **kwargs):
            state.update(valid=False, calls=state["calls"] + 1)
            if method == "summary_error":
                raise RuntimeError("provider error after invalidation")
            return {"content": "迟到输出"}

        async def stream(self, messages, **kwargs):
            state.update(valid=False, calls=state["calls"] + 1)
            yield "迟到输出"

        async def stream_chat(self, messages, **kwargs):
            state.update(valid=False, calls=state["calls"] + 1)
            kwargs["on_content"]("迟到输出")
            return {"content": "迟到输出"}

    agent = MainAgent([], llm=LLM(), execution_check=check)
    with pytest.raises(CollaborationConflict):
        if method.startswith("summary"):
            await agent._summarize([{"role": "user", "content": "历史"}])
        elif method in {"native", "native_stream"}:
            await agent._native_complete([], [], displayed.append if method == "native_stream" else None, None, [])
        else:
            await agent._complete([], displayed.append if method == "stream" else None)
    assert state["calls"] == 1 and not displayed
    with pytest.raises(CollaborationConflict):
        await agent._complete([], None)
    assert state["calls"] == 1


@pytest.mark.asyncio
async def test_tool_admission_rechecks_after_an_async_hook():
    from src.agents.agent_loop import MainAgent
    from src.agents.tool import Tool
    calls = []
    valid = [True]

    def check():
        if not valid[0]:
            raise CollaborationConflict("前置结果版本变化")

    async def read(args):
        calls.append(args)
        return "证据"

    async def hook(*args, **kwargs):
        valid[0] = False
        return None

    agent = MainAgent([], execution_check=check)
    agent._hook_system = object()
    agent._fire_hook = hook
    with pytest.raises(CollaborationConflict):
        await agent._run_bound_tool(Tool("read", "read", {}, read), {}, "plan", lambda _: None)
    assert not calls


@pytest.mark.parametrize("field,value", [("at", "bad"), ("round", True), ("task_id", "task-other"), ("reason", ""), ("owner_session", "sid-other")])
@pytest.mark.asyncio
async def test_corrupt_invalidation_marker_is_never_accepted(tmp_path, field, value):
    service, runner, parent, child, _ = await setup(tmp_path)
    service.release("owner", child.id, 1)
    await runner.join(child.id)
    change_source(runner, parent)
    task = service.reconcile_dependencies("owner", child.id, 1)[0]
    task.dependencies["invalidation"][field] = value
    runner.ledger.save(task)
    assert dependency_view(task, runner.ledger)["result_valid"] is False
    with pytest.raises(CollaborationConflict):
        service.review("owner", child.id, 1, "accept", "接受损坏合同")


@pytest.mark.asyncio
async def test_transitive_invalidation_and_restart_keep_original_contract(tmp_path):
    service, runner, parent, middle, _ = await setup(tmp_path)
    service.release("owner", middle.id, 1)
    await runner.join(middle.id)
    middle = service.review("owner", middle.id, 1, "accept", "核对中间结果")
    child, _ = service.submit(session="owner", request_id="grandchild", prompt="传递依赖",
                              max_steps=4, timeout_seconds=30, depends_on=[{"task_id": middle.id, "round": 1}])
    service.release("owner", child.id, 1)
    await runner.join(child.id)
    charge = budget_view(runner.get(child.id), runner.ledger)["used"]
    change_source(runner, parent)
    child = service.reconcile_dependencies("owner", child.id, 1)[0]
    assert child.dependencies["resolution"] == "invalidated"
    assert runner.recover() == []
    restored = TaskLedger(str(tmp_path)).load(child.id)
    assert restored.dependencies == child.dependencies and restored.status == "done"
    assert budget_view(restored, runner.ledger)["used"] == charge
    assert service.release("owner", child.id, 1)[1]
    with pytest.raises(CollaborationConflict):
        service.followup("owner", child.id, 1, "不能恢复失效合同")
