"""Causal versions survive completed commands, retention and process reloads; offline only."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.scheduling import codex_review_context as context, codex_watch as watch

NOW = datetime(2026, 10, 31, 10, tzinfo=timezone.utc)
TID = "causality-thread"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(watch, "_now", lambda: NOW)
    threads = [{"id": tid, "status": "idle", "turn": {"id": "turn-1", "status": "completed", "request": "核对文档",
                "review_context": context.capture("turn-1", [{"type": "userMessage", "content": [{"type": "text", "text": "核对文档"}]}])}}
               for tid in (TID, "other-thread")]
    watch.record_report(root, {"threads": threads}, now=NOW)
    for thread in watch.load_state(root)["threads"]:
        watch.hand_over(root, thread, now=NOW)
    return root


def manual(root, *, tid=TID):
    return watch.propose_action(root, thread=tid, kind="message", text="新增要求：先核对", session="owner")


def completed(root, *, tid=TID):
    action = manual(root, tid=tid)
    watch.decide_action(root, action["id"], True)
    watch.claim_action(root, action["id"])
    return watch.finish_action(root, action["id"], ok=True)


def automatic(root):
    return watch.propose_action(root, thread=TID, kind="message", text="旧建议", automatic=True,
                                expected_action_version=watch.action_version(root, TID))


@pytest.mark.parametrize("phase", ["proposed", "approved"])
def test_old_draft_cannot_be_approved_or_claimed_after_new_command_has_finished(setup, phase):
    root = setup
    old = automatic(root)
    if phase == "approved":
        watch.decide_action(root, old["id"], True)
    completed(root)
    operation = watch.decide_action if phase == "proposed" else watch.claim_action
    with pytest.raises(watch.CodexActionError, match="过时"):
        operation(root, old["id"], True) if phase == "proposed" else operation(root, old["id"])
    current = next(a for a in watch.load_actions(root) if a["id"] == old["id"])
    assert current["status"] == "expired"


def test_own_approval_and_claim_advance_basis_without_invalidating_the_action(setup):
    root = setup
    action = automatic(root)
    for operation in (lambda: watch.decide_action(root, action["id"], True), lambda: watch.claim_action(root, action["id"])):
        before = watch.action_version(root, TID)
        row = operation()
        after = watch.action_version(root, TID)
        assert after["revision"] == before["revision"] + 1
        assert row["action_version"] == after
    assert watch.finish_action(root, action["id"], ok=True)["status"] == "sent"


def test_other_thread_commands_do_not_invalidate_a_modern_review_or_draft(setup):
    root = setup
    completed(root)  # Establish the versioned store before the review.
    action = automatic(root)
    basis = watch.action_version(root, TID)
    completed(root, tid="other-thread")
    assert watch.action_version(root, TID) == basis
    assert watch.decide_action(root, action["id"], True)["status"] == "approved"
    assert watch.claim_action(root, action["id"])["status"] == "sending"


def test_active_grant_version_survives_terminal_history_retention(setup):
    root = setup
    completed(root)
    before = watch.action_version(root, TID)
    completed(root)
    after = watch.action_version(root, TID)
    template = completed(root, tid="other-thread")
    # Simulate continued terminal traffic in the existing bounded file, not 100 RPCs.
    watch._save_actions(root, watch.load_actions(root) + [{**template, "id": f"retired-{i}"} for i in range(100)])
    assert not any(a["thread_id"] == TID for a in watch.load_actions(root))
    assert watch.action_version(root, TID) == after != before
    with pytest.raises(watch.CodexActionError, match="动作已变化"):
        watch.propose_action(root, thread=TID, kind="message", text="过期建议", automatic=True, expected_action_version=before)


def test_legacy_read_is_unchanged_and_first_write_migrates_atomically(setup):
    root = setup
    old = completed(root)
    path = watch._actions_path(root)
    raw = json.dumps([old])
    path.write_text(raw)
    basis = watch.action_version(root, TID)
    assert basis["schema_version"] == 0 and watch.load_actions(root) == [old]
    assert path.read_text() == raw
    new = manual(root)
    store = json.loads(path.read_text())
    assert store["schema_version"] == 1 and watch.load_actions(root) == [old, new]
    assert watch.action_version(root, TID) != basis


def test_downgraded_then_migrated_store_does_not_revive_old_automatic_authority(setup):
    root = setup
    old = automatic(root)
    original = watch.action_version(root, TID)
    path = watch._actions_path(root)
    path.write_text(json.dumps(watch.load_actions(root)))  # Simulate an incompatible old writer.
    watch._save_actions(root, watch.load_actions(root))
    assert watch.action_version(root, TID)["store_id"] != original["store_id"]
    with pytest.raises(watch.CodexActionError, match="过时"):
        watch.decide_action(root, old["id"], True)


def test_legacy_automatic_draft_is_readable_but_never_silently_bound_to_new_version(setup):
    root = setup
    old = automatic(root)
    old.pop("action_version")
    path = watch._actions_path(root)
    raw = json.dumps([old])
    path.write_text(raw)
    assert watch.load_actions(root) == [old] and path.read_text() == raw
    with pytest.raises(watch.CodexActionError, match="缺少动作版本"):
        watch.decide_action(root, old["id"], True)
    assert watch.load_actions(root)[0]["status"] == "expired"
    assert "action_version" not in watch.load_actions(root)[0]


@pytest.mark.parametrize("fault", ["version", "bool", "store", "map", "thread", "counter", "overflow", "missing", "basis"])
def test_invalid_explicit_versioned_store_is_preserved_and_never_reset(setup, fault):
    root = setup
    manual(root)
    path = watch._actions_path(root)
    data = json.loads(path.read_text())
    if fault == "version":
        data["schema_version"] = 2
    elif fault == "bool":
        data["schema_version"] = True
    elif fault == "store":
        data["store_id"] = ""
    elif fault == "map":
        data["thread_revisions"] = []
    elif fault == "thread":
        data["thread_revisions"]["\x00"] = 1
    elif fault == "counter":
        data["thread_revisions"][TID] = True
    elif fault == "overflow":
        data["thread_revisions"][TID] = 2**53
    elif fault == "missing":
        data["thread_revisions"].clear()
    else:
        data["actions"][0]["action_version"] = {"schema_version": 1, "store_id": "x", "revision": 1}
    original = json.dumps(data)
    path.write_text(original)
    for operation in (lambda: watch.load_actions(root), lambda: watch.action_version(root, TID), lambda: manual(root)):
        with pytest.raises(watch.CodexActionStorageError):
            operation()
        assert path.read_text() == original


def test_failed_atomic_commit_does_not_advance_actions_or_revision(setup, monkeypatch):
    root = setup
    manual(root)
    path = watch._actions_path(root)
    before, basis = path.read_bytes(), watch.action_version(root, TID)
    replace = Path.replace

    def fail(source, target):
        if Path(target) == path:
            raise OSError("fake disk failure")
        return replace(source, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(watch.CodexActionStorageError):
        manual(root)
    assert path.read_bytes() == before and watch.action_version(root, TID) == basis


def test_revision_overflow_stops_writes_without_wrapping_or_reset(setup):
    root = setup
    manual(root)
    path = watch._actions_path(root)
    data = json.loads(path.read_text())
    data["thread_revisions"][TID] = 2**53 - 1
    original = json.dumps(data)
    path.write_text(original)
    with pytest.raises(watch.CodexActionStorageError, match="上限"):
        manual(root)
    assert path.read_text() == original
