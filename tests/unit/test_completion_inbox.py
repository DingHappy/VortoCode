import asyncio

import pytest

from src.agents.delegation import build_research_tools
from src.gateway.collaboration import CollaborationService
from src.gateway.handoffs import CompletionInbox, HandoffConflict, completion_state


def setup_inbox(root):
    service = CollaborationService(str(root), "sid-owner")
    task = service.ledger.create("dev", "实现功能", owner_session="sid-owner")
    task.status = "done"
    task.result = "已提交结果"
    service.ledger.save(task)
    return service, task, CompletionInbox(str(root), service.owner_identity)


def test_receipt_survives_restart_and_changed_result_is_unread(tmp_path):
    service, task, inbox = setup_inbox(tmp_path)
    rev = inbox.pending()[0]["revision"]
    assert inbox.acknowledge(task.id, rev, "已核对测试，向用户汇报")["handled"]
    restored = CompletionInbox(str(tmp_path), "sid-owner")
    assert restored.pending() == []
    handled = service.ledger.load(task.id)
    stamp = handled.updated
    inbox.acknowledge(task.id, rev, "重复确认")
    assert service.ledger.load(task.id).updated == stamp
    handled.result = "新结果"
    service.ledger.save(handled)
    assert restored.pending()[0]["revision"] != rev
    with pytest.raises(HandoffConflict, match="变化"):
        inbox.acknowledge(task.id, rev, "旧版本")


def test_owner_terminal_kind_and_persistence_boundaries(tmp_path, monkeypatch):
    service, task, inbox = setup_inbox(tmp_path)
    other = CompletionInbox(str(tmp_path), "sid-other")
    assert other.pending() == []
    with pytest.raises(HandoffConflict):
        other.acknowledge(task.id, inbox.pending()[0]["revision"], "窃取结果")
    service.ledger.create("delegation", "委派", owner_session="sid-owner")
    running = service.ledger.create("dev", "未完成", owner_session="sid-owner")
    assert len(inbox.pending()) == 1
    with pytest.raises(HandoffConflict):
        inbox.acknowledge(running.id, "anything", "处理中")
    rev = inbox.pending()[0]["revision"]
    monkeypatch.setattr(inbox.ledger, "save", lambda _: False)
    with pytest.raises(OSError):
        inbox.acknowledge(task.id, rev, "已处理")
    assert inbox.pending()[0]["revision"] == rev


def test_standalone_inbox_resolves_dynamic_owner_and_corrupt_receipt(tmp_path):
    from src.gateway.tasks import TaskLedger

    owner = {"id": "sid-first"}
    inbox = CompletionInbox(str(tmp_path), lambda: owner["id"])
    ledger = TaskLedger(str(tmp_path))
    task = ledger.create("dev", "独立后台执行", owner_session=owner["id"])
    task.status = "failed"
    task.handoff_receipt = "damaged old data"
    ledger.save(task)
    item = inbox.pending()[0]
    owner["id"] = "sid-second"
    assert inbox.pending() == []
    with pytest.raises(HandoffConflict):
        inbox.acknowledge(task.id, item["revision"], "越权处理")
    owner["id"] = "sid-first"
    inbox.acknowledge(task.id, item["revision"], "已解释失败")
    assert inbox.pending() == []
    assert isinstance(ledger.load(task.id).handoff_receipt, dict)
    owner["id"] = ""
    with pytest.raises(HandoffConflict):
        inbox.pending()


@pytest.mark.parametrize("changes", [
    {"handled_at": None}, {"handled_at": ""}, {"handled_at": "invalid"},
    {"handled_at": "2026-10-03T00:00:00"}, {"handled_at": 42},
    {"note": None}, {"note": ""}, {"note": "   "}, {"note": 42},
])
def test_incomplete_matching_receipt_stays_unread_and_can_be_repaired(tmp_path, changes):
    service, task, inbox = setup_inbox(tmp_path)
    rev = inbox.pending()[0]["revision"]
    task.handoff_receipt = {"revision": rev, "note": "已汇报", "handled_at": "2026-10-03T00:00:00+00:00"} | changes
    service.ledger.save(task)
    state = completion_state(service.ledger.load(task.id))
    assert state == {"revision": rev, "handled": False, "handled_at": ""}
    assert inbox.get(task.id, rev)["revision"] == rev
    assert inbox.pending()[0]["task_id"] == task.id
    assert inbox.acknowledge(task.id, rev, "核对后补齐处理回执")["handled"]
    assert inbox.pending() == []


def test_revision_only_receipt_cannot_suppress_unread_delivery(tmp_path):
    service, task, inbox = setup_inbox(tmp_path)
    rev = inbox.pending()[0]["revision"]
    task.handoff_receipt = {"revision": rev}
    service.ledger.save(task)
    assert inbox.pending()[0]["revision"] == rev


