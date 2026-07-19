"""Read-only, bounded projections for the cross-client Agent Dashboard.

The Dashboard must not infer runtime state in Desktop.  This module joins the
project's Git/worktree facts, durable background-task ownership, and the live
agent's context-window estimate into small JSON-safe summaries shared by every
client.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict

_ACTIVE_TASKS = frozenset({"queued", "running", "cancelling"})
_ATTENTION_TASKS = frozenset({"failed", "paused", "interrupted"})


def _git(repo_root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def _resolved_git_path(root: Path, value: str) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve()
    except OSError:
        return None


def project_dashboard_context(repo_root: str) -> Dict[str, Any]:
    """Return current cwd/branch/worktree facts without mutating Git state."""
    try:
        root = Path(repo_root).resolve()
    except OSError:
        root = Path(repo_root).absolute()
    payload: Dict[str, Any] = {
        "cwd": str(root),
        "branch": "",
        "head": "",
        "worktree": {"kind": "none", "name": "", "owned_count": 0},
    }
    try:
        top = _git(str(root), "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            return payload
        git_root = Path(top.stdout.strip()).resolve()
        payload["cwd"] = str(git_root)
        branch = _git(str(git_root), "symbolic-ref", "--quiet", "--short", "HEAD")
        head = _git(str(git_root), "rev-parse", "--short=12", "HEAD")
        git_dir = _git(str(git_root), "rev-parse", "--git-dir")
        common_dir = _git(str(git_root), "rev-parse", "--git-common-dir")
        git_path = _resolved_git_path(git_root, git_dir.stdout) if git_dir.returncode == 0 else None
        common_path = _resolved_git_path(git_root, common_dir.stdout) if common_dir.returncode == 0 else None
        linked = bool(git_path and common_path and git_path != common_path)

        owner_root = common_path.parent if common_path and common_path.name == ".git" else git_root
        owned_root = (owner_root / ".vortocode" / "worktrees").resolve()
        owned_count = 0
        listed = _git(str(git_root), "worktree", "list", "--porcelain")
        if listed.returncode == 0:
            for line in listed.stdout.splitlines():
                if not line.startswith("worktree "):
                    continue
                try:
                    Path(line[9:].strip()).resolve().relative_to(owned_root)
                except (OSError, ValueError):
                    continue
                owned_count += 1

        payload.update({
            "branch": branch.stdout.strip() if branch.returncode == 0 else "",
            "head": head.stdout.strip() if head.returncode == 0 else "",
            "worktree": {
                "kind": "linked" if linked else "main",
                "name": git_path.name if linked and git_path else git_root.name,
                "owned_count": owned_count,
            },
        })
    except (OSError, subprocess.SubprocessError, ValueError):
        return payload
    return payload


def background_tasks_by_session(repo_root: str) -> Dict[str, Dict[str, Any]]:
    """Group bounded durable-task counts by the stable raw Desktop sid."""
    try:
        from src.gateway.tasks import TaskLedger

        tasks = TaskLedger(repo_root).list(limit=500)
    except Exception:  # noqa: BLE001 - dashboard degradation must be read-only
        tasks = []
    try:
        from src.gateway.worktree_sessions import list_worktree_sessions

        worktrees = list_worktree_sessions(repo_root)
    except Exception:  # noqa: BLE001 - Git telemetry must not break session listing
        worktrees = []
    worktree_counts: Dict[str, int] = {}
    for worktree in worktrees:
        task_id = str(worktree.get("task_id") or "")
        if task_id:
            worktree_counts[task_id] = worktree_counts.get(task_id, 0) + 1
    grouped: Dict[str, list[Any]] = {}
    for task in tasks:
        owner = str(getattr(task, "owner_session", "") or "").strip()
        if owner.startswith("sid-"):
            owner = owner[4:]
        if not owner:
            continue
        grouped.setdefault(owner, []).append(task)

    output: Dict[str, Dict[str, Any]] = {}
    for sid, owned in grouped.items():
        statuses = [str(getattr(task, "status", "") or "") for task in owned]
        latest = owned[0]
        active_task = next(
            (task for task in owned if str(getattr(task, "status", "") or "") in _ACTIVE_TASKS),
            None,
        )
        attention_task = next(
            (task for task in owned if str(getattr(task, "status", "") or "") in _ATTENTION_TASKS),
            None,
        )
        latest_branch = next(
            (str(getattr(task, "branch", "") or "") for task in owned
             if getattr(task, "branch", "")),
            "",
        )
        output[sid] = {
            "total": len(owned),
            "active": sum(status in _ACTIVE_TASKS for status in statuses),
            "attention": sum(status in _ATTENTION_TASKS for status in statuses),
            "completed": sum(status == "done" for status in statuses),
            "latest_status": str(getattr(latest, "status", "") or "")[:32],
            "latest_prompt": str(getattr(latest, "prompt", "") or "")[:160],
            "branch": latest_branch[:160],
            "latest_task_id": str(getattr(latest, "id", "") or "")[:180],
            "active_task_id": str(getattr(active_task, "id", "") or "")[:180],
            "attention_task_id": str(getattr(attention_task, "id", "") or "")[:180],
            "plan_id": str(getattr(latest, "plan_id", "") or "")[:180],
            "worktree_count": sum(
                worktree_counts.get(str(getattr(task, "id", "") or ""), 0)
                for task in owned
            ),
        }
    return output


def agent_context_summary(agent: Any, mode: str = "plan") -> Dict[str, Any]:
    """Project MainAgent.context_usage into a stable, bounded REST shape."""
    context_usage = getattr(agent, "context_usage", None)
    if not callable(context_usage):
        return {}
    try:
        usage = context_usage("build" if mode == "build" else "plan")
        if not isinstance(usage, dict):
            return {}
        return {
            "used_tokens": max(0, int(usage.get("used_tokens") or 0)),
            "max_tokens": max(1, int(usage.get("max_context_tokens") or 1)),
            "pct": max(0, min(999, int(usage.get("pct") or 0))),
            "system_tokens": max(0, int(usage.get("system_tokens") or 0)),
            "history_tokens": max(0, int(usage.get("history_tokens") or 0)),
            "context_window_tokens": max(0, int(usage.get("context_window_tokens") or 0)),
            "context_window_pct": max(0.0, min(999.0, float(
                usage.get("context_window_pct") or 0.0
            ))),
            "context_window_source": str(usage.get("context_window_source") or "unknown")[:32],
            "history_messages": max(0, int(usage.get("history_messages") or 0)),
            "policy": str(usage.get("policy") or "")[:32],
            "will_compact": bool(usage.get("will_compact")),
        }
    except Exception:  # noqa: BLE001 - telemetry must never alter a turn
        return {}
