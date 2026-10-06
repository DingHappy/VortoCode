import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from src.gateway.check_reports import CheckReportWriter
from src.gateway.continuation_checks import ContinuationChecks
from src.gateway.continuations import ContinuationConflict, ContinuationService
from src.gateway.handoffs import CompletionInbox, revision
from src.gateway.session_actor import ActorContext, SessionActors
from src.gateway.sessions import SessionTable
from src.gateway.tasks import TaskLedger
from src.llm.hard_budget import CheckLimits
from src.web import session_store as ss


class Agent:
    def __init__(self):
        self.history = [{"role": "user", "content": "existing context"}]
        self.plan = [{"step": "保留原计划", "status": "pending"}]
        self.tools = {}


def setup(root):
    ledger = TaskLedger(str(root))
    task = ledger.create("dev", "目标", owner_session="sid-owner")
    task.status, task.result = "done", "交付"
    ledger.save(task)
    rev = revision(task)
    service = ContinuationService(str(root), "sid-owner")
    grant = service.authorize(task.id, rev)
    claim = service.claim(task.id, rev, grant["id"])
    budget = {"limits": asdict(CheckLimits()), "requests": 1, "tool_calls": 0,
              "spent_tokens": 11, "committed_tokens": 11,
              "usage_complete": True, "blocked_reason": ""}
    service.finish(task.id, rev, grant["id"], claim["attempt_id"], outcome="completed",
                   report="核对完成，原生运行证据待采集。", budget=budget)
    payload = {"task_id": task.id, "revision": rev, "authorization_id": grant["id"],
               "attempt_id": claim["attempt_id"], "result": "核对完成，原生运行证据待采集。",
               "budget": budget, "tainted": True}
    table = SessionTable()
    session = table.get("sid-owner", repo_root=str(root), factory=Agent)
    session["transcript"] = [{"role": "user", "text": "目标", "rid": "user-1"}]
    session["activities"] = [{"type": "agent_phase", "id": "user-1:thinking", "status": "completed"}]
    session["prompt_queue"] = [{"id": "next", "text": "前台要求", "mode": "plan"}]
    assert table.persist("sid-owner", str(root)) is True
    assert ss.rename_session(str(root), "owner", "原会话标题")
    writer = CheckReportWriter(table, str(root), "sid-owner")
    return SimpleNamespace(task=task, service=service, payload=payload, table=table,
                           session=session, writer=writer, root=str(root))


def test_durable_report_preserves_session_state_and_restores_after_restart(tmp_path):
    state = setup(tmp_path)
    original_history = copy.deepcopy(state.session["agent"].history)
    receipt = state.writer.persist(state.payload)
    saved = ss.load_session(state.root, "sid-owner")
    assert saved["transcript"][-1]["rid"] == receipt.report_id
    assert saved["transcript"][-1]["check_report"]["tainted"] is True
    assert saved["transcript"][-1]["check_report"]["task_id"] == state.task.id
    assert saved["history"] == original_history == state.session["agent"].history
    assert saved["plan"] == state.session["agent"].plan
    assert saved["activities"] == state.session["activities"]
    assert saved["prompt_queue"] == state.session["prompt_queue"]
    assert saved["title"] == "原会话标题"
    restored = SessionTable().get("sid-owner", repo_root=state.root, factory=Agent)
    assert restored["transcript"] == saved["transcript"]
    # A durable display report does not itself acknowledge the task.
    assert CompletionInbox(state.root, "sid-owner").pending()


def test_report_is_idempotent_across_concurrent_delivery_and_restart(tmp_path):
    state = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: state.writer.persist(state.payload), range(8)))
    assert len({receipt.report_id for receipt in receipts}) == 1
    assert len(state.session["transcript"]) == 2
    table = SessionTable()
    table.get("sid-owner", repo_root=state.root, factory=Agent)
    restored = CheckReportWriter(table, state.root, "sid-owner")
    assert restored.persist(state.payload) == receipts[0]
    assert len(table.peek("sid-owner")["transcript"]) == 2


def test_failed_disk_save_neither_changes_live_transcript_nor_returns_receipt(tmp_path, monkeypatch):
    state = setup(tmp_path)
    before = copy.deepcopy(ss.load_session(state.root, "sid-owner"))
    before_live = copy.deepcopy(state.session["transcript"])
    monkeypatch.setattr(ss, "save_session", lambda *args, **kwargs: False)
    with pytest.raises(OSError, match="持久"):
        state.writer.persist(state.payload)
    assert ss.load_session(state.root, "sid-owner") == before
    assert state.session["transcript"] == before_live
    assert state.table.persist("sid-owner", state.root) is False
    assert CompletionInbox(state.root, "sid-owner").pending()