def test_pending_only_projects_requested_number_of_results(tmp_path, monkeypatch):
    service, _, inbox = setup_inbox(tmp_path)
    for i in range(3):
        task = service.ledger.create("dev", str(i), owner_session="sid-owner")
        task.status = "done"
        service.ledger.save(task)
    original = inbox._view
    projected = []
    def view(task):
        projected.append(task.id)
        return original(task)
    monkeypatch.setattr(inbox, "_view", view)
    assert len(inbox.pending(limit=1)) == 1
    assert len(projected) == 1
    for bad_limit in (0, -1, 21, True, "1"):
        with pytest.raises(ValueError):
            inbox.pending(limit=bad_limit)


def test_receipt_notifications_follow_durable_save_and_are_idempotent(tmp_path):
    _, task, _ = setup_inbox(tmp_path)
    notified = []
    def update(saved):
        from src.gateway.tasks import TaskLedger
        assert TaskLedger(str(tmp_path)).load(saved.id).handoff_receipt == saved.handoff_receipt
        notified.append(saved.id)
        raise RuntimeError("transport disconnected")
    inbox = CompletionInbox(str(tmp_path), "sid-owner", on_update=update)
    rev = inbox.pending()[0]["revision"]
    assert inbox.acknowledge(task.id, rev, "已汇报")["handled"]
    assert inbox.pending() == []
    inbox.acknowledge(task.id, rev, "重复投递")
    assert notified == [task.id]


@pytest.mark.asyncio
async def test_tools_are_shared_and_inbox_marks_untrusted(tmp_path):
    import json
    from src.agents.agent_loop import MainAgent
    from src.agents.taint import is_tainted, reset_taint

    service, task, inbox = setup_inbox(tmp_path)
    tools = build_research_tools(str(tmp_path), collaboration=service)
    agent = MainAgent(tools)
    reset_taint()
    result = await agent._run_tool("task_inbox", {}, "plan", lambda _: None)
    assert is_tainted()
    rev = json.loads(result)["items"][0]["revision"]
    result = await agent._run_tool("task_acknowledge", {
        "task_id": task.id, "revision": rev, "note": "已说明测试尚未核验"}, "plan", lambda _: None)
    assert json.loads(result)["handled"]
    assert inbox.pending() == []
    reset_taint()


@pytest.mark.asyncio
async def test_turn_reserved_before_queue_broadcast_yields(monkeypatch):
    from src.web.routers import realtime

    release = asyncio.Event()
    key = "sid-reservation-test"
    monkeypatch.setattr(realtime, "_session_key", lambda _: key)
    monkeypatch.setattr(realtime, "_get_session", lambda _: {})
    monkeypatch.setattr(realtime, "_touch_session", lambda _: None)
    monkeypatch.setattr(realtime, "_persist_session", lambda _: None)
    async def broadcast(_):
        await release.wait()
    async def run(*args, **kwargs):
        await release.wait()
    monkeypatch.setattr(realtime, "_send_prompt_queue", broadcast)
    monkeypatch.setattr(realtime, "_run_agent_turn", run)
    item = realtime._new_prompt_item(text="first", mode="plan", images=[], audio=[],
                                     rid="one", want_reasoning=False, context_items=[])
    first = asyncio.create_task(realtime._start_prompt_item(object(), item))
    try:
        await asyncio.sleep(0)
        assert not await realtime._start_prompt_item(object(), {**item, "id": "two"})
        assert realtime._WS_AGENT_RUNNING[key]["id"] == "one"
        turn = realtime._WS_AGENT_TASKS[key]
        release.set()
        assert await first
        await turn
        assert not realtime._ACTORS.busy(key)
        assert key not in realtime._WS_AGENT_RUNNING
    finally:
        release.set()
        await first
        realtime._WS_AGENT_TASKS.pop(key, None)
        realtime._WS_AGENT_RUNNING.pop(key, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["dev", "api"])
