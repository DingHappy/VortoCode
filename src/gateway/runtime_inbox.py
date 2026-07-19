"""Bounded, read-only projection for a Desktop cross-runtime inbox.

Each Gateway remains authoritative for its own sessions and durable ledgers.
Desktop may aggregate several of these snapshots, but it must not rediscover
status rules or copy unbounded task/goal payloads across projects.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

_SESSION_STATUSES = {"needs_input", "failed", "working", "queued", "idle", "inactive", "completed"}
_GOAL_STATUSES = {"draft", "active", "blocked", "failed", "achieved"}
_TASK_STATUSES = {"queued", "running", "cancelling", "failed", "paused", "interrupted", "done", "cancelled"}
_DECISION_KINDS = {"confirmation", "goal", "task", "run", "pr_check", "pr_review", "hook"}
_DECISION_SEVERITIES = {"critical", "high", "medium", "low"}
_DECISION_ACTIONS = {"confirm", "open_goal", "resume_task", "open_task", "open_run", "open_diff", "open_session"}


def _get(item: Any, name: str, default: Any = "") -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _text(value: Any, limit: int) -> str:
    value = str(value or "").strip()
    return value if len(value) <= limit else value[:limit] + "…"


def _session_view(item: Any) -> dict[str, Any] | None:
    sid = _text(_get(item, "sid"), 120)
    if not sid:
        return None
    status = _text(_get(item, "status"), 24)
    if status not in _SESSION_STATUSES:
        status = "inactive"
    background = _get(item, "background_tasks", {})
    background = background if isinstance(background, dict) else {}
    hook_issues = _get(item, "hook_issues", {})
    hook_issues = hook_issues if isinstance(hook_issues, dict) else {}
    context = _get(item, "context", {})
    context = context if isinstance(context, dict) else {}
    return {
        "sid": sid,
        "title": _text(_get(item, "title") or "新任务", 120),
        "status": status,
        "updated": max(0.0, float(_get(item, "updated", 0) or 0)),
        "pending_input_count": max(0, int(_get(item, "pending_input_count", 0) or 0)),
        "queue_count": max(0, int(_get(item, "queue_count", 0) or 0)),
        "activity": _text(_get(item, "activity"), 160),
        "running_prompt": _text(_get(item, "running_prompt"), 160),
        "cwd": _text(_get(item, "cwd"), 500),
        "branch": _text(_get(item, "branch"), 160),
        "background_tasks": {
            "active": max(0, int(background.get("active") or 0)),
            "attention": max(0, int(background.get("attention") or 0)),
            "latest_task_id": _text(background.get("latest_task_id"), 180),
            "active_task_id": _text(background.get("active_task_id"), 180),
            "attention_task_id": _text(background.get("attention_task_id"), 180),
        },
        "hook_issues": {"count": max(0, int(hook_issues.get("count") or 0))},
        "context": {
            "pct": max(0, min(999, int(context.get("pct") or 0))),
            "used_tokens": max(0, int(context.get("used_tokens") or 0)),
            "max_tokens": max(0, int(context.get("max_tokens") or 0)),
        },
    }


def _goal_view(item: Any) -> dict[str, Any] | None:
    goal_id = _text(_get(item, "id"), 180)
    status = _text(_get(item, "status"), 24)
    if not goal_id or status not in _GOAL_STATUSES:
        return None
    criteria = _get(item, "acceptance_criteria", [])
    criteria = criteria if isinstance(criteria, list) else []
    passed = sum(_get(criterion, "status") == "passed" for criterion in criteria)
    failed = sum(_get(criterion, "status") == "failed" for criterion in criteria)
    return {
        "id": goal_id,
        "objective": _text(_get(item, "objective"), 240),
        "status": status,
        "blocker": _text(_get(item, "blocker"), 500),
        "next_action": _text(_get(item, "next_action"), 300),
        "updated": _text(_get(item, "updated") or _get(item, "created"), 80),
        "progress": {"passed": passed, "failed": failed, "total": len(criteria)},
    }


def _decision_view(item: Any) -> dict[str, Any] | None:
    decision_id = _text(_get(item, "id"), 240)
    kind = _text(_get(item, "kind"), 24)
    if not decision_id or kind not in _DECISION_KINDS:
        return None
    severity = _text(_get(item, "severity"), 24)
    if severity not in _DECISION_SEVERITIES:
        severity = "high"
    action = _text(_get(item, "action"), 32)
    if action not in _DECISION_ACTIONS:
        action = "confirm" if kind == "confirmation" else "open_session"
    return {
        "id": decision_id,
        "kind": kind,
        "severity": severity,
        "title": _text(_get(item, "title") or "需要处理", 200),
        "detail": _text(_get(item, "detail"), 1200),
        "created": _text(_get(item, "created"), 80),
        "target_id": _text(_get(item, "target_id") or decision_id, 240),
        "action": action,
        "session_id": _text(_get(item, "session_id"), 180),
        "tainted": bool(_get(item, "tainted", False)),
        "can_dismiss": bool(_get(item, "can_dismiss", False)),
    }


def _task_view(item: Any) -> dict[str, Any] | None:
    task_id = _text(_get(item, "id"), 180)
    status = _text(_get(item, "status"), 24)
    if not task_id or status not in _TASK_STATUSES:
        return None
    return {
        "id": task_id,
        "status": status,
        "prompt": _text(_get(item, "prompt"), 240),
        "detail": _text(_get(item, "error") or _get(item, "result"), 500),
        "owner_session": _text(_get(item, "owner_session"), 180),
        "goal_id": _text(_get(item, "goal_id"), 180),
        "plan_id": _text(_get(item, "plan_id"), 180),
        "branch": _text(_get(item, "branch"), 180),
        "updated": _text(_get(item, "updated") or _get(item, "created"), 80),
    }


def build_runtime_inbox(
    *,
    scope: str,
    sessions: Iterable[Any] = (),
    decisions: Iterable[Any] = (),
    goals: Iterable[Any] = (),
    tasks: Iterable[Any] = (),
) -> dict[str, Any]:
    """Return a stable summary suitable for polling across local runtimes."""
    session_views = [view for item in list(sessions)[:100] if (view := _session_view(item))]
    decision_views = [view for item in list(decisions)[:100] if (view := _decision_view(item))]
    goal_views = [view for item in list(goals)[:200] if (view := _goal_view(item))]
    task_views = [view for item in list(tasks)[:200] if (view := _task_view(item))]
    return {
        "version": 1,
        "scope": _text(scope, 24),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sessions": session_views,
        "decisions": decision_views,
        "goals": goal_views,
        "tasks": task_views,
        "counts": {
            "sessions_needing_input": sum(item["status"] == "needs_input" for item in session_views),
            "sessions_working": sum(item["status"] == "working" for item in session_views),
            "sessions_queued": sum(item["status"] == "queued" for item in session_views),
            "decisions": len(decision_views),
            "hook_issues": sum(item["hook_issues"]["count"] for item in session_views),
            "goals_active": sum(item["status"] in {"draft", "active"} for item in goal_views),
            "goals_blocked": sum(item["status"] in {"blocked", "failed"} for item in goal_views),
            "tasks_active": sum(item["status"] in {"queued", "running", "cancelling"} for item in task_views),
            "tasks_attention": sum(item["status"] in {"failed", "paused", "interrupted"} for item in task_views),
        },
    }
