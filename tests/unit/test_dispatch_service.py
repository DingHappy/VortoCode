"""Dispatch contracts and real HTTP adapters without model/network calls."""
import asyncio

import httpx
import pytest

from src.gateway.collaboration import CollaborationConflict
from src.gateway.dispatch import DispatchService
from src.gateway.tasks import TaskLedger, TaskRunner


def make_service(root, *, execute=None, worker=None, validator=lambda _: None):
    async def default_worker(task, progress):
        return "dev done"
    async def default_execute(task, service):
        service.start(task.id, task.collaboration["round"])
        await asyncio.sleep(0)
        service.finish(task.id, task.collaboration["round"], "交付分析")
    runner = TaskRunner(str(root), worker or default_worker, max_concurrent=1)
    service = DispatchService(str(root), runner, execute=execute or default_execute, validate_agent=validator)
    return service, runner


@pytest.mark.asyncio
async def test_durable_request_id_prevents_duplicate_and_conflicting_dispatch(tmp_path):
    calls = []
    async def execute(task, service):
        calls.append(task.id)
        service.start(task.id, 1)
        service.finish(task.id, 1, "完成")
    service, runner = make_service(tmp_path, execute=execute)
    body = {"session": "owner", "request_id": "req-1", "prompt": "分析模块", "acceptance": ["给出证据"]}
    first, replayed = service.submit(**body)
    assert not replayed
    second, replayed = service.submit(**{**body, "session": "sid-owner"})
    assert replayed and first.id == second.id and runner.active_count == 1
    with pytest.raises(CollaborationConflict):
        service.submit(**{**body, "prompt": "不同工作"})
    await runner.join(first.id)
    restored = DispatchService(str(tmp_path), runner, execute=execute, validate_agent=lambda _: None)
    assert restored.submit(**body)[1]
    assert calls == [first.id]
    other, _ = restored.submit(**{**body, "session": "other"})
    assert other.id != first.id
    await runner.join(other.id)
    with pytest.raises(CollaborationConflict):
        restored.get("other", first.id)


@pytest.mark.asyncio
async def test_review_followup_rounds_and_acceptance(tmp_path):
    service, runner = make_service(tmp_path)
    task, _ = service.submit(session="owner", request_id="r", prompt="分析")
    await runner.join(task.id)
    service.review("owner", task.id, 1, "rework", "缺少错误路径")
    followup = service.followup("owner", task.id, 1, "补充错误路径")
    assert followup.id == task.id and followup.collaboration["round"] == 2
    with pytest.raises(CollaborationConflict):
        service.followup("owner", task.id, 1, "重复发送")
    await runner.join(task.id)
    service.review("owner", task.id, 2, "accept", "已检查")
    with pytest.raises(CollaborationConflict):
        service.followup("owner", task.id, 2, "扩展工作")
    with pytest.raises(CollaborationConflict):
        await service.cancel("owner", task.id, 1)


@pytest.mark.asyncio
async def test_cancellation_before_first_timeslice_is_durable(tmp_path):
    service, runner = make_service(tmp_path)
    task, _ = service.submit(session="owner", request_id="r", prompt="分析")
    cancelled = await service.cancel("owner", task.id, 1)
    assert cancelled.status == "cancelled" and runner.active_count == 0
    assert (await service.cancel("owner", task.id, 1)).status == "cancelled"
    assert [m["kind"] for m in cancelled.collaboration["messages"]] == ["assigned", "cancelled"]


@pytest.mark.asyncio
async def test_development_and_dispatch_share_the_same_concurrency_pool(tmp_path):
    gate = asyncio.Event()
    started = asyncio.Event()
    async def dev(task, progress):
        started.set()
        await gate.wait()
        return "dev done"
    service, runner = make_service(tmp_path, worker=dev)
    dev_task = await runner.submit("开发")
    await started.wait()
    task, _ = service.submit(session="owner", request_id="r", prompt="分析")
    await asyncio.sleep(0)
    assert service.get("owner", task.id).status == "queued"
    gate.set()
    await runner.join(dev_task.id)
    await runner.join(task.id)
    assert service.get("owner", task.id).status == "done"