async def test_next_user_turn_notified_until_receipt_without_injecting_result(tmp_path, monkeypatch, kind):
    from src.web.routers import realtime

    service, task, inbox = setup_inbox(tmp_path)
    if kind == "api":
        original = inbox.pending()[0]
        inbox.acknowledge(task.id, original["revision"], "已处理原始开发交接")
        task = service.create("HTTP 下派研究", dispatch={"source": "api"})
        service.start(task.id, 1)
        task = service.finish(task.id, 1, "结果待处理")
    task.result = "外部结果不可直接拼入用户授权"
    service.ledger.save(task)
    seen = []
    class Agent:
        tools = {"task_inbox": object()}
        async def run_turn(self, text, **kwargs):
            seen.append(text)
    async def noop(*args, **kwargs):
        pass
    monkeypatch.setattr(realtime, "_ws_agent", lambda _: Agent())
    monkeypatch.setattr(realtime, "_session_key", lambda _: "sid-owner")
    monkeypatch.setattr(realtime, "_get_session", lambda _: {"repo_root": str(tmp_path)})
    monkeypatch.setattr(realtime, "_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(realtime, "_record_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(realtime, "_persist_session", lambda _: None)
    monkeypatch.setattr(realtime, "_ensure_mcp", noop)
    monkeypatch.setattr(realtime, "_publish_session_event", noop)
    monkeypatch.setattr(realtime, "_advance_prompt_queue", noop)
    await realtime._run_agent_turn(object(), "继续检查", "plan")
    assert "task_inbox" in seen[0]
    assert task.result not in seen[0]
    inbox.acknowledge(task.id, inbox.pending()[0]["revision"], "已核对交接")
    await realtime._run_agent_turn(object(), "继续检查", "plan")
    assert seen[1] == "继续检查"


def test_service_delivery_rounds_share_inbox_without_conflating_acceptance(tmp_path):
    from src.gateway.handoffs import completion_state
    from src.gateway.worktree_sessions import task_session_view

    service = CollaborationService(str(tmp_path), "sid-owner")
    task = service.create("分析入口", "reviewer", ["提供证据"], dispatch={"source": "api"})
    inbox = CompletionInbox(str(tmp_path), "sid-owner")
    assert inbox.pending() == []
    service.start(task.id, 1)
    done = service.finish(task.id, 1, "同一段结果", tainted=True)
    first = inbox.pending()[0]
    assert {key: first[key] for key in ("kind", "round", "agent", "review", "tainted")} == {
        "kind": "delegation", "round": 1, "agent": "reviewer", "review": "pending", "tainted": True}
    assert first["acceptance"] == ["提供证据"]
    assert completion_state(done)["handled"] is False
    with pytest.raises(HandoffConflict):
        CompletionInbox(str(tmp_path), "sid-other").acknowledge(task.id, first["revision"], "越权")
    inbox.acknowledge(task.id, first["revision"], "已汇报证据尚未全部验证")
    handled = service.get(task.id)
    assert handled.collaboration["review"] == "pending"
    assert task_session_view(str(tmp_path), handled, worktrees=[])["handoff"]["handling"]["handled"]
    service.review(task.id, 1, "rework", "需要补充")
    assert inbox.pending() == []  # Review messages do not invent a new delivery.
    service.followup(task.id, 1, "补充入口证据")
    assert inbox.pending() == []
    service.start(task.id, 2)
    service.finish(task.id, 2, "同一段结果", tainted=True)
    second = CompletionInbox(str(tmp_path), "sid-owner").pending()[0]
    assert second["round"] == 2 and second["revision"] != first["revision"]
    with pytest.raises(HandoffConflict, match="变化"):
        inbox.acknowledge(task.id, first["revision"], "旧回执")
    service.review(task.id, 2, "accept", "已核对")
    assert inbox.pending()[0]["revision"] == second["revision"]
    inbox.acknowledge(task.id, second["revision"], "已验收并汇报")
    assert service.get(task.id).collaboration["review"] == "accepted"
    assert CompletionInbox(str(tmp_path), "sid-owner").pending() == []


@pytest.mark.parametrize("status", ["failed", "cancelled", "interrupted"])
def test_service_failures_remain_processable_with_save_failure_preserving_unread(tmp_path, monkeypatch, status):
    service = CollaborationService(str(tmp_path), "sid-owner")
    task = service.create("研究", dispatch={"source": "api"})
    task.status = status
    task.error = "执行未完成"
    service.ledger.save(task)
    inbox = CompletionInbox(str(tmp_path), "sid-owner")
    item = inbox.pending()[0]
    monkeypatch.setattr(inbox.ledger, "save", lambda _: False)
    with pytest.raises(OSError):
        inbox.acknowledge(task.id, item["revision"], "已说明阻塞")
    assert inbox.pending()[0]["revision"] == item["revision"]


def test_model_owned_and_corrupt_delegations_are_not_async_inbox_deliveries(tmp_path):
    service = CollaborationService(str(tmp_path), "sid-owner")
    model = service.create("同步模型委派")
    service.start(model.id, 1)
    service.finish(model.id, 1, "已在同回合返回")
    damaged = service.create("损坏记录", dispatch={"source": "api"})
    damaged.status = "done"
    damaged.collaboration["review"] = []
    service.ledger.save(damaged)
    inbox = CompletionInbox(str(tmp_path), "sid-owner")
    assert inbox.pending() == []
    for task in (model, damaged):
        with pytest.raises(HandoffConflict):
            inbox.acknowledge(task.id, "invalid", "不能确认")
