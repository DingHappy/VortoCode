from types import SimpleNamespace

from src.workflow.tasks.runtime_inbox import build_runtime_inbox


def test_runtime_inbox_builds_bounded_actionable_counts():
    sessions = [{
        "sid": "desktop-1",
        "title": "修复登录",
        "status": "needs_input",
        "updated": 10,
        "pending_input_count": 1,
        "background_tasks": {"active": 1, "attention": 0},
        "hook_issues": {"count": 2, "items": [{"error": "must not leak"}]},
        "context": {"pct": 42, "used_tokens": 100, "max_tokens": 1000},
    }]
    goals = [SimpleNamespace(
        id="goal-1", objective="发布 Desktop", status="blocked", blocker="CI 失败",
        next_action="修复", created="2026-07-17T00:00:00+00:00", updated="",
        acceptance_criteria=[SimpleNamespace(status="passed"), SimpleNamespace(status="failed")],
    )]
    tasks = [SimpleNamespace(
        id="task-1", status="running", prompt="继续开发", error="", result="",
        owner_session="sid-desktop-1", goal_id="goal-1", plan_id="plan-1", branch="vorto/task",
        created="2026-07-17T00:00:00+00:00", updated="",
    )]
    decisions = [{"id": "confirm:1", "kind": "confirmation", "detail": "确认写入"}]

    snapshot = build_runtime_inbox(
        scope="project", sessions=sessions, decisions=decisions, goals=goals, tasks=tasks,
    )

    assert snapshot["version"] == 1
    assert snapshot["counts"] == {
        "sessions_needing_input": 1,
        "sessions_working": 0,
        "sessions_queued": 0,
        "decisions": 1,
        "hook_issues": 2,
        "goals_active": 0,
        "goals_blocked": 1,
        "tasks_active": 1,
        "tasks_attention": 0,
    }
    assert snapshot["goals"][0]["progress"] == {"passed": 1, "failed": 1, "total": 2}
    assert snapshot["sessions"][0]["hook_issues"] == {"count": 2}
    assert "items" not in snapshot["sessions"][0]["hook_issues"]
    assert snapshot["decisions"][0] == {
        "id": "confirm:1", "kind": "confirmation", "severity": "high", "title": "需要处理",
        "detail": "确认写入", "created": "", "target_id": "confirm:1", "action": "confirm",
        "session_id": "", "tainted": False, "can_dismiss": False,
    }


def test_runtime_inbox_drops_invalid_rows_and_caps_payloads():
    sessions = [{"sid": "", "status": "working"}] + [
        {"sid": f"sid-{index}", "status": "unknown", "title": "x" * 400}
        for index in range(120)
    ]
    snapshot = build_runtime_inbox(scope="scratch", sessions=sessions)

    assert len(snapshot["sessions"]) == 99
    assert snapshot["sessions"][0]["status"] == "inactive"
    assert len(snapshot["sessions"][0]["title"]) == 121


def test_runtime_inbox_sanitizes_decision_payloads():
    snapshot = build_runtime_inbox(scope="general", decisions=[
        {"id": "", "kind": "confirmation"},
        {"id": "bad", "kind": "unknown"},
        {
            "id": "decision-1", "kind": "task", "severity": "unexpected", "action": "shell",
            "title": "t" * 500, "detail": "d" * 2_000, "session_id": "s" * 300,
            "secret": "must-not-cross-runtime-boundary",
        },
    ])

    assert snapshot["counts"]["decisions"] == 1
    decision = snapshot["decisions"][0]
    assert decision["severity"] == "high"
    assert decision["action"] == "open_session"
    assert len(decision["title"]) == 201
    assert len(decision["detail"]) == 1201
    assert len(decision["session_id"]) == 181
    assert "secret" not in decision


def test_runtime_inbox_task_lifecycle_counts_include_pending_handoff_not_done_success():
    from src.workflow.tasks.tasks import BackgroundTask
    from src.workflow.tasks.handoffs import revision
    value = BackgroundTask.new("dev", "done", owner_session="sid-owner")
    value.status, value.result = "done", "result"
    original = value.to_dict()
    pending = build_runtime_inbox(scope="project", tasks=[value])
    assert pending["counts"]["tasks_attention"] == 1
    assert pending["tasks"][0]["lifecycle"]["handling"] == "pending"
    assert value.to_dict() == original
    value.handoff_receipt = {"revision": revision(value), "note": "checked", "handled_at": "2026-10-07T00:00:00+00:00"}
    handled = build_runtime_inbox(scope="project", tasks=[value])
    assert handled["counts"]["tasks_attention"] == 0
    assert handled["tasks"][0]["lifecycle"]["column"] == "completed"
    assert handled["tasks"][0]["lifecycle"]["review"] == "not_applicable"


def test_completed_task_does_not_promote_its_linked_goal_or_write_evidence():
    from src.workflow.workbench.goals import Goal
    from src.workflow.tasks.handoffs import revision
    from src.workflow.tasks.tasks import BackgroundTask
    goal = Goal.new("deliver", ["real proof"])
    goal.status = "active"
    value = BackgroundTask.new("dev", "done", owner_session="sid-owner", goal_id=goal.id)
    value.status, value.result = "done", "result"
    goal.task_ids = [value.id]
    value.handoff_receipt = {"revision": revision(value), "note": "handled", "handled_at": "2026-10-07T00:00:00+00:00"}
    original = goal.to_dict()
    view = build_runtime_inbox(scope="project", tasks=[value], goals=[goal])
    assert view["tasks"][0]["lifecycle"]["column"] == "completed"
    assert view["goals"][0]["status"] == "active" and view["goals"][0]["progress"]["passed"] == 0
    assert goal.to_dict() == original
