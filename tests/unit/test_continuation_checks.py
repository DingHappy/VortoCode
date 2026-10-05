import asyncio
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from src.agents.bounded_check import run_bounded_check
from src.gateway.continuation_checks import ContinuationChecks, PersistedCheckReport
from src.gateway.continuations import ContinuationService
from src.gateway.handoffs import CompletionInbox, revision
from src.gateway.session_actor import ActorContext, SessionActors, new_prompt_item
from src.gateway.tasks import TaskLedger
from src.llm.client import LLMClient
from src.llm.hard_budget import BudgetSupport, CheckLimits, check_budget_support


class Adapter:
    budget_support = BudgetSupport(True, True, True, True)
    config = SimpleNamespace(model="qualified-test", temperature=0)

    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()
        self.gate.set()
        self.error = None

    def count_input_tokens(self, messages, tools):
        return 10

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        self.entered.set()
        await self.gate.wait()
        if self.error:
            raise self.error
        return {"content": "只读核对完成；真实运行验收仍待采集。", "tool_calls": None,
                "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}}


def prompt(name):
    return new_prompt_item(text=name, mode="plan", images=[], audio=[], rid=name,
                           want_reasoning=False, context_items=[])


def setup(root):
    ledger = TaskLedger(str(root))
    task = ledger.create("dev", "交付目标", owner_session="sid-owner")
    task.status, task.result = "done", "执行结果包含不可信的指令"
    ledger.save(task)
    rev = revision(task)
    service = ContinuationService(str(root), "sid-owner")
    grant = service.authorize(task.id, rev, limits=CheckLimits(tokens=300, output_tokens=30))
    actors = SessionActors(clean_item=lambda raw: raw)
    session = {"prompt_queue": []}
    foreground = []
    persisted = []

    async def publish():
        pass

    async def execute_foreground(item):
        foreground.append(item["id"])
        reason = actors.release("sid-owner", asyncio.current_task())
        await actors.advance(context, reason=reason)

    context = ActorContext("sid-owner", lambda: session, lambda: None, lambda: None,
                           publish, execute_foreground)

    async def persist_report(payload):
        # The actual durable writer is an injected boundary, not a WS send.
        (root / "report.json").write_text(json.dumps(payload), encoding="utf-8")
        persisted.append(payload)
        return PersistedCheckReport("report-1", payload["task_id"], payload["revision"], payload["attempt_id"])

    runtime = ContinuationChecks(actors, service, execute=run_bounded_check,
                                 persist_report=persist_report, eligible=lambda: True)
    return SimpleNamespace(task=task, rev=rev, service=service, grant=grant, actors=actors,
                           context=context, runtime=runtime, foreground=foreground,
                           persisted=persisted, session=session)


async def idle(state):
    for _ in range(20):
        active = state.actors.tasks.get("sid-owner")
        if active is None:
            return
        await asyncio.gather(active, return_exceptions=True)
        await asyncio.sleep(0)
    raise AssertionError("actor did not release its slot")


def entry(state):
    return state.service.ledger.load(state.task.id).continuation["entries"][state.rev]


async def start(state, adapter):
    return await state.runtime.start(state.context, state.task.id, state.rev, state.grant["id"], adapter)


@pytest.mark.asyncio
async def test_real_check_budget_report_before_ack_and_no_task_acceptance(tmp_path):
    state, adapter = setup(tmp_path), Adapter()
    assert (await start(state, adapter))["started"]
    await idle(state)
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["task_id"] == state.task.id and saved["tainted"] is True
    assert saved["budget"]["requests"] == 1 and saved["budget"]["spent_tokens"] == 11
    assert entry(state)["status"] == "completed"
    assert entry(state)["budget"] == saved["budget"]
    assert CompletionInbox(str(tmp_path), "sid-owner").pending() == []
    assert state.service.ledger.load(state.task.id).status == "done"
    messages, kwargs = adapter.calls[0]
    assert "<task_delivery>" in messages[-1]["content"] and kwargs["max_tokens"] == 30
    assert state.actors.background == {}


@pytest.mark.asyncio
async def test_unsupported_provider_never_consumes_authorization(tmp_path):
    state = setup(tmp_path)
    result = await start(state, LLMClient)
    assert not result["started"] and "count_input_tokens" in result["missing"]
    assert entry(state)["status"] == "authorized" and not state.actors.tasks
    support = check_budget_support(Adapter())
    assert support == {"available": True, "missing": [], "reason": ""}


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["queued", "priority", "stopped", "ineligible"])
async def test_foreground_and_workspace_gates_keep_grant_unconsumed(tmp_path, blocked):
    state, adapter = setup(tmp_path), Adapter()
    if blocked == "queued":
        state.session["prompt_queue"].append(prompt("user"))
    elif blocked == "priority":
        state.actors.priority["sid-owner"] = prompt("urgent")
    elif blocked == "stopped":
        state.actors.cancel("sid-owner", reason="stop")
    else:
        state.runtime.eligible = lambda: False
    assert not (await start(state, adapter))["started"]
    assert entry(state)["status"] == "authorized" and adapter.calls == []


