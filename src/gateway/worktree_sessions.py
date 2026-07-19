"""Read-only snapshots for Desktop worktree and persistent dev-plan sessions."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List

from src.agents.dev_plan import list_plans, load_plan
from src.agents.worktree_bindings import list_worktree_bindings


def _git(repo_root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def list_worktree_sessions(repo_root: str) -> List[Dict[str, Any]]:
    """List live VortoCode-owned worktrees without exposing unrelated system paths."""
    root = Path(repo_root).resolve()
    owned_root = (root / ".vortocode" / "worktrees").resolve()
    try:
        result = _git(str(root), "worktree", "list", "--porcelain")
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    sessions: List[Dict[str, Any]] = []
    bindings = list_worktree_bindings(str(root))
    for chunk in result.stdout.strip().split("\n\n"):
        fields: Dict[str, str] = {}
        flags = set()
        for line in chunk.splitlines():
            key, _, value = line.partition(" ")
            if value:
                fields[key] = value
            else:
                flags.add(key)
        raw_path = fields.get("worktree", "")
        if not raw_path:
            continue
        try:
            path = Path(raw_path).resolve()
            relative = path.relative_to(owned_root)
        except (OSError, ValueError):
            continue
        try:
            status = _git(str(path), "status", "--porcelain=v1", "--untracked-files=normal")
            changed = len([line for line in status.stdout.splitlines() if line.strip()])
        except (OSError, subprocess.SubprocessError):
            changed = 0
        branch = fields.get("branch", "").removeprefix("refs/heads/")
        worktree_id = relative.as_posix()
        binding = bindings.get(worktree_id, {})
        sessions.append({
            "id": worktree_id,
            "path": f".vortocode/worktrees/{relative.as_posix()}",
            "head": fields.get("HEAD", "")[:12],
            "branch": branch,
            "detached": "detached" in flags,
            "changed_files": changed,
            "locked": "locked" in flags,
            "prunable": "prunable" in flags,
            "task_id": binding.get("task_id", ""),
            "owner_session": binding.get("owner_session", ""),
            "plan_id": binding.get("plan_id", ""),
            "created": binding.get("created", ""),
        })
    return sessions


def list_plan_sessions(repo_root: str) -> List[Dict[str, Any]]:
    """Return block-level progress for resumable development plans."""
    sessions = []
    for summary in list_plans(repo_root):
        plan = load_plan(repo_root, str(summary.get("plan_id") or ""))
        if plan is None:
            continue
        counts = plan.counts()
        sessions.append({
            "plan_id": plan.plan_id,
            "task": plan.task,
            "status": plan.status,
            "branch": plan.branch,
            "base": plan.base,
            "updated": summary.get("updated"),
            "progress": {
                "landed": counts.get("landed", 0),
                "failed": counts.get("failed", 0),
                "running": counts.get("running", 0),
                "pending": counts.get("pending", 0),
                "total": len(plan.blocks),
            },
            "blocks": [
                {
                    "id": block.id,
                    "title": block.title or block.desc,
                    "kind": block.kind,
                    "status": block.status,
                    "attempts": block.attempts,
                    "note": block.note[-300:],
                }
                for block in plan.blocks
            ],
            "integration": plan.integration,
            "review": plan.review,
            "can_resume": plan.status in {"running", "integration_failed", "failed"}
                or any(not block.landed for block in plan.blocks),
        })
    return sessions


def task_session_view(
    repo_root: str,
    task: Any,
    *,
    worktrees: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Project one durable task into the shared Desktop/Journal handoff shape."""
    data = task.to_dict()
    data["log"] = data.get("log", [])[-30:]
    plan = load_plan(repo_root, task.plan_id) if task.plan_id else None
    counts = plan.counts() if plan is not None else {}
    completed = [block.title or block.desc for block in plan.blocks if block.landed] if plan else []
    remaining = [block.title or block.desc for block in plan.blocks if not block.landed] if plan else []
    progress = {
        "landed": counts.get("landed", 0),
        "failed": counts.get("failed", 0),
        "running": counts.get("running", 0),
        "pending": counts.get("pending", 0),
        "total": len(plan.blocks) if plan else 0,
    }
    can_resume = bool(
        task.plan_id
        and plan is not None
        and task.status in {"paused", "interrupted", "failed", "cancelled"}
    )
    next_action = (
        "从持久计划恢复剩余任务"
        if can_resume else
        "等待当前 worktree 步骤完成"
        if task.status in {"queued", "running"} else
        "审查分支并运行 Goal 验收"
        if task.status == "done" else
        "检查错误和最近日志"
    )
    handoff_lines = [
        f"任务交接：{task.prompt}",
        f"状态：{task.status}",
        f"计划：{task.plan_id or '尚未生成'}",
        f"分支：{task.branch or (plan.branch if plan else '尚未生成')}",
    ]
    if plan is not None:
        handoff_lines.append(
            f"进度：{progress['landed']}/{progress['total']} 已落地，"
            f"{progress['failed']} 失败，"
            f"{progress['pending'] + progress['running']} 待处理"
        )
    if remaining:
        handoff_lines.append("待处理：" + "；".join(remaining[:6]))
    if task.log:
        handoff_lines.append("最近上下文：" + "；".join(task.log[-3:]))
    if task.error:
        handoff_lines.append("错误：" + task.error)
    handoff_lines.append("下一步：" + next_action)
    data["plan"] = {
        "status": plan.status,
        "branch": plan.branch,
        "base": plan.base,
        "progress": progress,
        "blocks": [
            {
                "id": block.id,
                "title": block.title or block.desc,
                "kind": block.kind,
                "status": block.status,
                "attempts": block.attempts,
                "note": block.note[-300:],
            }
            for block in plan.blocks
        ],
        "integration": plan.integration,
    } if plan is not None else None
    data["can_pause"] = task.status in {"queued", "running"}
    data["can_resume"] = can_resume
    live_worktrees = worktrees if worktrees is not None else list_worktree_sessions(repo_root)
    data["worktrees"] = [
        dict(item) for item in live_worktrees
        if str(item.get("task_id") or "") == str(task.id)
    ][:20]
    try:
        from src.gateway.task_review import task_review_summary

        data["branch_review"] = task_review_summary(repo_root, task)
    except Exception:  # noqa: BLE001 - review evidence must not break task hydration
        data["branch_review"] = None
    data["handoff"] = {
        "completed": completed[:12],
        "remaining": remaining[:12],
        "next_action": next_action,
        "text": "\n".join(handoff_lines),
    }
    return data


def worktree_workspace_snapshot(repo_root: str) -> Dict[str, Any]:
    return {
        "worktrees": list_worktree_sessions(repo_root),
        "plans": list_plan_sessions(repo_root),
    }
