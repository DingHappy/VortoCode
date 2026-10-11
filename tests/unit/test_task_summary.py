"""Lifecycle summaries are observations, never acceptance/handling writes."""
from copy import deepcopy

from src.workflow.tasks.handoffs import CompletionInbox, revision
from src.workflow.tasks.task_dependencies import _input
from src.workflow.tasks.task_summary import SnapshotSources, task_summary
from src.workflow.tasks.tasks import BackgroundTask, TaskLedger


def task(name="source", *, status="done", kind="delegation", review="accepted"):
    value = BackgroundTask.new(kind, name, tid="task-" + name, owner_session="sid-owner")
    value.status, value.result = status, "result " + name
    if kind == "delegation":
        value.collaboration = {"round": 1, "assignee": "research", "review": review,
                               "acceptance": ["evidence"], "tainted": False, "dispatch": {"source": "api"}}
    return value


def handled(value):
    value.handoff_receipt = {"revision": revision(value), "note": "checked", "handled_at": "2026-10-07T00:00:00+00:00"}
    return value


def child(parent):
    value = task("child")
    refs = [{"task_id": parent.id, "round": 1}]
    value.collaboration["dispatch"]["depends_on"] = refs
    value.dependencies = {"version": 1, "task_id": value.id, "owner_session": value.owner_session,
                          "requires": refs, "resolution": "consumed", "released_round": 1, "inputs": [_input(parent)]}
    return handled(value)


def summary(value, *others, budget=2048):
    return task_summary(value, SnapshotSources([value, *others], budget=budget))


def test_acceptance_and_handoff_are_independent_and_old_receipts_survive_review():
    value = handled(task(review="pending"))
    original_receipt = deepcopy(value.handoff_receipt)
    assert summary(value)["column"] == "attention"
    assert summary(value)["review"] == "pending" and summary(value)["handling"] == "handled"
    value.collaboration["review"] = "accepted"
    assert summary(value)["column"] == "completed"
    assert value.handoff_receipt == original_receipt
    value.collaboration["review"] = "rework_requested"
    assert summary(value)["column"] == "attention"
    assert summary(task())["label"] == "已验收 · 待处理交接"


def test_dev_done_handled_is_not_acceptance_or_goal_achievement():
    value = handled(task(kind="dev"))
    view = summary(value)
    assert view["column"] == "completed" and view["review"] == "not_applicable"
    assert "不代表验收" in view["label"] and "goal" not in view
    value.result = "new result"
    assert summary(value)["handling"] == "pending" and summary(value)["column"] == "attention"


def test_round_change_same_text_and_corrupt_receipt_do_not_hide_new_work():
    value = handled(task())
    value.collaboration["round"] = 2
    assert summary(value)["handling"] == "pending"
    value.handoff_receipt = {"revision": revision(value), "note": "checked", "handled_at": "bad"}
    assert summary(value)["column"] == "attention"
    value.collaboration["round"] = True
    assert summary(value)["review"] == "unknown"
    value.owner_session = ""
    assert summary(value)["handling"] == "unknown"


def test_dependency_validity_reuses_domain_rules_and_preserves_history():
    parent = task()
    value = child(parent)
    before = deepcopy(value.to_dict())
    assert summary(value, parent)["column"] == "completed"
    assert summary(value, parent)["validity"] == "valid"
    parent.result = "updated external result"
    assert summary(value, parent)["validity"] == "invalid"
    assert summary(value, parent)["column"] == "attention"
    assert value.to_dict() == before


def test_missing_prefix_and_lookup_budget_are_unknown_not_invalid_or_complete():
    parent = task()
    value = child(parent)
    for view in [summary(value), summary(value, parent, budget=0)]:
        assert view["validity"] == "unknown" and view["column"] == "attention"
    value.dependencies["inputs"] = None
    assert summary(value, parent)["validity"] == "unknown"


def test_persisted_invalidation_never_recovers_after_parent_restored():
    parent = task(); value = child(parent)
    value.dependencies.update(resolution="invalidated", invalidation={"task_id": value.id,
        "owner_session": value.owner_session, "round": 1, "reason": "old input invalid", "at": "2026-10-07T00:00:00+00:00"})
    view = summary(value)
    assert view["validity"] == "invalid" and view["review"] == "accepted" and value.result
    assert summary(value, parent)["column"] == "attention"


def test_restart_retains_handled_current_result_but_not_a_changed_result(tmp_path):
    ledger = TaskLedger(str(tmp_path)); value = task()
    assert ledger.save(value)
    inbox = CompletionInbox(str(tmp_path), "sid-owner")
    inbox.acknowledge(value.id, revision(value), "checked result")
    restored = TaskLedger(str(tmp_path)).load(value.id)
    assert summary(restored)["column"] == "completed"
    restored.result = "new"
    assert ledger.save(restored)
    assert summary(TaskLedger(str(tmp_path)).load(value.id))["column"] == "attention"


def test_recovery_history_does_not_hide_unmatched_failures_or_mark_success():
    value = task(status="interrupted", kind="dev")
    assert summary(value)["column"] == "attention"
    view = task_summary(value, SnapshotSources([value]), recovered_by="task-resume")
    assert view["column"] == "history" and view["execution"] == "interrupted"
    assert view["handling"] == "pending"


def test_unknown_kinds_are_bounded_and_never_grant_completion():
    value = handled(task(kind="x" * 10000))
    view = summary(value)
    assert view["kind"] == "unknown" and view["column"] == "attention"


def test_damaged_kind_is_unknown_instead_of_breaking_the_snapshot():
    value = task(); value.kind = {"bad": "kind"}
    assert summary(value)["kind"] == "unknown"
    assert summary(value)["column"] == "attention"


def test_damaged_source_is_unknown_but_observed_owner_or_round_change_is_invalid():
    parent = task(); value = child(parent)
    parent.collaboration["round"] = True
    assert summary(value, parent)["validity"] == "unknown"
    parent.collaboration["round"] = 2
    assert summary(value, parent)["validity"] == "invalid"
    parent.collaboration["round"] = 1
    parent.owner_session = "sid-other"
    assert summary(value, parent)["validity"] == "invalid"


def test_ownerless_direct_delegation_cannot_gain_completion_without_handoff():
    value = task(); value.collaboration.pop("dispatch"); value.owner_session = ""
    assert summary(value)["column"] == "attention"
    assert summary(value)["handling"] == "unknown"
