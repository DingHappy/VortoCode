from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest

from src.gateway.continuations import ContinuationConflict, ContinuationService, recover_claims
from src.gateway.handoffs import CompletionInbox, revision
from src.gateway.tasks import TaskLedger, TaskRunner
from src.llm.hard_budget import CheckLimits


def setup(root):
    ledger = TaskLedger(str(root))
    task = ledger.create("dev", "检查交付", owner_session="sid-owner")
    task.status, task.result = "done", "结果"
    assert ledger.save(task)
    service = ContinuationService(str(root), "sid-owner")
    return task, revision(task), service


def budget_for(limits=None, **changes):
    return {"limits": asdict(limits or CheckLimits()), "requests": 1, "tool_calls": 0,
            "spent_tokens": 11, "committed_tokens": 11,
            "usage_complete": True, "blocked_reason": ""} | changes


def test_authorize_restart_once_only_and_receipt_independence(tmp_path):
    task, rev, service = setup(tmp_path)
    grant = service.authorize(task.id, rev, limits=CheckLimits(tokens=200))
    assert service.authorize(task.id, rev, limits=CheckLimits(tokens=200)) == grant
    restored = ContinuationService(str(tmp_path), "sid-owner")
    claim = restored.claim(task.id, rev, grant["id"])
    with pytest.raises(ContinuationConflict):
        service.claim(task.id, rev, grant["id"])
    done = restored.finish(task.id, rev, grant["id"], claim["attempt_id"],
                           outcome="completed", report="核对完成，待向用户汇报",
                           budget=budget_for(CheckLimits(tokens=200)))
    assert done["status"] == "completed"
    assert CompletionInbox(str(tmp_path), "sid-owner").pending()[0]["revision"] == rev
    assert restored.ledger.load(task.id).status == "done"
    with pytest.raises(ContinuationConflict):
        restored.authorize(task.id, rev)


def test_competing_services_only_one_durable_claim(tmp_path):
    task, rev, service = setup(tmp_path)
    grant = service.authorize(task.id, rev)
    def claim(_):
        try:
            return ContinuationService(str(tmp_path), "sid-owner").claim(task.id, rev, grant["id"])
        except ContinuationConflict:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))
    assert sum(item is not None for item in results) == 1


def test_revocation_invalidates_late_completion_and_cannot_regrant(tmp_path):
    task, rev, service = setup(tmp_path)
    grant = service.authorize(task.id, rev)
    claim = service.claim(task.id, rev, grant["id"])
    revoked = service.revoke(task.id, rev, grant["id"])
    assert service.revoke(task.id, rev, grant["id"]) == revoked
    with pytest.raises(ContinuationConflict):
        service.finish(task.id, rev, grant["id"], claim["attempt_id"], outcome="completed", report="迟到结果")
    with pytest.raises(ContinuationConflict):
        service.authorize(task.id, rev)


def test_new_result_requires_explicit_grant_and_rejects_old_finish(tmp_path):
    task, rev, service = setup(tmp_path)
    grant = service.authorize(task.id, rev)
    claim = service.claim(task.id, rev, grant["id"])
    task = service.ledger.load(task.id)
    task.result = "新交付"
    service.ledger.save(task)
    new_rev = revision(task)
    with pytest.raises(ContinuationConflict):
        service.finish(task.id, rev, grant["id"], claim["attempt_id"], outcome="completed", report="旧结果")
    with pytest.raises(ContinuationConflict):
        service.claim(task.id, new_rev, grant["id"])
    service.revoke(task.id, rev, grant["id"])
    assert service.authorize(task.id, new_rev)["id"] != grant["id"]


def test_owner_wrong_attempt_handled_and_nonterminal_guards(tmp_path):
    task, rev, service = setup(tmp_path)
    with pytest.raises(ContinuationConflict):
        ContinuationService(str(tmp_path), "sid-other").authorize(task.id, rev)
    grant = service.authorize(task.id, rev)
    claim = service.claim(task.id, rev, grant["id"])
    with pytest.raises(ContinuationConflict):
        service.finish(task.id, rev, grant["id"], "wrong", outcome="failed", report="失败")
    CompletionInbox(str(tmp_path), "sid-owner").acknowledge(task.id, rev, "手动完成处理")
    with pytest.raises(ContinuationConflict):
        service.finish(task.id, rev, grant["id"], claim["attempt_id"], outcome="completed", report="重复处理")
    service.revoke(task.id, rev, grant["id"])
    queued = service.ledger.create("dev", "未结束", owner_session="sid-owner")
    with pytest.raises(ContinuationConflict):
        service.authorize(queued.id, revision(queued))


