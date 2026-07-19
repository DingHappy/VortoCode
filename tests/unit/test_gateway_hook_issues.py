"""Actionable Hook issue projection stays bounded and acknowledgement-safe."""

from src.gateway.hook_issues import (
    acknowledge_hook_issue, forget_hook_issue_session, has_hook_issue,
    hook_issue_snapshot, load_hook_issue_acks,
)


def test_hook_issue_snapshot_tracks_terminal_failures_and_acknowledgements():
    activities = [
        {"type": "agent_hook", "id": "h1", "name": "format", "status": "running"},
        {"type": "agent_hook", "id": "h1", "name": "format", "event": "post_tool_use",
         "tool": "edit_file", "status": "failed", "summary": "format failed",
         "error": "exit 1", "duration_ms": 12, "recorded_at": "2026-07-17T01:00:00Z"},
        {"type": "agent_hook", "id": "h2", "name": "audit", "status": "succeeded"},
        {"type": "agent_hook", "id": "h3", "name": "lint", "status": "timed_out",
         "duration_ms": "bad"},
        {"type": "agent_tool", "id": "tool-1", "status": "failed"},
    ]
    snapshot = hook_issue_snapshot(activities)
    assert snapshot["count"] == 2
    assert [item["id"] for item in snapshot["items"]] == ["h3", "h1"]
    assert snapshot["items"][0]["duration_ms"] == 0
    assert has_hook_issue(activities, [], "h1") is True

    acknowledged = hook_issue_snapshot(activities, ["h1"])
    assert acknowledged["count"] == 1
    assert acknowledged["latest"]["id"] == "h3"
    assert has_hook_issue(activities, ["h1"], "h1") is False


def test_later_success_for_same_lifecycle_id_clears_issue():
    activities = [
        {"type": "agent_hook", "id": "same", "status": "failed"},
        {"type": "agent_hook", "id": "same", "status": "succeeded"},
    ]
    assert hook_issue_snapshot(activities)["count"] == 0


def test_acknowledgement_lives_outside_repo_and_stores_no_error_text(tmp_path, monkeypatch):
    store = tmp_path.parent / f"{tmp_path.name}-hook-acks.json"
    monkeypatch.setenv("VORTOCODE_HOOK_ISSUE_STORE", str(store))
    activities = [{
        "type": "agent_hook", "id": "hook-sensitive", "name": "audit",
        "status": "failed", "error": "private command output",
    }]
    assert acknowledge_hook_issue(str(tmp_path), "sid-owner", activities, "hook-sensitive") is True
    assert load_hook_issue_acks(str(tmp_path), "sid-owner") == ["hook-sensitive"]
    assert "private command output" not in store.read_text(encoding="utf-8")
    assert not (tmp_path / ".vortocode" / "hook-issues.json").exists()
    assert forget_hook_issue_session(str(tmp_path), "sid-owner") is True
    assert load_hook_issue_acks(str(tmp_path), "sid-owner") == []
    assert not store.exists()
