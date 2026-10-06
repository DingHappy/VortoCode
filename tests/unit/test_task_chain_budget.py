"""Cumulative execution admission, conservative reservations and shared task state."""
import hashlib
import json

import pytest

from src.gateway.collaboration import CollaborationConflict
from src.gateway.dispatch import DispatchService
from src.gateway.handoffs import revision
from src.gateway.task_chain_budget import budget_view
from src.gateway.tasks import TaskLedger, TaskRunner


LIMITS = {"tasks": 4, "rounds": 4, "steps": 16, "timeout_seconds": 120}


def fixture(root, execute=None):
    calls = []

    async def worker(task, progress):
        return "dev"

    async def run(task, collaboration):
        calls.append((task.id, task.collaboration["round"]))
        collaboration.start(task.id, task.collaboration["round"])
        collaboration.finish(task.id, task.collaboration["round"], "app.py:1 配置证据", tainted=True)

    runner = TaskRunner(str(root), worker, max_concurrent=1)
    service = DispatchService(str(root), runner, execute=execute or run, validate_agent=lambda _: None)
    return service, runner, calls


def submit(service, key="root", parents=(), limits=None):
    return service.submit(session="owner", request_id=key, prompt="研究配置 " + key,
                          max_steps=4, timeout_seconds=30, chain_limits=limits,
                          depends_on=[{"task_id": task.id, "round": task.collaboration["round"]} for task in parents])


async def root(service, runner, key="root", limits=LIMITS):
    task, _ = submit(service, key, limits=limits)
    await runner.join(task.id)
    return service.review("owner", task.id, 1, "accept", "已核对证据")


def view(task, runner):
    return budget_view(task, runner.ledger)


@pytest.mark.asyncio
async def test_members_share_monotonic_allowance_and_replays_do_not_charge(tmp_path):
    service, runner, calls = fixture(tmp_path)
    parent = await root(service, runner)
    original_revision = revision(parent)
    a, _ = submit(service, "a", [parent])
    b, _ = submit(service, "b", [parent])
    assert a.status == b.status == "waiting" and runner.active_count == 0
    assert view(a, runner)["used"] == {"tasks": 3, "rounds": 1, "steps": 4, "timeout_seconds": 30}
    assert submit(service, "a", [parent])[1]
    assert service.release("owner", a.id, 1)[0].status == "queued"
    assert service.release("owner", a.id, 1)[1]
    await runner.join(a.id)
    a = service.followup("owner", a.id, 1, "补充范围内证据")
    await runner.join(a.id)
    await service.cancel("owner", b.id, 1)
    current = view(service.get("owner", a.id), runner)
    assert current["used"] == {"tasks": 3, "rounds": 3, "steps": 12, "timeout_seconds": 90}
    assert current["reserved_rounds"] == [1, 2] and not current["token_cost_hard_limit"]
    assert service.release("owner", a.id, 1)[1] and len(calls) == 3
    assert revision(service.get("owner", parent.id)) == original_revision


@pytest.mark.parametrize("dimension,value", [("rounds", 1), ("steps", 4), ("timeout_seconds", 30)])
@pytest.mark.asyncio
async def test_exhausted_allowance_keeps_waiting_before_any_model(tmp_path, dimension, value):
    service, runner, calls = fixture(tmp_path)
    parent = await root(service, runner, limits={**LIMITS, dimension: value})
    child, _ = submit(service, "child", [parent])
    with pytest.raises(CollaborationConflict, match="累计执行额度不足"):
        service.release("owner", child.id, 1)
    child = service.get("owner", child.id)
    assert child.status == "waiting" and child.dependencies["resolution"] == "waiting"
    assert len(calls) == 1 and runner.active_count == 0 and view(child, runner)["used"]["rounds"] == 1


@pytest.mark.asyncio
async def test_task_cap_and_cancellation_do_not_create_room_for_new_id(tmp_path):
    service, runner, calls = fixture(tmp_path)
    parent = await root(service, runner, limits={**LIMITS, "tasks": 2})
    child, _ = submit(service, "child", [parent])
    await service.cancel("owner", child.id, 1)
    with pytest.raises(CollaborationConflict, match="累计任务数"):
        submit(service, "new-id", [parent])
    assert len(runner.ledger.list()) == 2 and len(calls) == 1


