"""Legacy grant compatibility is read-only; old unbound actions are not upgraded."""
import json
from datetime import datetime, timezone

import pytest

from src.scheduling import codex_goal, codex_observer as observer, codex_watch as watch
from src.workflow.workbench.personal_goals import PersonalGoals

NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)
TID = "grant-compatibility"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(watch, "_now", lambda: NOW)
    raw = observer.summarize_thread({"id": TID, "name": "文档检查", "cwd": "/synthetic/docs", "status": {"type": "idle"}},
        {"id": "turn-1", "status": "completed", "items": [{"type": "userMessage", "content": [{"type": "text", "text": "仅核对文档"}]}]},
        observed_at=NOW.timestamp())
    watch.record_report(root, {"threads": [raw]}, now=NOW)
    thread = watch.load_state(root)["threads"][0]
    watch.hand_over(root, thread, now=NOW)
    return root, thread


def test_unchanged_legacy_grant_supports_fresh_manual_control_without_rewriting_it(setup):
    root, thread = setup
    path = watch._handover_path(root)
    record = watch.load_handovers(root)[TID]
    record.pop("token")
    original = json.dumps({TID: record}, ensure_ascii=False)
    path.write_text(original)
    action = watch.propose_action(root, thread=TID, kind="message", text="主人新拟的话", session="owner-session")
    assert action["handover_fingerprint"] == codex_goal.digest(record)
    watch.decide_action(root, action["id"], True)
    assert watch.claim_action(root, action["id"])["status"] == "sending"
    assert path.read_text() == original
    watch.take_back(root, TID)
    watch.hand_over(root, thread, now=NOW)
    assert watch.load_handovers(root)[TID]["token"]
    assert codex_goal.digest(watch.load_handovers(root)[TID]) != action["handover_fingerprint"]


@pytest.mark.parametrize("approved", [False, True])
def test_old_ordinary_actions_without_a_grant_fingerprint_require_new_proposal(setup, approved):
    root, _ = setup
    action = watch.propose_action(root, thread=TID, kind="message", text="旧版本拟的话", session="owner-session")
    if approved:
        watch.decide_action(root, action["id"], True)
    old = watch.load_actions(root)[0]
    old.pop("handover_fingerprint")
    watch._save_actions(root, [old])
    with pytest.raises(watch.CodexActionError, match="交接版本"):
        (watch.claim_action(root, action["id"]) if approved else watch.decide_action(root, action["id"], True))
    persisted = watch.load_actions(root)[0]
    assert persisted["status"] == "expired" and "handover_fingerprint" not in persisted


def test_existing_goal_action_token_still_binds_legacy_goal_actions(setup):
    root, thread = setup
    service = PersonalGoals(root)
    goal = service.create({"request_id": "legacy-goal", "objective": "核对文档", "acceptance_criteria": ["用户核对"], "next_action": "核对"})
    watch.take_back(root, TID)
    watch.hand_over(root, thread, now=NOW, goal=codex_goal.binding(root, goal["id"], 1))
    action = watch.propose_action(root, thread=TID, kind="message", text="按原目标推进", session="owner-session")
    old = watch.load_actions(root)[0]
    old.pop("handover_fingerprint")
    watch._save_actions(root, [old])
    assert watch.decide_action(root, action["id"], True)["status"] == "approved"
    assert watch.claim_action(root, action["id"])["status"] == "sending"


@pytest.mark.parametrize("token", [None, "", True, 1, "x" * 81])
def test_invalid_explicit_grant_version_is_not_treated_as_a_legacy_record(setup, token):
    root, _ = setup
    path = watch._handover_path(root)
    record = watch.load_handovers(root)[TID]
    record["token"] = token
    raw = json.dumps({TID: record})
    path.write_text(raw)
    with pytest.raises(watch.CodexActionError, match="交接版本"):
        watch.load_handovers(root)
    assert path.read_text() == raw


def test_compare_and_remove_can_never_remove_a_replacement_grant(setup):
    root, thread = setup
    old = watch.load_handovers(root)[TID]
    watch.take_back(root, TID)
    new = watch.hand_over(root, thread, now=NOW)
    assert not watch.take_back(root, TID, expected_fingerprint=codex_goal.digest(old))
    assert watch.load_handovers(root)[TID] == new
    assert watch.take_back(root, TID, expected_fingerprint=codex_goal.digest(new))
