"""Owner-scoped completion inbox derived from the existing durable task ledger.

No model scheduling: acknowledgement records handling, never task acceptance.
Receipts use a content revision rather than updated timestamps (saving a receipt
itself changes updated). Changed results become unread again.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Callable

from src.gateway.audit import _clip_text
from src.gateway.tasks import BackgroundTask, TaskLedger, TASK_STATE_LOCK, _now
from src.utils.ids import typed_id

_LOCK = TASK_STATE_LOCK


class HandoffConflict(ValueError):
    """Wrong owner, changed result or invalid completion receipt."""


TERMINAL = {"done", "failed", "cancelled", "paused", "interrupted"}


def _service_delegation(task: BackgroundTask) -> bool:
    state = task.collaboration
    if task.kind != "delegation" or not isinstance(state, dict):
        return False
    dispatch = state.get("dispatch")
    return (isinstance(dispatch, dict) and dispatch.get("source") == "api"
            and type(state.get("round")) is int and 1 <= state["round"] <= 3
            and isinstance(state.get("assignee"), str)
            and isinstance(state.get("review"), str)
            and state["review"] in {"not_submitted", "pending", "accepted", "rework_requested"}
            and isinstance(state.get("acceptance"), list)
            and len(state["acceptance"]) <= 20
            and all(isinstance(item, str) for item in state["acceptance"])
            and type(state.get("tainted")) is bool)


def revision(task: BackgroundTask) -> str:
    payload = [task.id, task.status, task.prompt, task.result, task.error,
               task.plan_id, task.branch, task.goal_id]
    if _service_delegation(task):
        state = task.collaboration
        # A new round with identical text is still a new delivery. Review and
        # message timestamps are not delivery identity, so receipts survive them.
        payload.append(["delegation", state["round"], state["assignee"],
                        state["acceptance"], state["tainted"]])
        if task.dependencies:
            payload.append(["dependencies", task.dependencies.get("requires"), task.dependencies.get("inputs")])
            if task.dependencies.get("invalidation"):
                payload.append(["invalidation", task.dependencies["invalidation"]])
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def completion_state(task: BackgroundTask) -> dict | None:
    """Project handling separately from execution and collaboration acceptance."""
    if (not isinstance(task.status, str) or task.status not in TERMINAL or not isinstance(task.owner_session, str)
            or not task.owner_session.strip()
            or (task.kind not in {"dev", "dev-resume"} and not _service_delegation(task))):
        return None
    receipt = task.handoff_receipt if isinstance(task.handoff_receipt, dict) else {}
    current_revision = revision(task)
    stamp = receipt.get("handled_at")
    note = receipt.get("note")
    try:
        stamped = isinstance(stamp, str) and datetime.fromisoformat(stamp).utcoffset() is not None
    except ValueError:
        stamped = False
    handled = (receipt.get("revision") == current_revision and stamped
               and isinstance(note, str) and bool(note.strip()))
    return {"revision": current_revision, "handled": handled,
            "handled_at": stamp if handled and isinstance(stamp, str) else ""}


class CompletionInbox:
    def __init__(self, repo_root: str, owner: str | Callable[[], str], *,
                 on_update: Callable[[BackgroundTask], None] | None = None):
        self.ledger = TaskLedger(repo_root)
        self._owner = owner
        self._on_update = on_update

    def owner_identity(self) -> str:
        owner = self._owner() if callable(self._owner) else self._owner
        if not isinstance(owner, str) or not owner.strip():
            raise HandoffConflict("交接缺少所属会话身份")
        return owner

    def pending(self, limit: int = 20) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("交接读取上限必须是 1 到 20 的整数")
        owner = self.owner_identity()
        with _LOCK:
            items = []
            for task in self.ledger.list():
                state = completion_state(task)
                if task.owner_session != owner or state is None or state["handled"]:
                    continue
                items.append(self._view(task))
                if len(items) == limit:
                    break
            return items

    def get(self, task_id: str, expected_revision: str) -> dict:
        """Read one exact, owned, unread delivery for a bounded check."""
        with _LOCK:
            task = self.ledger.load(task_id) if typed_id(task_id, "task") else None
            state = completion_state(task) if task is not None else None
            if task is None or state is None or task.owner_session != self.owner_identity():
                raise HandoffConflict("交接不存在或不属于当前会话")
            if state["revision"] != expected_revision or state["handled"]:
                raise HandoffConflict("交接已处理或结果版本已变化")
            return self._view(task)

    def _view(self, task: BackgroundTask) -> dict:
        view = {"task_id": task.id, "kind": task.kind, "revision": revision(task), "status": task.status,
                "prompt": _clip_text(task.prompt, 2000), "result": _clip_text(task.result, 4000),
                "error": _clip_text(task.error, 2000), "plan_id": task.plan_id,
                "branch": task.branch, "goal_id": task.goal_id,
                "next_action": "核对结果与证据、向用户汇报或说明阻塞，再确认交接已处理；done 不等于验收通过。"}
        if _service_delegation(task):
            state = task.collaboration
            view.update({"round": state["round"], "agent": _clip_text(state["assignee"], 120),
                         "review": state["review"], "tainted": state["tainted"],
                         "acceptance": [_clip_text(item, 500) for item in state["acceptance"]],
                         "next_action": "用 task_status 核对当前轮次与证据，必要时 task_review 记录验收；"
                                        "向用户汇报后确认交接已处理。处理回执不等于验收通过。"})
            from src.gateway.task_dependencies import dependency_view
            dependencies = dependency_view(task, self.ledger)
            if dependencies:
                view["dependencies"] = dependencies
                if dependencies.get("result_valid") is False:
                    view["next_action"] = dependencies["next_action"]
        return view

    def acknowledge(self, task_id: str, expected_revision: str, note: str) -> dict:
        if not typed_id(task_id, "task") or not isinstance(note, str) or not note.strip():
            raise HandoffConflict("需要有效任务 ID 和具体处理说明")
        with _LOCK:
            task = self.ledger.load(task_id)
            state = completion_state(task) if task is not None else None
            if task is None or state is None or task.owner_session != self.owner_identity():
                raise HandoffConflict("交接不存在或不属于当前会话")
            if expected_revision != revision(task):
                raise HandoffConflict("交接结果已变化，请重新读取 task_inbox")
            if not state["handled"]:
                task.handoff_receipt = {"revision": expected_revision, "note": _clip_text(note, 2000),
                                        "handled_at": _now()}
                if not self.ledger.save(task):
                    raise OSError("交接处理回执未能持久化；仍视为未处理")
                if self._on_update is not None:
                    try:
                        self._on_update(task)
                    except Exception:  # noqa: BLE001 - transport failure cannot undo the receipt.
                        pass
            return {"task_id": task.id, "revision": expected_revision, "handled": True}