@pytest.mark.asyncio
async def test_inheritance_cannot_merge_or_reset_and_accepts_unbudgeted_context(tmp_path):
    service, runner, _ = fixture(tmp_path)
    a = await root(service, runner, "a")
    b = await root(service, runner, "b")
    unbudgeted = await root(service, runner, "old", limits=None)
    with pytest.raises(CollaborationConflict, match="不能合并"):
        submit(service, "merge", [a, b])
    with pytest.raises(CollaborationConflict, match="重设"):
        submit(service, "reset", [a], limits=LIMITS)
    child, _ = submit(service, "inherit", [unbudgeted, a])
    assert view(child, runner)["root_task_id"] == a.id
    assert view(child, runner)["used"]["tasks"] == 2
    scoped, _ = submit(service, "new-root", [unbudgeted], limits=LIMITS)
    assert view(scoped, runner)["root_task_id"] == scoped.id and view(scoped, runner)["used"]["rounds"] == 0


@pytest.mark.parametrize("limits", [{}, {**LIMITS, "tasks": True}, {**LIMITS, "rounds": "4"},
                                  {**LIMITS, "extra": 1}, {**LIMITS, "steps": 1153},
                                  {**LIMITS, "timeout_seconds": 29}, {**LIMITS, "steps": 3}])
@pytest.mark.asyncio
async def test_invalid_or_impossible_root_budget_creates_nothing(tmp_path, limits):
    service, runner, calls = fixture(tmp_path)
    with pytest.raises(CollaborationConflict):
        submit(service, limits=limits)
    assert not runner.ledger.list() and not calls and runner.active_count == 0


@pytest.mark.asyncio
async def test_admission_save_precedes_child_and_ambiguous_retry_reuses_membership(tmp_path, monkeypatch):
    service, runner, calls = fixture(tmp_path)
    parent = await root(service, runner)
    original = TaskLedger.save
    monkeypatch.setattr(TaskLedger, "save", lambda self, task: original(self, task) if task.id == parent.id else False)
    with pytest.raises(OSError):
        submit(service, "child", [parent])
    assert view(service.get("owner", parent.id), runner)["used"]["tasks"] == 2
    child_id = "task-dispatch-" + hashlib.sha256(json.dumps(["sid-owner", "child"]).encode()).hexdigest()[:32]
    assert not runner.ledger.has_record(child_id) and len(calls) == 1
    with pytest.raises(CollaborationConflict, match="已绑定其他合同"):
        service.submit(session="owner", request_id="child", prompt="不同目标", max_steps=4, timeout_seconds=30,
                       depends_on=[{"task_id": parent.id, "round": 1}])
    monkeypatch.setattr(TaskLedger, "save", original)
    child, _ = submit(service, "child", [parent])
    assert child.id == child_id and view(child, runner)["used"]["tasks"] == 2


@pytest.mark.asyncio
async def test_root_save_failure_cannot_admit_or_reserve_child(tmp_path, monkeypatch):
    service, runner, calls = fixture(tmp_path)
    parent = await root(service, runner)
    original = TaskLedger.save
    monkeypatch.setattr(TaskLedger, "save", lambda self, task: False if task.id == parent.id else original(self, task))
    with pytest.raises(OSError, match="额度未能持久化"):
        submit(service, "child", [parent])
    assert view(service.get("owner", parent.id), runner)["used"]["tasks"] == 1 and len(calls) == 1


@pytest.mark.asyncio
async def test_release_second_save_failure_retains_one_reservation_without_granting_work(tmp_path, monkeypatch):
    service, runner, calls = fixture(tmp_path)
    parent = await root(service, runner, limits={**LIMITS, "rounds": 2, "steps": 8, "timeout_seconds": 60})
    child, _ = submit(service, "child", [parent])
    original = TaskLedger.save
    monkeypatch.setattr(TaskLedger, "save", lambda self, task: False if task.id == child.id and task.status == "queued" else original(self, task))
    with pytest.raises(OSError):
        service.release("owner", child.id, 1)
    waiting = service.get("owner", child.id)
    assert waiting.status == "waiting" and view(waiting, runner)["used"]["rounds"] == 2
    assert len(calls) == 1 and runner.active_count == 0
    monkeypatch.setattr(TaskLedger, "save", original)
    service.release("owner", child.id, 1)
    await runner.join(child.id)
    assert view(service.get("owner", child.id), runner)["used"]["rounds"] == 2 and len(calls) == 2