@pytest.mark.asyncio
async def test_timeout_and_unexpected_worker_error_have_terminal_state(tmp_path):
    async def timed(task, service):
        service.start(task.id, 1)
        try:
            await asyncio.sleep(20)
        except asyncio.CancelledError:
            service.finish(task.id, 1, "", cancelled=True)
            raise
    service, runner = make_service(tmp_path, execute=timed)
    task, _ = service.submit(session="owner", request_id="r", prompt="分析")
    task.collaboration["dispatch"]["timeout_seconds"] = 0.01
    runner.ledger.save(task)
    await runner.join(task.id)
    assert service.get("owner", task.id).status == "failed"
    assert "时限" in service.get("owner", task.id).error
    async def broken(task, service):
        raise RuntimeError("worker failed")
    service.execute = broken
    task, _ = service.submit(session="owner", request_id="next", prompt="分析")
    await runner.join(task.id)
    assert "worker failed" in service.get("owner", task.id).error


@pytest.mark.asyncio
async def test_failed_storage_never_schedules_and_restart_never_auto_replays(tmp_path, monkeypatch):
    service, runner = make_service(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(TaskLedger, "save", lambda *_: False)
        with pytest.raises(OSError):
            service.submit(session="owner", request_id="r", prompt="分析")
        assert runner.active_count == 0
    task, _ = service.submit(session="owner", request_id="r", prompt="分析")
    await runner.join(task.id)
    task = runner.get(task.id)
    task.status = "queued"  # Simulate a process that died after durable create but before execution.
    runner.ledger.save(task)
    runner.recover()
    assert service.submit(session="owner", request_id="r", prompt="分析")[0].status == "interrupted"
    assert runner.active_count == 0
    service.followup("owner", task.id, 1, "确认继续")
    await runner.join(task.id)
    assert runner.get(task.id).collaboration["round"] == 2


@pytest.mark.asyncio
async def test_real_executor_caps_role_and_environment_budget_and_inherits_untrusted(tmp_path, monkeypatch):
    import src.agents.dispatch as adapter
    from src.agents.agent_loop import MainAgent
    from src.agents.capabilities import SENSITIVE_FILES
    from src.agents.taint import is_tainted

    seen = []
    class LLM:
        async def chat(self, messages, **kwargs):
            assert is_tainted()
            assert "验收要求" in str(messages)
            return {"content": "已分析"}
    def factory(*args, **kwargs):
        agent = MainAgent(*args, llm=LLM(), **{k: v for k, v in kwargs.items() if k != "llm"})
        seen.append(agent)
        return agent
    monkeypatch.setattr(adapter, "MainAgent", factory)
    monkeypatch.setenv("VORTOCODE_MAX_STEPS", "100")
    service, runner = make_service(tmp_path, execute=lambda task, parent: adapter.execute_dispatch(str(tmp_path), task, parent))
    task, _ = service.submit(session="owner", request_id="r", prompt="分析", max_steps=2, acceptance=["验收要求"])
    await runner.join(task.id)
    assert runner.get(task.id).status == "done"
    assert seen[0].max_steps == 2
    assert SENSITIVE_FILES not in seen[0]._capabilities.allowed
    assert not any(name.startswith("dev_") or name == "task" for name in seen[0].tools)


@pytest.mark.asyncio
async def test_http_submit_query_review_followup_and_validation(tmp_path, monkeypatch):
    from src.web.routers import delegations
    from src.web.server import app

    service, runner = make_service(tmp_path)
    monkeypatch.setattr(delegations, "get_dispatch_service", lambda: service)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        payload = {"session": "owner", "request_id": "req", "prompt": "分析模块"}
        response = await client.post("/api/delegations", json=payload)
        assert response.status_code == 202
        tid = response.json()["id"]
        await runner.join(tid)
        assert (await client.post("/api/delegations", json=payload)).json()["replayed"]
        assert (await client.get(f"/api/delegations/{tid}", params={"session": "owner"})).json()["review"] == "pending"
        assert (await client.get(f"/api/delegations/{tid}", params={"session": "other"})).status_code == 404
        assert len((await client.get("/api/delegations", params={"session": "owner"})).json()["tasks"]) == 1
        review = {"session": "owner", "round": 1, "verdict": "rework", "note": "缺少说明"}
        assert (await client.post(f"/api/delegations/{tid}/review", json=review)).status_code == 200
        followup = {"session": "owner", "round": 1, "message": "补充说明"}
        assert (await client.post(f"/api/delegations/{tid}/followup", json=followup)).status_code == 202
        await runner.join(tid)
        assert (await client.post(f"/api/delegations/{tid}/followup", json=followup)).status_code == 409
        for invalid in ({**payload, "max_steps": True}, {**payload, "session": "../../x"},
                        {**payload, "prompt": ""}, {**payload, "acceptance": [""]},
                        {**payload, "capabilities": ["host_process"]}):
            assert (await client.post("/api/delegations", json=invalid)).status_code in {400, 422}
        assert (await client.post("/api/delegations", json={**payload, "prompt": "different"})).status_code == 409
        monkeypatch.setenv("VORTOCODE_API_TOKEN", "test-admin-secret")
        assert (await client.get("/api/delegations/capabilities")).status_code == 401
        headers = {"Authorization": "Bearer test-admin-secret"}
        caps = await client.get("/api/delegations/capabilities", headers=headers)
        assert caps.status_code == 200
        check = caps.json()["automatic_check"]
        assert check["available"] is False and "count_input_tokens" in check["missing"]
        assert check["limits"]["tokens"] == 8000 and check["reason"]
        monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
        assert (await client.post("/api/delegations", headers=headers, json=payload)).status_code == 409


def test_development_delivery_and_unknown_roles_rejected(tmp_path):
    from src.agents.dispatch import validate_dispatch_agent
    root = tmp_path / ".vortocode" / "agents"
    root.mkdir(parents=True)
    for name in ("dev", "deliver", "read"):
        (root / f"{name}.md").write_text(f"---\nname: {name}\ntools: {name}\n---\nRole instructions")
    validate_dispatch_agent(str(tmp_path), "read")
    for name in ("dev", "deliver", "missing"):
        with pytest.raises(ValueError):
            validate_dispatch_agent(str(tmp_path), name)


@pytest.mark.asyncio
async def test_role_is_revalidated_before_execution(tmp_path):
    gate = asyncio.Event()
    async def worker(task, progress):
        await gate.wait()
        return "done"
    valid = [True]
    def validator(_):
        if not valid[0]:
            raise ValueError("角色已改为 dev")
    service, runner = make_service(tmp_path, validator=validator, worker=worker)
    dev = await runner.submit("占用并发槽")
    await asyncio.sleep(0)
    task, _ = service.submit(session="owner", request_id="r", prompt="分析")
    valid[0] = False
    gate.set()
    await runner.join(dev.id)
    await runner.join(task.id)
    assert runner.get(task.id).status == "failed"
    assert "dev" in runner.get(task.id).error


@pytest.mark.asyncio
async def test_api_execution_enters_main_agent_inbox_and_shared_review_receipt(tmp_path):
    import json
    from src.agents.agent_loop import MainAgent
    from src.agents.delegation import build_research_tools
    from src.agents.taint import is_tainted, reset_taint
    from src.gateway.handoffs import CompletionInbox
    from src.web.routers.delegations import _view

    dispatch, runner = make_service(tmp_path)
    task, _ = dispatch.submit(session="owner", request_id="receipt", prompt="分析并提供证据")
    await runner.join(task.id)
    collaboration = dispatch.collaboration("owner")
    agent = MainAgent(build_research_tools(str(tmp_path), collaboration=collaboration))
    reset_taint()
    try:
        result = json.loads(await agent._run_tool("task_inbox", {}, "plan", lambda _: None))
        item = result["items"][0]
        assert is_tainted() and item["task_id"] == task.id and item["round"] == 1
        followup_tool = next(tool for tool in build_research_tools(str(tmp_path), collaboration=collaboration)
                             if tool.name == "task_followup")
        with pytest.raises(CollaborationConflict, match="原下派入口"):
            await followup_tool.handler({"task_id": task.id, "round": 1, "message": "补充"})
        assert dispatch.get("owner", task.id).collaboration["round"] == 1
        await agent._run_tool("task_review", {"task_id": task.id, "round": 1,
                              "verdict": "accept", "note": "已核对来源"}, "plan", lambda _: None)
        assert _view(dispatch.get("owner", task.id))["handling"]["handled"] is False
        receipt = json.loads(await agent._run_tool("task_acknowledge", {
            "task_id": task.id, "revision": item["revision"], "note": "已向用户汇报"}, "plan", lambda _: None))
        assert receipt["handled"]
        restored = dispatch.get("owner", task.id)
        assert restored.collaboration["review"] == "accepted"
        assert _view(restored)["handling"]["handled"] is True
        assert CompletionInbox(str(tmp_path), "sid-owner").pending() == []
    finally:
        reset_taint()
