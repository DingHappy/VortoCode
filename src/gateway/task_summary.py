"""Read-only task lifecycle projection for a bounded Desktop Inbox snapshot.

No disk reads or writes: dependency validation reuses the existing domain rules
against the supplied task prefix. Missing sources/budget are unknown, not invalid.
"""
from __future__ import annotations

from src.gateway.collaboration import CollaborationConflict
from src.gateway.handoffs import collaboration_state_valid, completion_state
from src.gateway.task_dependencies import dependency_state, dependency_view, has_dependencies
from src.gateway.tasks import BackgroundTask

MAX_SUMMARY_LOOKUPS = 2048


class SnapshotSources:
    def __init__(self, tasks, *, budget=MAX_SUMMARY_LOOKUPS):
        self.tasks = {task.id: task for task in tasks if isinstance(task, BackgroundTask)}
        self.remaining = budget
        self.unknown = False

    def load(self, task_id):
        if self.remaining <= 0:
            self.unknown = True
            return None
        self.remaining -= 1
        task = self.tasks.get(task_id)
        if (task is None or not isinstance(task.kind, str) or not isinstance(task.prompt, str) or not isinstance(task.result, str)
                or not isinstance(task.owner_session, str) or not isinstance(task.status, str)
                or task.status not in {"waiting", "queued", "running", "cancelling", "blocked", "done", "failed", "cancelled", "paused", "interrupted"}
                or task.kind == "delegation" and not collaboration_state_valid(task)):
            self.unknown = True
            return None
        return task


def _validity(task, sources):
    if not has_dependencies(task):
        return "not_applicable"
    try:
        state = dependency_state(task)
        if state["resolution"] == "invalidated":
            return "invalid"
        if state["resolution"] != "consumed":
            return "unknown" if task.status == "done" else "not_applicable"
        sources.unknown = False
        view = dependency_view(task, sources)
        if sources.unknown:
            return "unknown"
        return "valid" if view.get("result_valid") is True else "invalid"
    except (CollaborationConflict, TypeError, ValueError, AttributeError):
        return "unknown"


def task_summary(task, sources: SnapshotSources, *, recovered_by="") -> dict:
    view = {"version": 1, "execution": getattr(task, "status", ""), "kind": task.kind if isinstance(task, BackgroundTask) and isinstance(task.kind, str) and task.kind in {"dev", "dev-resume", "delegation"} else "unknown", "review": "unknown",
            "handling": "unknown", "validity": "unknown", "round": None,
            "revision": "", "column": "attention", "label": "状态待核对", "recovered_by": recovered_by}
    if view["execution"] == "queued":
        view.update(column="queued", label="已排队")
    elif view["execution"] in {"running", "cancelling"}:
        view.update(column="working", label="停止中" if view["execution"] == "cancelling" else "执行中")
    if not isinstance(task, BackgroundTask) or not isinstance(task.owner_session, str) or not task.owner_session.strip():
        return view
    try:
        review = (task.collaboration["review"] if collaboration_state_valid(task)
                  else "unknown" if task.kind == "delegation" else "not_applicable")
        handoff = completion_state(task)
        # Service-shaped malformed/ownerless records cannot gain completion by
        # pretending that the absent handling contract is not applicable.
        handling = ("handled" if handoff["handled"] else "pending") if handoff else (
            "not_applicable" if task.kind == "delegation" and collaboration_state_valid(task)
            and not task.collaboration.get("dispatch") else "unknown")
        validity = _validity(task, sources)
        view.update(review=review, handling=handling, validity=validity,
                    round=task.collaboration["round"] if collaboration_state_valid(task) else None,
                    revision=handoff["revision"] if handoff else "")
    except (TypeError, ValueError, AttributeError):
        return view
    if validity == "invalid":
        view["label"] = "历史结果已失效 · 待处理"
    elif task.status == "queued":
        view.update(column="queued", label="已排队")
    elif task.status in {"running", "cancelling"}:
        view.update(column="working", label="停止中" if task.status == "cancelling" else "执行中")
    elif task.status == "done":
        if validity == "unknown" or review == "unknown" or handling == "unknown":
            view["label"] = "执行结束 · 状态待核对"
        elif review in {"pending", "not_submitted", "rework_requested"}:
            view["label"] = "待返工" if review == "rework_requested" else "执行结束 · 待验收"
        elif handling == "pending":
            view["label"] = "已验收 · 待处理交接" if review == "accepted" else "执行结束 · 待处理交接"
        elif task.kind == "delegation" and review == "accepted":
            view.update(column="completed", label="已验收" + (" · 交接已处理" if handling == "handled" else ""))
        elif task.kind in {"dev", "dev-resume"} and handling == "handled":
            view.update(column="completed", label="执行结束 · 交接已处理（不代表验收）")
    elif recovered_by and task.status in {"failed", "paused", "interrupted", "cancelled"}:
        view.update(column="history", label="已由恢复任务接续 · 历史记录")
    else:
        view["label"] = {"waiting": "等待前置验收", "blocked": "等待答复或处理", "failed": "执行失败",
                         "paused": "已暂停", "interrupted": "已中断", "cancelled": "已取消 · 查看记录"}.get(task.status, "状态待核对")
    return view
