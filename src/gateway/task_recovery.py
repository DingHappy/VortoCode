"""One durable resumption per stopped development task, using the shared pool."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Callable, Iterable

from src.gateway.tasks import BackgroundTask, TASK_STATE_LOCK, TaskLedger, TaskRunner

_KINDS = {"dev", "dev-resume"}
_STOPPED = {"paused", "interrupted", "failed", "cancelled"}


class RecoveryConflict(ValueError):
    pass


def recovery_task_id(source_id: str) -> str:
    return "task-resume-" + hashlib.sha256(source_id.encode()).hexdigest()[:32]


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source_revision(task: BackgroundTask) -> str:
    fields = [task.id, task.kind, task.prompt, task.owner_session, task.goal_id,
              task.plan_id, task.branch, task.status, task.result, task.error]
    if task.development:
        fields.append(task.development)
    return _digest(fields)


def plan_revision(plan) -> str:
    data = plan.to_dict()
    data.pop("updated", None)
    return _digest(data)


def _matches(child: BackgroundTask, source: BackgroundTask) -> bool:
    contract = child.recovery
    return not (source.kind not in _KINDS or source.status not in _STOPPED
            or child.id != recovery_task_id(source.id)
            or child.kind != "dev-resume" or child.parent_task_id != source.id
            or child.owner_session != source.owner_session or child.goal_id != source.goal_id
            or child.prompt != source.prompt or child.plan_id != source.plan_id
            or not isinstance(contract, dict) or contract.get("source_task_id") != source.id
            or contract.get("source_revision") != source_revision(source)
            or not isinstance(contract.get("plan_revision"), str) or len(contract["plan_revision"]) != 64)


def recovery_links(tasks: Iterable[BackgroundTask]) -> dict[str, str]:
    """Project validated lineage without hiding unmatched historical failures."""
    by_id = {task.id: task for task in tasks if isinstance(task, BackgroundTask)}
    links = {}
    for child in by_id.values():
        source = by_id.get(child.parent_task_id)
        if source is not None and _matches(child, source):
            links[source.id] = child.id
    return links


def resumed_task(ledger: TaskLedger, source: BackgroundTask) -> BackgroundTask | None:
    """Read the exact owned child; corrupt or mismatched records never grant work."""
    child = ledger.load(recovery_task_id(source.id))
    if child is None:
        if ledger.has_record(recovery_task_id(source.id)):
            raise RecoveryConflict("恢复记录已存在但不可读取，不能重新执行")
        return None
    if not _matches(child, source):
        raise RecoveryConflict("恢复记录与原任务合同不一致，请检查任务台账")
    return child


def validate_recovery_plan(task: BackgroundTask, plan) -> None:
    """Queued resumptions cannot silently run a plan edited after authorization."""
    contract = task.recovery
    if not contract:  # Legacy/Goal resumptions retain their existing entry contract.
        return
    if (not isinstance(contract, dict) or task.kind != "dev-resume"
            or contract.get("source_task_id") != task.parent_task_id
            or plan is None or plan.plan_id != task.plan_id
            or contract.get("plan_revision") != plan_revision(plan)):
        raise RecoveryConflict("持久计划在恢复排队后已变化；未执行，请重新审阅最新计划")


class TaskRecovery:
    def __init__(self, runner: TaskRunner, *, read_plan: Callable, on_update: Callable | None = None):
        self.runner = runner
        self.ledger = runner.ledger
        self.read_plan = read_plan
        self.on_update = on_update

    def resume(self, task_id: str) -> tuple[BackgroundTask, bool]:
        with TASK_STATE_LOCK:
            previous = self.ledger.load(task_id)
            if previous is None:
                raise RecoveryConflict("任务不存在或不可读取")
            if previous.kind not in _KINDS or previous.status not in _STOPPED:
                raise RecoveryConflict("只有已停止的开发任务可以通过此入口恢复")
            existing = resumed_task(self.ledger, previous)
            if existing is not None:
                # Replays never enqueue, even after completion or process restart.
                return existing, True
            if not previous.plan_id:
                raise RecoveryConflict("任务还没有持久 plan_id，无法恢复")
            development = {}
            if previous.development:
                from src.gateway.dev_questions import development_state
                development = deepcopy(development_state(previous))
                if any(q.get("status") == "open" for q in development["questions"]):
                    raise RecoveryConflict("开发任务仍有未回答问题，不能通过普通恢复跳过")
                development.update(waiting="", execution_revision="")
            plan = self.read_plan(previous.plan_id)
            if plan is None or plan.plan_id != previous.plan_id or plan.status not in {"running", "integrated", "integration_failed", "failed"}:
                raise RecoveryConflict("持久计划不存在、身份无效或已经完成")
            if (not isinstance(plan.branch, str) or not plan.branch.startswith("vorto/")
                    or previous.branch and plan.branch != previous.branch):
                raise RecoveryConflict("恢复计划必须保留原隔离开发分支")
            if any(item.plan_id == previous.plan_id and self.runner.is_active(item.id)
                   for item in self.ledger.list()):
                raise RecoveryConflict("该计划已有任务在执行")
            if self.runner.active_count >= 100:
                raise RecoveryConflict("后台任务队列已满，请稍后重试")
            child = self.ledger.create(
                "dev-resume", previous.prompt, tid=recovery_task_id(previous.id),
                goal_id=previous.goal_id, plan_id=previous.plan_id,
                parent_task_id=previous.id, owner_session=previous.owner_session,
                development=development,
                recovery={"source_task_id": previous.id, "source_revision": source_revision(previous),
                          "plan_revision": plan_revision(plan)})
            self.runner.enqueue_worker(child.id)
            if self.on_update is not None:
                try:
                    self.on_update(previous)
                except Exception:  # noqa: BLE001 - durable resumption survives transport failure.
                    pass
            return child, False