@pytest.mark.parametrize("field,value", [
    ("result", "伪造的结论"), ("tainted", False), ("attempt_id", "attempt-wrong"),
    ("revision", "old-result"), ("authorization_id", "check-wrong"),
    ("budget", {}), ("task_id", "task-missing"),
])
def test_forged_or_stale_reports_never_enter_transcript(tmp_path, field, value):
    state = setup(tmp_path)
    payload = copy.deepcopy(state.payload) | {field: value}
    with pytest.raises(ContinuationConflict):
        state.writer.persist(payload)
    assert len(state.session["transcript"]) == 1


def test_owner_root_closed_session_and_invalid_stable_id_fail_closed(tmp_path):
    state = setup(tmp_path)
    with pytest.raises(ContinuationConflict):
        CheckReportWriter(state.table, state.root, "sid-../../owner")
    with pytest.raises(ContinuationConflict):
        CheckReportWriter(state.table, state.root, "sid-other").persist(state.payload)
    state.session["repo_root"] = str(tmp_path / "other-project")
    with pytest.raises(OSError):
        state.writer.persist(state.payload)
    assert ss.load_session(state.root, "sid-owner")["transcript"][-1]["role"] == "user"
    state.table.pop("sid-owner")
    with pytest.raises(OSError):
        state.writer.persist(state.payload)


def test_report_bounds_and_conflicting_identity_do_not_overwrite_saved_text(tmp_path):
    state = setup(tmp_path)
    state.session["transcript"] = [{"role": "user", "text": str(i)} for i in range(205)]
    receipt = state.writer.persist(state.payload)
    assert len(state.session["transcript"]) == 200
    assert len(ss.load_session(state.root, "sid-owner")["transcript"]) == 200
    state.session["transcript"][-1]["text"] = "tampered text"
    with pytest.raises(OSError):
        state.writer.persist(state.payload)
    saved = ss.load_session(state.root, "sid-owner")["transcript"][-1]
    assert saved["rid"] == receipt.report_id and saved["text"] != "tampered text"


def test_corrupt_completed_budget_cannot_issue_report_receipt(tmp_path):
    state = setup(tmp_path)
    task = state.service.ledger.load(state.task.id)
    entry = task.continuation["entries"][state.payload["revision"]]
    entry["limits"]["tokens"] = True
    entry["budget"]["limits"]["tokens"] = True
    state.service.ledger.save(task)
    with pytest.raises(ContinuationConflict, match="预算"):
        state.writer.persist(state.payload)
    assert len(state.session["transcript"]) == 1


@pytest.mark.parametrize("changes", [
    {"usage_complete": False}, {"blocked_reason": "已阻塞"},
    {"committed_tokens": 12}, {"spent_tokens": 8001, "committed_tokens": 8001},
    {"spent_tokens": 12, "committed_tokens": 11},
])
def test_recovery_cannot_publish_corrupt_or_unsettled_completed_budget(tmp_path, changes):
    state = setup(tmp_path)
    task = state.service.ledger.load(state.task.id)
    entry = task.continuation["entries"][state.payload["revision"]]
    entry["budget"].update(changes)
    state.service.ledger.save(task)
    state.payload["budget"] = copy.deepcopy(entry["budget"])
    with pytest.raises(ContinuationConflict, match="预算|用量"):
        state.writer.persist(state.payload)
    assert len(state.session["transcript"]) == 1
    assert CompletionInbox(state.root, "sid-owner").pending()


async def idle(actors):
    for _ in range(20):
        task = actors.tasks.get("sid-owner")
        if task is None:
            return
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
    raise AssertionError("actor stayed occupied")


@pytest.mark.asyncio
async def test_report_only_recovery_repairs_failed_ack_without_model_reexecution(tmp_path, monkeypatch):
    state = setup(tmp_path)
    state.session["prompt_queue"] = []
    actors = SessionActors(clean_item=lambda raw: raw)
    calls = []

    async def publish():
        pass

    async def no_model(*args, **kwargs):
        calls.append("forbidden")
        raise AssertionError("redelivery must not call model")

    async def writer(payload):
        return state.writer.persist(payload)

    context = ActorContext("sid-owner", lambda: state.session, lambda: None, lambda: None, publish, no_model)
    runtime = ContinuationChecks(actors, state.service, execute=no_model,
                                 persist_report=writer, eligible=lambda: True)
    saved_write = runtime.inbox.ledger.save
    monkeypatch.setattr(runtime.inbox.ledger, "save", lambda _: False)
    args = (context, state.task.id, state.payload["revision"], state.payload["authorization_id"], state.payload["attempt_id"])
    assert (await runtime.resume_report(*args))["started"]
    await idle(actors)
    assert len(state.session["transcript"]) == 2
    assert runtime.inbox.pending()
    monkeypatch.setattr(runtime.inbox.ledger, "save", saved_write)
    assert (await runtime.resume_report(*args))["started"]
    await idle(actors)
    assert not calls and runtime.inbox.pending() == []
    assert len(state.session["transcript"]) == 2
    assert state.service.ledger.load(state.task.id).continuation["entries"][state.payload["revision"]]["status"] == "completed"


