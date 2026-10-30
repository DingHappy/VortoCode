"""Snapshot-bound Goal submissions, using the existing Goal/Task ledgers and pool."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from src.workflow.tasks.tasks import BackgroundTask, TaskRunner
from src.workflow.workbench.goals import Goal, GoalLedger


class GoalSubmissionConflict(ValueError):
    pass


def goal_task_id(goal_id: str, previous_task_id: str, resume: bool) -> str:
    key = json.dumps([goal_id, previous_task_id, resume], separators=(",", ":"))
    return "task-goal-" + hashlib.sha256(key.encode()).hexdigest()[:32]


def is_goal_submission_task_id(task_id: str) -> bool:
    return isinstance(task_id, str) and re.fullmatch(r"task-goal-[0-9a-f]{32}", task_id) is not None


def goal_contract(goal: Goal) -> str:
    data = [goal.objective, goal.constraints, goal.non_goals,
            [[item.id, item.text, asdict(item.verifier) if item.verifier else None]
             for item in goal.acceptance_criteria]]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def submit_goal(runner: TaskRunner, goal_id: str, *, previous_task_id: str = "",
                resume: bool = False, expected_contract: str | None = None) -> tuple[BackgroundTask, bool]:
    """One snapshot/intent creates one task; replay only reads, never re-enqueues."""
    if (not isinstance(previous_task_id, str) or len(previous_task_id) > 120
            or previous_task_id and not re.fullmatch(r"[A-Za-z0-9._-]+", previous_task_id)):
        raise GoalSubmissionConflict("上一项任务身份无效，请刷新目标")
    ledger = GoalLedger(runner.repo_root)
    if Path(runner.ledger.repo_root).resolve() != Path(ledger.repo_root).resolve():
        raise GoalSubmissionConflict("目标与任务运行端不一致")
    tid = goal_task_id(goal_id, previous_task_id, resume)
    kind = "dev-resume" if resume else "dev"
    with ledger.transaction(goal_id):
        goal = ledger.load(goal_id)
        if goal is None:
            raise KeyError(goal_id)
        if (goal.kind != "development" or not isinstance(goal.task_ids, list)
                or any(not isinstance(item, str) for item in goal.task_ids)):
            raise GoalSubmissionConflict("目标任务关联记录无效，请先核对")
        if expected_contract is not None and expected_contract != goal_contract(goal):
            raise GoalSubmissionConflict("目标合同已变化，请刷新后确认执行")
        task = runner.ledger.load(tid)
        if task is not None:
            claim = task.goal_submission
            if (task.id != tid or task.goal_id != goal.id or task.kind != kind
                    or not isinstance(claim, dict) or type(claim.get("version")) is not int or claim["version"] != 1
                    or claim.get("previous_task_id") != previous_task_id
                    or claim.get("contract") != goal_contract(goal)
                    or type(claim.get("enqueue_attempted")) is not bool or tid not in goal.task_ids):
                raise GoalSubmissionConflict("已有提交与目标合同不一致，未重新下发")
            if task.status == "queued" and not runner.is_active(tid):
                # The workspace-owning runtime has no worker for this durable queue entry.
                task.status = "interrupted"
                task.error = "提交曾中断或排队结果无法确认；未自动重新执行，请核对后开始新一轮。"
                if not runner.ledger.save(task):
                    raise OSError("任务中断状态未能保存；未重新执行")
            if not claim["enqueue_attempted"]:
                raise GoalSubmissionConflict(f"本轮提交未完成，未自动重跑；请核对任务 {tid} 后刷新目标")
            return task, True
        if runner.ledger.has_record(tid) or tid in goal.task_ids:
            raise GoalSubmissionConflict(f"本轮任务记录缺失或无法读取，未重新执行；请核对 {tid}")
        latest = goal.task_ids[-1] if goal.task_ids else ""
        if previous_task_id != latest:
            raise GoalSubmissionConflict("目标已有新的任务，请刷新后明确确认下一轮")
        if goal.status == "achieved":
            raise GoalSubmissionConflict("目标已经 achieved，无需重复执行")
        if resume and not goal.plan_id:
            raise GoalSubmissionConflict("目标还没有可续跑的 plan_id")
        for task_id in goal.task_ids:
            old = runner.ledger.load(task_id)
            if old is None:
                raise GoalSubmissionConflict("历史任务无法核实，请先检查任务台账")
            if old.status not in {"done", "failed", "cancelled", "paused", "interrupted"} or runner.is_active(task_id):
                raise GoalSubmissionConflict("目标已有后台任务在执行或等待处理")
        if runner._closing:
            raise GoalSubmissionConflict("运行端正在关闭，未提交新任务")
        prompt = goal.contract_prompt()  # Preserve the previous blocker in this intent's prompt.
        claim = {"version": 1, "previous_task_id": previous_task_id,
                 "contract": goal_contract(goal), "enqueue_attempted": False}
        goal.status, goal.blocker = "active", ""
        goal.next_action = "开发任务正在提交，请在任务卡核对执行状态"
        goal.task_ids.append(tid)
        # Write the fixed identity and association before creating or scheduling work.
        ledger.save_required(goal)
        task = runner.ledger.create(kind, prompt, tid=tid, goal_id=goal.id,
                                    plan_id=goal.plan_id if resume else "", goal_submission=claim)
        task.goal_submission["enqueue_attempted"] = True
        if not runner.ledger.save(task):
            raise OSError("提交认领未能保存；未排队执行")
        try:
            runner.enqueue_worker(tid)
        except Exception:
            raise GoalSubmissionConflict(f"排队结果无法确认，未自动重试；请核对任务 {tid}") from None
        return task, False