@pytest.mark.asyncio
async def test_shared_cap_serializes_concurrent_ready_siblings(tmp_path):
    import asyncio
    service, runner, calls = fixture(tmp_path)
    parent = await root(service, runner, limits={**LIMITS, "rounds": 2})
    a, _ = submit(service, "a", [parent])
    b, _ = submit(service, "b", [parent])
    async def release(task):
        return service.release("owner", task.id, 1)
    results = await asyncio.gather(release(a), release(b), return_exceptions=True)
    assert sum(isinstance(item, CollaborationConflict) for item in results) == 1
    await runner.join(a.id)
    assert service.get("owner", b.id).status == "waiting" and len(calls) == 2


@pytest.mark.parametrize("rounds", [1, 2])
@pytest.mark.asyncio
async def test_answer_uses_same_budget_and_replay_never_recharges(tmp_path, rounds):
    calls = []
    async def asking(task, collaboration):
        calls.append(task.collaboration["round"])
        collaboration.start(task.id, task.collaboration["round"])
        if task.collaboration["round"] == 1:
            collaboration.ask_question(task.id, 1, "哪个环境？")
        else:
            collaboration.finish(task.id, 2, "测试环境证据")
    service, runner, _ = fixture(tmp_path, asking)
    task, _ = submit(service, limits={**LIMITS, "rounds": rounds})
    await runner.join(task.id)
    task = service.get("owner", task.id)
    question = task.collaboration["questions"][0]
    if rounds == 1:
        with pytest.raises(CollaborationConflict, match="累计执行额度不足"):
            service.answer_question("owner", task.id, 1, question["id"], "测试环境")
        latest = service.get("owner", task.id)
        assert latest.status == "blocked" and latest.collaboration["questions"][0]["status"] == "open"
        assert calls == [1]
    else:
        service.answer_question("owner", task.id, 1, question["id"], "测试环境")
        await runner.join(task.id)
        latest, replayed = service.answer_question("owner", task.id, 1, question["id"], "测试环境")
        assert replayed and latest.status == "done" and view(latest, runner)["used"]["rounds"] == 2 and calls == [1, 2]


@pytest.mark.parametrize("damage", ["missing_link", "missing_both", "missing_root", "owner", "limits", "duplicate", "unbooked", "contract"])
@pytest.mark.asyncio
async def test_damaged_queued_contract_cannot_reach_executor(tmp_path, damage):
    service, runner, calls = fixture(tmp_path)
    parent = await root(service, runner)
    child, _ = submit(service, "child", [parent])
    service.release("owner", child.id, 1)
    parent = runner.get(parent.id)
    if damage == "missing_root":
        runner.ledger._path(parent.id).unlink()
    elif damage in {"missing_link", "missing_both", "contract"}:
        child = runner.get(child.id)
        if damage in {"missing_link", "missing_both"}:
            child.chain_budget = {}
            if damage == "missing_both":
                child.collaboration["dispatch"].pop("chain_root")
        else:
            child.collaboration["dispatch"]["max_steps"] = 5
        runner.ledger.save(child)
    else:
        if damage == "owner":
            parent.owner_session = "sid-other"
        elif damage == "limits":
            parent.chain_budget["limits"]["rounds"] = True
        elif damage == "duplicate":
            parent.chain_budget["reservations"].append(dict(parent.chain_budget["reservations"][-1]))
        else:
            parent.chain_budget["reservations"] = parent.chain_budget["reservations"][:1]
        runner.ledger.save(parent)
    await runner.join(child.id)
    final = service.get("owner", child.id)
    assert final.status == "failed" and final.error and len(calls) == 1


@pytest.mark.asyncio
async def test_restart_and_old_release_replay_keep_charges_then_followup_spends_next_round(tmp_path):
    service, runner, calls = fixture(tmp_path)
    parent = await root(service, runner)
    child, _ = submit(service, "child", [parent])
    service.release("owner", child.id, 1)
    await service.cancel("owner", child.id, 1)
    interrupted = runner.get(child.id)
    interrupted.status = "queued"
    runner.ledger.save(interrupted)
    restarted, pool, new_calls = fixture(tmp_path)
    assert pool.recover()[0].status == "interrupted"
    assert restarted.release("owner", child.id, 1)[1] and not new_calls
    assert view(restarted.get("owner", child.id), pool)["used"]["rounds"] == 2
    restarted.followup("owner", child.id, 1, "核对后继续")
    await pool.join(child.id)
    assert view(restarted.get("owner", child.id), pool)["used"]["rounds"] == 3
    assert len(new_calls) == 1 and len(calls) == 1