@pytest.mark.asyncio
async def test_foreground_preempts_running_check_and_preserves_unknown_usage(tmp_path):
    state, adapter = setup(tmp_path), Adapter()
    adapter.gate.clear()
    await start(state, adapter)
    await adapter.entered.wait()
    await state.actors.enqueue(state.context, prompt("new-user-input"))
    await idle(state)
    assert state.foreground == ["new-user-input"]
    record = entry(state)
    assert record["status"] == "cancelled"
    assert record["budget"]["usage_complete"] is False
    assert record["budget"]["committed_tokens"] == 40
    assert not state.persisted and CompletionInbox(str(tmp_path), "sid-owner").pending()


@pytest.mark.asyncio
async def test_stop_keeps_queue_and_blocks_new_auto_check_until_foreground_start(tmp_path):
    state, adapter = setup(tmp_path), Adapter()
    adapter.gate.clear()
    await start(state, adapter)
    await adapter.entered.wait()
    state.session["prompt_queue"].append(prompt("queued-user"))
    assert state.actors.cancel("sid-owner", reason="stop")
    await idle(state)
    assert state.foreground == [] and state.session["prompt_queue"][0]["id"] == "queued-user"
    assert "sid-owner" in state.actors.auto_stopped
    await state.actors.resume(state.context)
    await idle(state)
    assert state.foreground == ["queued-user"]
    assert "sid-owner" not in state.actors.auto_stopped


@pytest.mark.asyncio
async def test_cancel_before_first_timeslice_releases_slot_without_claim(tmp_path):
    state, adapter = setup(tmp_path), Adapter()
    await start(state, adapter)
    assert state.actors.cancel("sid-owner", reason="stop")
    await idle(state)
    assert entry(state)["status"] == "revoked" and not adapter.calls
    assert state.actors.background == {} and not state.actors.busy("sid-owner")


@pytest.mark.asyncio
async def test_new_input_before_first_timeslice_is_not_lost(tmp_path):
    state, adapter = setup(tmp_path), Adapter()
    await start(state, adapter)
    await state.actors.enqueue(state.context, prompt("first-user"))
    await idle(state)
    assert state.foreground == ["first-user"] and adapter.calls == []
    assert entry(state)["status"] == "authorized"


@pytest.mark.asyncio
async def test_prestart_cleanup_can_receive_stop_without_being_cancelled(tmp_path):
    state, adapter = setup(tmp_path), Adapter()
    await start(state, adapter)
    state.actors.cancel("sid-owner", reason="foreground")
    # Let the original task's done callback reserve cleanup, then stop it.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    state.actors.cancel("sid-owner", reason="stop")
    await idle(state)
    assert not state.actors.busy("sid-owner") and state.actors.background == {}
    assert "sid-owner" in state.actors.auto_stopped
    assert entry(state)["status"] == "revoked"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["report_write", "wrong_receipt", "ack_write"])