@pytest.mark.parametrize("operation", ["authorize", "claim", "finish", "revoke"])
def test_failed_save_keeps_durable_previous_state(tmp_path, monkeypatch, operation):
    task, rev, service = setup(tmp_path)
    grant = None if operation == "authorize" else service.authorize(task.id, rev)
    claim = service.claim(task.id, rev, grant["id"]) if operation == "finish" else None
    before = service.ledger.load(task.id).continuation
    monkeypatch.setattr(service.ledger, "save", lambda _: False)
    with pytest.raises(OSError):
        if operation == "authorize":
            service.authorize(task.id, rev)
        elif operation == "claim":
            service.claim(task.id, rev, grant["id"])
        elif operation == "finish":
            service.finish(task.id, rev, grant["id"], claim["attempt_id"], outcome="failed", report="失败")
        else:
            service.revoke(task.id, rev, grant["id"])
    assert TaskLedger(str(tmp_path)).load(task.id).continuation == before


def test_startup_reconciles_claim_without_retry_or_acknowledgement(tmp_path):
    task, rev, service = setup(tmp_path)
    grant = service.authorize(task.id, rev)
    service.claim(task.id, rev, grant["id"])
    runner = TaskRunner(str(tmp_path), None)
    notifications = []
    runner.subscribe(notifications.append)
    assert [item.id for item in runner.recover()] == [task.id]
    assert len(notifications) == 1
    entry = service.ledger.load(task.id).continuation["entries"][rev]
    assert entry["status"] == "interrupted"
    assert runner.active_count == 0 and runner.recover() == []
    assert CompletionInbox(str(tmp_path), "sid-owner").pending()
    with pytest.raises(ContinuationConflict):
        service.claim(task.id, rev, grant["id"])


def test_corrupt_budget_closed_and_recovery_save_failure(tmp_path, monkeypatch):
    task, rev, service = setup(tmp_path)
    grant = service.authorize(task.id, rev)
    stored = service.ledger.load(task.id)
    stored.continuation["entries"][rev]["limits"]["tokens"] = True
    service.ledger.save(stored)
    with pytest.raises(ContinuationConflict, match="预算"):
        service.claim(task.id, rev, grant["id"])
    stored.continuation["entries"][rev]["limits"]["tokens"] = 100
    service.ledger.save(stored)
    service.claim(task.id, rev, grant["id"])
    monkeypatch.setattr(service.ledger, "save", lambda _: False)
    with pytest.raises(OSError):
        recover_claims(service.ledger)
    assert service.ledger.load(task.id).continuation["entries"][rev]["status"] == "claimed"


def test_transports_cannot_undo_grant_and_return_is_detached(tmp_path):
    task, rev, service = setup(tmp_path)
    def broken(_):
        raise RuntimeError("offline")
    service._on_update = broken
    grant = service.authorize(task.id, rev)
    grant["limits"]["tokens"] = 1
    stored = service.ledger.load(task.id).continuation["entries"][rev]
    assert stored["limits"]["tokens"] == 8000


def test_corrupt_records_and_history_capacity_do_not_reset_authority(tmp_path):
    task, rev, service = setup(tmp_path)
    task.continuation = "corrupt"
    service.ledger.save(task)
    with pytest.raises(ContinuationConflict, match="损坏"):
        service.authorize(task.id, rev)
    task.continuation = {"entries": {str(i): {"status": "interrupted"} for i in range(20)}}
    service.ledger.save(task)
    with pytest.raises(ContinuationConflict, match="上限"):
        service.authorize(task.id, rev)


def test_recovery_keeps_unclaimed_authorization_without_scheduling(tmp_path):
    task, rev, service = setup(tmp_path)
    grant = service.authorize(task.id, rev)
    runner = TaskRunner(str(tmp_path), None)
    assert runner.recover() == [] and runner.active_count == 0
    assert service.ledger.load(task.id).continuation["entries"][rev] == grant


@pytest.mark.parametrize("field", list(CheckLimits.__dataclass_fields__))
def test_missing_budget_field_is_not_filled_by_defaults_before_claim(tmp_path, field):
    task, rev, service = setup(tmp_path)
    grant = service.authorize(task.id, rev, limits=CheckLimits(output_tokens=20))
    saved = service.ledger.load(task.id)
    del saved.continuation["entries"][rev]["limits"][field]
    service.ledger.save(saved)
    for read in (service.pending_authorization, service.claim):
        with pytest.raises(ContinuationConflict, match="预算"):
            read(task.id, rev, grant["id"])
    assert service.ledger.load(task.id).continuation["entries"][rev]["status"] == "authorized"


