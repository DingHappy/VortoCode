"""Durable, result-bound check authorization. No scheduler or model execution.

Claims are write-ahead and never retried automatically. One process owns the
ledger; the shared domain lock is not a multi-process/distributed lease.
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import asdict
from typing import Callable

from src.gateway.audit import _clip_text
from src.gateway.handoffs import completion_state
from src.gateway.tasks import BackgroundTask, TASK_STATE_LOCK, TaskLedger, _now
from src.llm.hard_budget import CheckLimits
from src.utils.ids import typed_id


class ContinuationConflict(ValueError):
    """Missing authority, stale result, or already consumed authorization."""


def _entries(task: BackgroundTask) -> dict:
    state = task.continuation
    if not isinstance(state, dict) or (state and set(state) != {"entries"}):
        raise ContinuationConflict("检查授权记录损坏，禁止执行")
    entries = state.get("entries", {})
    if (not isinstance(entries, dict) or len(entries) > 20
            or any(not isinstance(key, str) or not isinstance(item, dict)
                   for key, item in entries.items())):
        raise ContinuationConflict("检查授权记录损坏，禁止执行")
    return entries


class ContinuationService:
    def __init__(self, repo_root: str, owner: str | Callable[[], str], *,
                 on_update: Callable[[BackgroundTask], None] | None = None):
        self.ledger = TaskLedger(repo_root)
        self._owner = owner
        self._on_update = on_update

    def owner_identity(self) -> str:
        owner = self._owner() if callable(self._owner) else self._owner
        if not isinstance(owner, str) or not owner.strip():
            raise ContinuationConflict("检查授权缺少所属会话身份")
        return owner

    def _load(self, task_id: str, expected_revision: str) -> BackgroundTask:
        owner = self.owner_identity()
        task = self.ledger.load(task_id) if typed_id(task_id, "task") else None
        state = completion_state(task) if task else None
        if (not isinstance(owner, str) or not owner.strip() or task is None
                or task.owner_session != owner or state is None):
            raise ContinuationConflict("检查任务不存在或不属于当前会话")
        if expected_revision != state["revision"]:
            raise ContinuationConflict("结果版本已变化，请重新读取交接")
        if state["handled"]:
            raise ContinuationConflict("该结果已处理，无需自动检查")
        return task

    def _save(self, task: BackgroundTask, entry: dict) -> dict:
        if not self.ledger.save(task):
            raise OSError("检查状态未能持久化；禁止继续执行")
        self.notify_update(task)
        return copy.deepcopy(entry)

    def notify_update(self, task: BackgroundTask) -> None:
        if self._on_update is not None:
            try:
                self._on_update(task)
            except Exception:  # noqa: BLE001 - durable state survives transport failure.
                pass

    def authorize(self, task_id: str, expected_revision: str, *,
                  limits: CheckLimits | None = None) -> dict:
        """Explicit caller authorization, not inferred from task completion."""
        if limits is not None and not isinstance(limits, CheckLimits):
            raise ValueError("需要有效的严格检查预算")
        contract = asdict(limits or CheckLimits())
        with TASK_STATE_LOCK:
            task = self._load(task_id, expected_revision)
            entries = _entries(task)
            if expected_revision in entries:
                existing = entries[expected_revision]
                existing = self._entry(task, expected_revision, existing.get("id"))
                if existing.get("status") == "authorized" and existing.get("limits") == contract:
                    self._unclaimed(existing)
                    self._limits(existing)
                    return copy.deepcopy(existing)
                raise ContinuationConflict("该结果版本已授权或消耗，不能重新授权")
            if len(entries) >= 20:
                raise ContinuationConflict("检查授权记录已达上限")
            entry = {"id": "check-" + uuid.uuid4().hex, "revision": expected_revision,
                     "owner": task.owner_session, "limits": contract, "status": "authorized",
                     "authorized_at": _now()}
            entries[expected_revision] = entry
            task.continuation = {"entries": entries}
            return self._save(task, entry)

    def _entry(self, task: BackgroundTask, rev: str, authorization_id: str) -> dict:
        entry = _entries(task).get(rev)
        if (not isinstance(authorization_id, str) or not typed_id(authorization_id, "check")
                or not isinstance(entry, dict) or entry.get("id") != authorization_id
                or entry.get("owner") != task.owner_session or entry.get("revision") != rev):
            raise ContinuationConflict("检查授权不存在或身份不匹配")
        return entry

    @staticmethod
    def _limits(entry: dict) -> CheckLimits:
        try:
            limits = entry["limits"]
            if not isinstance(limits, dict) or set(limits) != set(CheckLimits.__dataclass_fields__):
                raise ValueError("所有预算字段必须显式保存")
            return CheckLimits(**limits)
        except (KeyError, TypeError, ValueError) as exc:
            raise ContinuationConflict("检查预算记录损坏，禁止执行") from exc

    @staticmethod
    def _unclaimed(entry: dict) -> None:
        if (not isinstance(entry.get("authorized_at"), str) or not entry["authorized_at"].strip()
                or any(key in entry for key in ("attempt_id", "claimed_at", "finished_at", "report", "budget"))):
            raise ContinuationConflict("检查授权记录损坏或已有尝试，禁止重新认领")

    @staticmethod
    def _attempt(entry: dict, attempt_id: str) -> None:
        if (not isinstance(attempt_id, str) or not typed_id(attempt_id, "attempt")
                or entry.get("attempt_id") != attempt_id):
            raise ContinuationConflict("检查尝试身份不匹配")

    def claim(self, task_id: str, expected_revision: str, authorization_id: str) -> dict:
        """Call only after provider/actor eligibility checks; persist before executing."""
        with TASK_STATE_LOCK:
            task = self._load(task_id, expected_revision)
            entry = self._entry(task, expected_revision, authorization_id)
            if entry.get("status") != "authorized":
                raise ContinuationConflict("检查授权已认领、撤销或消耗")
            self._unclaimed(entry)
            self._limits(entry)
            entry.update(status="claimed", attempt_id="attempt-" + uuid.uuid4().hex,
                         claimed_at=_now())
            return self._save(task, entry)

    def pending_authorization(self, task_id: str, expected_revision: str, authorization_id: str) -> dict:
        with TASK_STATE_LOCK:
            task = self._load(task_id, expected_revision)
            entry = self._entry(task, expected_revision, authorization_id)
            if entry.get("status") != "authorized":
                raise ContinuationConflict("检查授权已认领、撤销或消耗")
            self._unclaimed(entry)
            self._limits(entry)
            return copy.deepcopy(entry)

    def completed_report(self, task_id: str, expected_revision: str, authorization_id: str,
                         attempt_id: str) -> dict:
        """Read an authoritative completed check, including after handling.

        Report redelivery never authorizes a model request. Result versions and
        ownership must still match; old reports cannot be delivered as new work.
        """
        with TASK_STATE_LOCK:
            task = self.ledger.load(task_id) if typed_id(task_id, "task") else None
            state = completion_state(task) if task else None
            if (task is None or task.id != task_id or state is None
                    or task.owner_session != self.owner_identity()):
                raise ContinuationConflict("检查任务不存在或不属于当前会话")
            if state["revision"] != expected_revision:
                raise ContinuationConflict("结果版本已变化，不能补发旧检查")
            entry = self._entry(task, expected_revision, authorization_id)
            self._attempt(entry, attempt_id)
            if (entry.get("status") != "completed"
                    or not isinstance(entry.get("report"), str) or not entry["report"].strip()
                    or not isinstance(entry.get("budget"), dict)):
                raise ContinuationConflict("该尝试没有可补发的完整检查报告")
            self._budget(entry, entry["budget"])
            self._completed_budget(entry)
            return copy.deepcopy(entry)

    @staticmethod
    def _budget(entry: dict, budget: dict | None) -> None:
        if budget is None:
            return
        ContinuationService._limits(entry)
        if not isinstance(budget, dict) or budget.get("limits") != entry["limits"]:
            raise ContinuationConflict("检查用量与授权预算不匹配")
        ContinuationService._limits(budget)
        for key in ("requests", "tool_calls", "spent_tokens", "committed_tokens"):
            if type(budget.get(key)) is not int or budget[key] < 0:
                raise ContinuationConflict("检查用量记录无效")
        if any(budget[key] > entry["limits"][key] for key in ("requests", "tool_calls")):
            raise ContinuationConflict("检查调用用量超过授权限制")
        if (type(budget.get("usage_complete")) is not bool
                or not isinstance(budget.get("blocked_reason"), str)):
            raise ContinuationConflict("检查用量记录无效")
        if budget["spent_tokens"] > budget["committed_tokens"]:
            raise ContinuationConflict("检查用量超过已记录的预算承诺")
        entry["budget"] = {key: copy.deepcopy(budget[key]) for key in (
            "limits", "requests", "tool_calls", "spent_tokens", "committed_tokens", "usage_complete")}
        entry["budget"]["blocked_reason"] = _clip_text(budget["blocked_reason"], 500)

    @staticmethod
    def _completed_budget(entry: dict) -> None:
        budget = entry.get("budget")
        if (not isinstance(budget, dict) or budget.get("usage_complete") is not True
                or budget.get("blocked_reason") or budget["spent_tokens"] != budget["committed_tokens"]
                or budget["committed_tokens"] > entry["limits"]["tokens"]):
            raise ContinuationConflict("检查预算未结清、已阻塞或超限，不能视为完成")

    def finish(self, task_id: str, expected_revision: str, authorization_id: str,
               attempt_id: str, *, outcome: str, report: str, budget: dict | None = None) -> dict:
        """Record check outcome only; does not acknowledge delivery or accept task."""
        if outcome not in {"completed", "failed", "cancelled"} or not isinstance(report, str) or not report.strip():
            raise ContinuationConflict("需要有效检查结论和说明")
        with TASK_STATE_LOCK:
            task = self._load(task_id, expected_revision)
            entry = self._entry(task, expected_revision, authorization_id)
            self._attempt(entry, attempt_id)
            if entry.get("status") != "claimed":
                raise ContinuationConflict("检查尝试已结束、撤销或身份不匹配")
            if outcome == "completed" and budget is None:
                raise ContinuationConflict("完成检查必须提供完整预算记录")
            self._budget(entry, budget)
            if outcome == "completed":
                self._completed_budget(entry)
            entry.update(status=outcome, finished_at=_now(), report=_clip_text(report, 4000))
            return self._save(task, entry)

    def terminate(self, task_id: str, expected_revision: str, authorization_id: str,
                  attempt_id: str, *, outcome: str, budget: dict | None = None) -> dict:
        """Close interrupted execution even after a receipt or result change.

        Does not submit a potentially stale check report. Revocation remains
        authoritative. Cancellation snapshots can include unknown reserved usage.
        """
        if outcome not in {"failed", "cancelled"}:
            raise ContinuationConflict("需要有效检查中断状态")
        with TASK_STATE_LOCK:
            task = self.ledger.load(task_id) if typed_id(task_id, "task") else None
            if task is None or task.owner_session != self.owner_identity():
                raise ContinuationConflict("检查任务不存在或不属于当前会话")
            entry = self._entry(task, expected_revision, authorization_id)
            self._attempt(entry, attempt_id)
            if entry.get("status") != "claimed":
                if entry.get("status") == "revoked" and budget is not None:
                    self._budget(entry, budget)
                    return self._save(task, entry)
                return copy.deepcopy(entry)
            self._budget(entry, budget)
            entry.update(status=outcome, finished_at=_now(), report="检查未完成，交接继续待处理。")
            return self._save(task, entry)

    def revoke(self, task_id: str, expected_revision: str, authorization_id: str) -> dict:
        with TASK_STATE_LOCK:
            # Revocation also works after a manual receipt or a new result.
            owner = self._owner() if callable(self._owner) else self._owner
            task = self.ledger.load(task_id) if typed_id(task_id, "task") else None
            if not isinstance(owner, str) or not owner.strip() or task is None or task.owner_session != owner:
                raise ContinuationConflict("检查任务不存在或不属于当前会话")
            entry = self._entry(task, expected_revision, authorization_id)
            if entry.get("status") == "revoked":
                return copy.deepcopy(entry)
            if entry.get("status") not in {"authorized", "claimed"}:
                raise ContinuationConflict("检查尝试已结束")
            entry.update(status="revoked", finished_at=_now())
            return self._save(task, entry)


def recover_claims(ledger: TaskLedger) -> list[BackgroundTask]:
    """Startup-only reconciliation, never a retry trigger or live lease expiry."""
    recovered = []
    with TASK_STATE_LOCK:
        for task in ledger.list():
            try:
                entries = _entries(task)
            except ContinuationConflict:
                continue  # Corrupt records stay blocked, not reset to grantable.
            changed = False
            for entry in entries.values():
                if entry.get("status") == "claimed":
                    entry.update(status="interrupted", finished_at=_now(),
                                 report="服务重启，检查可能已产生用量；不自动重试，交接仍待处理。")
                    changed = True
            if changed:
                if not ledger.save(task):
                    raise OSError("中断检查对账未能持久化")
                recovered.append(task)
    return recovered