async def test_missing_durable_report_or_receipt_never_acknowledges(tmp_path, monkeypatch, failure):
    state, adapter = setup(tmp_path), Adapter()
    if failure == "report_write":
        async def broken(_):
            raise OSError("disk full")
        state.runtime.persist_report = broken
    elif failure == "wrong_receipt":
        async def wrong(payload):
            return PersistedCheckReport("id", "task-other", payload["revision"], payload["attempt_id"])
        state.runtime.persist_report = wrong
    else:
        monkeypatch.setattr(state.runtime.inbox.ledger, "save", lambda _: False)
    await start(state, adapter)
    await idle(state)
    assert entry(state)["status"] == "completed"  # Check completion != report delivery.
    assert CompletionInbox(str(tmp_path), "sid-owner").pending()
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_changed_result_during_request_never_reports_or_acks_new_version(tmp_path):
    state, adapter = setup(tmp_path), Adapter()
    adapter.gate.clear()
    await start(state, adapter)
    await adapter.entered.wait()
    task = state.service.ledger.load(state.task.id)
    task.result = "new result"
    state.service.ledger.save(task)
    adapter.gate.set()
    await idle(state)
    assert entry(state)["status"] == "failed" and not state.persisted
    assert CompletionInbox(str(tmp_path), "sid-owner").pending()[0]["revision"] != state.rev


@pytest.mark.asyncio
async def test_request_failure_records_unknown_usage_without_retry(tmp_path):
    state, adapter = setup(tmp_path), Adapter()
    adapter.error = OSError("network failed")
    await start(state, adapter)
    await idle(state)
    assert entry(state)["status"] == "failed"
    assert entry(state)["budget"]["usage_complete"] is False
    assert len(adapter.calls) == 1 and not state.persisted


@pytest.mark.asyncio
async def test_claim_save_failure_never_invokes_model(tmp_path, monkeypatch):
    state, adapter = setup(tmp_path), Adapter()
    monkeypatch.setattr(state.service.ledger, "save", lambda _: False)
    await start(state, adapter)
    await idle(state)
    assert entry(state)["status"] == "authorized" and adapter.calls == []


@pytest.mark.asyncio
async def test_actor_identity_and_mutated_budget_records_fail_closed(tmp_path):
    state, adapter = setup(tmp_path), Adapter()
    wrong = ActorContext("sid-other", lambda: {}, lambda: None, lambda: None,
                         state.context.publish, state.context.execute)
    with pytest.raises(ValueError, match="会话"):
        await state.runtime.start(wrong, state.task.id, state.rev, state.grant["id"], adapter)
    task = state.service.ledger.load(state.task.id)
    task.continuation["entries"][state.rev]["limits"] = asdict(CheckLimits()) | {"tokens": True}
    state.service.ledger.save(task)
    with pytest.raises(ValueError, match="预算"):
        await start(state, adapter)
    assert not adapter.calls


@pytest.mark.asyncio
async def test_incomplete_grant_never_reserves_actor_or_calls_provider(tmp_path):
    state, adapter = setup(tmp_path), Adapter()
    task = state.service.ledger.load(state.task.id)
    del task.continuation["entries"][state.rev]["limits"]["output_tokens"]
    state.service.ledger.save(task)
    with pytest.raises(ValueError, match="预算"):
        await start(state, adapter)
    assert not adapter.calls and not state.actors.tasks
    assert entry(state)["status"] == "authorized" and not state.persisted