@pytest.mark.parametrize("operation", ["authorize", "pending", "claim"])
def test_reset_status_does_not_overwrite_an_existing_attempt(tmp_path, operation):
    task, rev, service = setup(tmp_path)
    grant = service.authorize(task.id, rev)
    claim = service.claim(task.id, rev, grant["id"])
    saved = service.ledger.load(task.id)
    saved.continuation["entries"][rev]["status"] = "authorized"
    service.ledger.save(saved)
    before = service.ledger.load(task.id).continuation
    with pytest.raises(ContinuationConflict, match="尝试"):
        if operation == "authorize":
            service.authorize(task.id, rev)
        elif operation == "pending":
            service.pending_authorization(task.id, rev, grant["id"])
        else:
            service.claim(task.id, rev, grant["id"])
    assert service.ledger.load(task.id).continuation == before
    # Stopping this corrupt record remains possible, without new execution.
    assert service.revoke(task.id, rev, grant["id"])["attempt_id"] == claim["attempt_id"]


@pytest.mark.parametrize("identity", [None, 1, "invalid", "check-"])
def test_corrupt_grant_cannot_be_replayed_as_valid_authorization(tmp_path, identity):
    task, rev, service = setup(tmp_path)
    service.authorize(task.id, rev)
    saved = service.ledger.load(task.id)
    saved.continuation["entries"][rev]["id"] = identity
    service.ledger.save(saved)
    before = service.ledger.load(task.id).continuation
    with pytest.raises(ContinuationConflict, match="身份"):
        service.authorize(task.id, rev)
    assert service.ledger.load(task.id).continuation == before


@pytest.mark.parametrize("invalid", [
    None,
    {"usage_complete": False},
    {"blocked_reason": "计量违反合同"},
    {"committed_tokens": 12},
    {"spent_tokens": 12, "committed_tokens": 11},
    {"spent_tokens": 8001, "committed_tokens": 8001},
])
def test_completed_check_requires_settled_budget_within_limit(tmp_path, invalid):
    task, rev, service = setup(tmp_path)
    grant = service.authorize(task.id, rev)
    claim = service.claim(task.id, rev, grant["id"])
    before = service.ledger.load(task.id).continuation
    with pytest.raises(ContinuationConflict, match="预算|用量"):
        service.finish(task.id, rev, grant["id"], claim["attempt_id"], outcome="completed",
                       report="不能当成完成", budget=None if invalid is None else budget_for(**invalid))
    assert service.ledger.load(task.id).continuation == before
    assert CompletionInbox(str(tmp_path), "sid-owner").pending()


def test_failed_provider_can_preserve_actual_overage_without_success_report(tmp_path):
    task, rev, service = setup(tmp_path)
    grant = service.authorize(task.id, rev, limits=CheckLimits(tokens=100, output_tokens=20))
    claim = service.claim(task.id, rev, grant["id"])
    budget = budget_for(CheckLimits(tokens=100, output_tokens=20), spent_tokens=150,
                        committed_tokens=150, blocked_reason="provider 违反输出封顶合同")
    closed = service.terminate(task.id, rev, grant["id"], claim["attempt_id"], outcome="failed", budget=budget)
    assert closed["budget"] == budget and closed["status"] == "failed"
    with pytest.raises(ContinuationConflict):
        service.completed_report(task.id, rev, grant["id"], claim["attempt_id"])
    assert CompletionInbox(str(tmp_path), "sid-owner").pending()


def test_usage_budget_boolean_cannot_equal_valid_integer_limit(tmp_path):
    task, rev, service = setup(tmp_path)
    limits = CheckLimits(tokens=1)
    grant = service.authorize(task.id, rev, limits=limits)
    claim = service.claim(task.id, rev, grant["id"])
    snapshot = budget_for(limits, spent_tokens=1, committed_tokens=1)
    snapshot["limits"]["tokens"] = True  # True == 1 must not bypass validation.
    with pytest.raises(ContinuationConflict, match="预算"):
        service.finish(task.id, rev, grant["id"], claim["attempt_id"], outcome="completed",
                       report="不能放行", budget=snapshot)
    assert service.ledger.load(task.id).continuation["entries"][rev]["status"] == "claimed"


@pytest.mark.parametrize("operation", ["finish", "terminate", "report"])
def test_missing_attempt_identity_cannot_match_corrupt_stored_identity(tmp_path, operation):
    task, rev, service = setup(tmp_path)
    grant = service.authorize(task.id, rev)
    service.claim(task.id, rev, grant["id"])
    saved = service.ledger.load(task.id)
    saved.continuation["entries"][rev]["attempt_id"] = None
    service.ledger.save(saved)
    before = service.ledger.load(task.id).continuation
    with pytest.raises(ContinuationConflict, match="尝试身份"):
        if operation == "finish":
            service.finish(task.id, rev, grant["id"], None, outcome="completed", report="结果", budget=budget_for())
        elif operation == "terminate":
            service.terminate(task.id, rev, grant["id"], None, outcome="cancelled")
        else:
            service.completed_report(task.id, rev, grant["id"], None)
    assert service.ledger.load(task.id).continuation == before
