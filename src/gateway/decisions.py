"""Actionable decision queue assembled from durable runtime state."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_MAX_DISMISSED = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


class DecisionStore:
    """Small acknowledgement ledger so resolved historical failures stay quiet."""

    def __init__(self, repo_root: str):
        self.repo_root = str(repo_root)
        self.path = Path(repo_root) / ".vortocode" / "decisions.json"

    def _load(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            dismissed = payload.get("dismissed") if isinstance(payload, dict) else None
            return {
                str(key): str(value)
                for key, value in (dismissed or {}).items()
                if isinstance(key, str)
            }
        except (OSError, TypeError, ValueError):
            return {}

    def dismissed(self) -> set[str]:
        return set(self._load())

    def dismiss(self, decision_id: str) -> bool:
        decision_id = str(decision_id or "").strip()
        if not decision_id or len(decision_id) > 180 or decision_id.startswith("confirm:"):
            return False
        dismissed = self._load()
        dismissed[decision_id] = _now()
        dismissed = dict(list(dismissed.items())[-_MAX_DISMISSED:])
        try:
            from src.utils.state_dir import ensure_state_gitignore

            ensure_state_gitignore(self.repo_root)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps({"dismissed": dismissed}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
            return True
        except (OSError, TypeError, ValueError):
            return False


def _item(
    decision_id: str,
    kind: str,
    severity: str,
    title: str,
    detail: str,
    *,
    created: str = "",
    target_id: str = "",
    action: str = "inspect",
    tainted: bool = False,
    can_dismiss: bool = True,
) -> dict[str, Any]:
    return {
        "id": decision_id,
        "kind": kind,
        "severity": severity,
        "title": _text(title, 180),
        "detail": _text(detail, 1000),
        "created": _text(created or _now(), 80),
        "target_id": _text(target_id, 180),
        "action": action,
        "tainted": bool(tainted),
        "can_dismiss": bool(can_dismiss),
    }


def build_decision_queue(
    *,
    confirmations: Iterable[dict[str, Any]] = (),
    goals: Iterable[Any] = (),
    tasks: Iterable[Any] = (),
    runs: Iterable[Any] = (),
    dismissed: set[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Normalize local state into stable, actionable queue items."""
    hidden = dismissed or set()
    output: list[dict[str, Any]] = []
    for confirmation in confirmations:
        confirmation_id = _text(confirmation.get("id"), 120)
        if not confirmation_id:
            continue
        tainted = bool(confirmation.get("tainted"))
        output.append(_item(
            f"confirm:{confirmation_id}", "confirmation", "critical" if tainted else "high",
            "外部内容回合等待确认" if tainted else "Runtime 等待操作确认",
            confirmation.get("text") or "有一项操作等待允许或拒绝",
            created=confirmation.get("created") or "", target_id=confirmation_id,
            action="confirm", tainted=tainted, can_dismiss=False,
        ))

    for goal in goals:
        if getattr(goal, "status", "") != "blocked":
            continue
        version = getattr(goal, "updated", "") or getattr(goal, "blocker", "") or "blocked"
        fingerprint = hashlib.sha256(str(version).encode("utf-8", errors="replace")).hexdigest()[:10]
        decision_id = f"goal:{goal.id}:{fingerprint}"
        if decision_id in hidden:
            continue
        output.append(_item(
            decision_id, "goal", "high", f"目标阻塞 · {_text(goal.objective, 120)}",
            goal.blocker or goal.next_action or "目标需要人工检查失败证据",
            created=goal.updated or goal.created, target_id=goal.id, action="open_goal",
        ))

    for task in tasks:
        status = getattr(task, "status", "")
        if status not in {"failed", "interrupted", "paused"}:
            continue
        decision_id = f"task:{task.id}"
        if decision_id in hidden:
            continue
        resumable = bool(getattr(task, "plan_id", ""))
        label = {"failed": "后台任务失败", "interrupted": "后台任务被中断", "paused": "后台任务已暂停"}[status]
        detail = getattr(task, "error", "") or getattr(task, "result", "") or getattr(task, "prompt", "")
        output.append(_item(
            decision_id, "task", "high" if status != "paused" else "medium", label,
            detail or "检查任务状态后决定是否恢复",
            created=getattr(task, "updated", "") or getattr(task, "created", ""),
            target_id=task.id, action="resume_task" if resumable else "open_task",
        ))

    for run in runs:
        status = getattr(run, "status", "")
        if status not in {"failed", "interrupted"}:
            continue
        decision_id = f"run:{run.id}"
        if decision_id in hidden:
            continue
        detail = getattr(run, "error", "") or getattr(run, "output", "") or getattr(run, "command", "")
        # cron 的例行班次走同一条 run lane（B6-5），但标题得说人话：它不是谁点的测试。
        title = "例行作业失败" if getattr(run, "kind", "") == "cron" else "测试或命令运行失败"
        output.append(_item(
            decision_id, "run", "high", title, detail or "打开运行记录检查失败输出",
            created=getattr(run, "updated", "") or getattr(run, "created", ""),
            target_id=run.id, action="open_run",
        ))

    output.sort(key=lambda item: item.get("created") or "", reverse=True)
    return output[:max(1, min(int(limit), 200))]