@pytest.mark.asyncio
async def test_report_recovery_respects_foreground_and_stale_result(tmp_path):
    state = setup(tmp_path)
    actors = SessionActors(clean_item=lambda raw: raw)

    async def publish():
        pass

    async def forbidden(*args, **kwargs):
        raise AssertionError("busy recovery must not write or execute")

    context = ActorContext("sid-owner", lambda: state.session, lambda: None, lambda: None, publish, forbidden)
    runtime = ContinuationChecks(actors, state.service, execute=forbidden,
                                 persist_report=forbidden, eligible=lambda: True)
    args = (context, state.task.id, state.payload["revision"], state.payload["authorization_id"], state.payload["attempt_id"])
    assert not (await runtime.resume_report(*args))["started"]
    state.session["prompt_queue"] = []
    task = state.service.ledger.load(state.task.id)
    task.result = "new delivery"
    state.service.ledger.save(task)
    with pytest.raises(ValueError):
        await runtime.resume_report(*args)
    assert len(state.session["transcript"]) == 1


@pytest.mark.asyncio
async def test_web_report_writer_replay_and_broadcast_failure_keeps_durability(tmp_path, monkeypatch):
    from src.web.routers import realtime
    state = setup(tmp_path)
    monkeypatch.setattr(realtime, "_TABLE", state.table)
    published = []

    async def publish(key, event, **kwargs):
        published.append((key, event))
        raise OSError("observer disconnected")

    monkeypatch.setattr(realtime, "_publish_session_event", publish)
    receipt = await realtime._persist_check_report("sid-owner", state.payload)
    assert published[0][0] == "sid-owner"
    event = published[0][1]
    assert event["type"] == "agent_emit" and event["rid"] == receipt.report_id
    assert event["tainted"] is True
    saved = ss.load_session(state.root, "sid-owner")
    assert saved["transcript"][-1]["text"] == event["text"]


def test_session_filename_aliases_are_rejected_before_report_storage(tmp_path):
    state = setup(tmp_path)
    for alias in ("sid-_owner", "sid-owner_", "sid-___"):
        with pytest.raises(ContinuationConflict):
            CheckReportWriter(state.table, state.root, alias)
    with pytest.raises(ContinuationConflict):
        state.writer.persist(state.payload | {"task_id": state.task.id + "_"})
    assert len(state.session["transcript"]) == 1


def test_corrupt_attempt_identity_cannot_create_report_receipt(tmp_path):
    state = setup(tmp_path)
    task = state.service.ledger.load(state.task.id)
    task.continuation["entries"][state.payload["revision"]]["attempt_id"] = {"bad": "identity"}
    state.service.ledger.save(task)
    with pytest.raises(ContinuationConflict, match="身份"):
        state.writer.persist(state.payload | {"attempt_id": {"bad": "identity"}})
    assert len(state.session["transcript"]) == 1


@pytest.mark.asyncio
async def test_web_history_replay_includes_durable_report_after_restart(tmp_path, monkeypatch):
    from src.web.routers import realtime
    state = setup(tmp_path)
    receipt = state.writer.persist(state.payload)
    restored = SessionTable()
    restored.get("sid-owner", repo_root=state.root, factory=Agent)
    monkeypatch.setattr(realtime, "_TABLE", restored)
    monkeypatch.setattr(realtime, "_SESSIONS", restored.data)
    monkeypatch.chdir(tmp_path)
    sent = []

    class WebSocket:
        query_params = {"sid": "owner"}

        async def send_json(self, event):
            sent.append(event)

    async def publish(key, event, **kwargs):
        sent.append(event)

    monkeypatch.setattr(realtime, "_publish_session_event", publish)
    await realtime._replay_history(WebSocket())
    history = next(event for event in sent if event["type"] == "agent_history")
    report = next(item for item in history["items"] if item.get("rid") == receipt.report_id)
    assert report["check_report"]["task_id"] == state.task.id
    assert report["check_report"]["tainted"] is True
    assert state.payload["result"] in report["text"]