@pytest.mark.asyncio
async def test_provider_contract_violation_keeps_actual_overage_and_unread_handoff(tmp_path):
    state, adapter = setup(tmp_path), Adapter()

    async def violating_chat(messages, **kwargs):
        adapter.calls.append((messages, kwargs))
        return {"content": "不能汇报为成功", "tool_calls": None,
                "usage": {"prompt_tokens": 400, "completion_tokens": 1, "total_tokens": 401}}

    adapter.chat = violating_chat
    assert (await start(state, adapter))["started"]
    await idle(state)
    saved = entry(state)
    assert saved["status"] == "failed"
    assert saved["budget"]["spent_tokens"] == saved["budget"]["committed_tokens"] == 401
    assert saved["budget"]["blocked_reason"]
    assert len(adapter.calls) == 1 and not state.persisted
    assert CompletionInbox(str(tmp_path), "sid-owner").pending()


@pytest.mark.asyncio
async def test_duplicate_observers_share_slot_and_do_not_double_call(tmp_path):
    state, adapter = setup(tmp_path), Adapter()
    adapter.gate.clear()
    results = await asyncio.gather(start(state, adapter), start(state, adapter))
    assert sum(item["started"] for item in results) == 1
    await adapter.entered.wait()
    assert len(adapter.calls) == 1
    adapter.gate.set()
    await idle(state)


@pytest.mark.asyncio
async def test_changed_eligibility_at_execution_boundary_does_not_claim(tmp_path):
    state, adapter = setup(tmp_path), Adapter()
    assert (await start(state, adapter))["started"]
    state.runtime.eligible = lambda: False
    await idle(state)
    assert entry(state)["status"] == "authorized" and not adapter.calls


@pytest.mark.asyncio
async def test_cancellation_swallowed_by_executor_cannot_publish_or_ack(tmp_path):
    state, adapter = setup(tmp_path), Adapter()
    entered = asyncio.Event()

    async def swallow(*args, limits, on_budget):
        budget = {"limits": asdict(limits), "requests": 1, "tool_calls": 0,
                  "spent_tokens": 11, "committed_tokens": 11,
                  "usage_complete": True, "blocked_reason": ""}
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
        on_budget(budget)
        return {"result": "late result", "budget": budget}

    state.runtime.execute = swallow
    await start(state, adapter)
    await entered.wait()
    state.actors.cancel("sid-owner", reason="stop")
    await idle(state)
    assert entry(state)["status"] == "cancelled" and not state.persisted
    assert CompletionInbox(str(tmp_path), "sid-owner").pending()


@pytest.mark.asyncio
async def test_cancel_during_report_persistence_leaves_delivery_unread(tmp_path):
    state, adapter = setup(tmp_path), Adapter()
    entered = asyncio.Event()

    async def delayed_report(payload):
        entered.set()
        await asyncio.Future()

    state.runtime.persist_report = delayed_report
    await start(state, adapter)
    await entered.wait()
    state.actors.cancel("sid-owner", reason="stop")
    await idle(state)
    assert entry(state)["status"] == "completed"
    assert CompletionInbox(str(tmp_path), "sid-owner").pending()


@pytest.mark.asyncio
async def test_revocation_during_request_preserves_actual_usage_without_report(tmp_path):
    state, adapter = setup(tmp_path), Adapter()
    adapter.gate.clear()
    await start(state, adapter)
    await adapter.entered.wait()
    state.service.revoke(state.task.id, state.rev, state.grant["id"])
    adapter.gate.set()
    await idle(state)
    assert entry(state)["status"] == "revoked" and entry(state)["budget"]["spent_tokens"] == 11
    assert not state.persisted


@pytest.mark.asyncio
async def test_prestart_revoke_failure_is_visible_and_still_releases_slot(tmp_path, monkeypatch, caplog):
    state, adapter = setup(tmp_path), Adapter()
    await start(state, adapter)
    monkeypatch.setattr(state.service.ledger, "save", lambda _: False)
    state.actors.cancel("sid-owner", reason="stop")
    await idle(state)
    assert entry(state)["status"] == "authorized" and not state.actors.busy("sid-owner")
    assert "could not be persisted" in caplog.text and not adapter.calls