@pytest.mark.asyncio
async def test_http_budget_strict_contract_and_exhaustion_are_visible_without_execution(tmp_path, monkeypatch):
    import httpx
    from src.web.server import app
    from src.web.routers import delegations
    service, runner, calls = fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "local-chain-test")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    monkeypatch.setattr(delegations, "get_dispatch_service", lambda: service)
    headers = {"Authorization": "Bearer local-chain-test"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local", headers=headers) as client:
        body = {"session": "owner", "request_id": "http-root", "prompt": "研究配置", "max_steps": 4,
                "timeout_seconds": 30, "chain_limits": {**LIMITS, "rounds": 1}}
        bad = {**body, "chain_limits": {**LIMITS, "rounds": True}}
        assert (await client.post("/api/delegations", json=bad)).status_code == 422
        response = await client.post("/api/delegations", json=body)
        assert response.status_code == 202 and response.json()["chain_budget"]["used"]["rounds"] == 1
        tid = response.json()["id"]
        await runner.join(tid)
        await client.post(f"/api/delegations/{tid}/review", json={"session": "owner", "round": 1, "verdict": "accept", "note": "证据已核对"})
        child = (await client.post("/api/delegations", json={"session": "owner", "request_id": "http-child", "prompt": "后续研究",
                    "max_steps": 4, "timeout_seconds": 30, "depends_on": [{"task_id": tid, "round": 1}]})).json()
        assert child["chain_budget"]["root_task_id"] == tid
        rejected = await client.post(f'/api/delegations/{child["id"]}/release', json={"session": "owner", "round": 1})
        assert rejected.status_code == 409 and "累计执行额度不足" in rejected.text
        current = (await client.get(f'/api/delegations/{child["id"]}?session=owner')).json()
        assert current["status"] == "waiting" and current["chain_budget"]["remaining"]["rounds"] == 0
        assert len(calls) == 1 and runner.active_count == 0
        caps = (await client.get("/api/delegations/capabilities")).json()["chain_budget"]
        assert caps["available"] and not caps["token_cost_hard_limit"] and not caps["refund"]


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.asyncio
async def test_actual_read_agent_question_answer_keeps_one_chain_and_original_step_limit(tmp_path, monkeypatch, native):
    import src.agents.dispatch as adapter
    from src.agents.agent_loop import MainAgent
    requests, agents = [], []
    class LLM:
        async def chat(self, messages, **kwargs):
            requests.append(messages)
            if len(requests) == 1:
                args = {"question": "哪个环境？", "context": "已定位配置入口"}
                if native:
                    return {"content": "", "tool_calls": [{"id": "ask", "name": "ask_task_question", "arguments": json.dumps(args)}]}
                return {"content": json.dumps({"tool": "ask_task_question", "args": args}, ensure_ascii=False)}
            assert "测试环境" in str(messages)
            return {"content": "测试环境配置证据"}
    def factory(*args, **kwargs):
        agent = MainAgent(*args, **{**kwargs, "llm": LLM(), "native": native})
        agents.append(agent)
        return agent
    monkeypatch.setattr(adapter, "MainAgent", factory)
    monkeypatch.setenv("VORTOCODE_MAX_STEPS", "99")
    service, runner, _ = fixture(tmp_path, lambda task, collaboration: adapter.execute_dispatch(str(tmp_path), task, collaboration))
    task, _ = submit(service, limits={**LIMITS, "rounds": 2})
    await runner.join(task.id)
    task = service.get("owner", task.id)
    assert task.status == "blocked" and len(requests) == 1
    question = task.collaboration["questions"][0]
    service.answer_question("owner", task.id, 1, question["id"], "测试环境")
    await runner.join(task.id)
    final = service.get("owner", task.id)
    assert final.status == "done" and final.collaboration["round"] == 2 and len(requests) == 2
    assert view(final, runner)["used"] == {"tasks": 1, "rounds": 2, "steps": 8, "timeout_seconds": 60}
    assert all(agent.max_steps <= 4 and agent._untrusted_input for agent in agents)
