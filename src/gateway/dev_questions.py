"""Plan/block-bound development input, on the original ledger and worker pool."""
from __future__ import annotations

import hashlib
from typing import Callable

from src.gateway.collaboration import CollaborationConflict
from src.gateway.dispatch import DispatchBusy, session_identity
from src.gateway.task_questions import _expired, _now, _text, cancel_open_questions, new_question
from src.gateway.task_recovery import plan_revision
from src.gateway.tasks import TASK_STATE_LOCK, BackgroundTask, TaskRunner
from src.utils.ids import typed_id

_LIMITS = {"max_steps": 16, "max_attempts": 2, "max_rounds": 3, "max_questions": 2}


def development_state(task: BackgroundTask) -> dict:
    state = task.development
    if not state:
        return {"version": 1, "round": 1, "owner_session": task.owner_session,
                "plan_id": task.plan_id, "root_task_id": task.id, "questions": [], "tainted": False,
                "limits": dict(_LIMITS)}
    if (not isinstance(state, dict) or type(state.get("version")) is not int or state.get("version") != 1
            or type(state.get("round")) is not int or not 1 <= state["round"] <= 3
            or state.get("owner_session") != task.owner_session or state.get("plan_id") != task.plan_id
            or not typed_id(state.get("root_task_id"), "task") or type(state.get("tainted")) is not bool
            or state.get("limits") != _LIMITS
            or not isinstance(state.get("questions"), list) or len(state["questions"]) > 2
            or any(not isinstance(q, dict) for q in state["questions"])):
        raise CollaborationConflict("开发问答合同损坏或归属已变化")
    return state


class DevelopmentQuestions:
    def __init__(self, runner: TaskRunner, *, read_plan: Callable,
                 validate_execution: Callable, on_update: Callable | None = None):
        self.runner, self.ledger = runner, runner.ledger
        self.read_plan, self.validate_execution = read_plan, validate_execution
        self.on_update = on_update

    def get(self, owner: str, task_id: str) -> BackgroundTask:
        task = self.ledger.load(task_id)
        if (task is None or task.kind not in {"dev", "dev-resume"}
                or task.owner_session != session_identity(owner)):
            raise CollaborationConflict("开发任务不存在或不属于此会话")
        development_state(task)
        return task

    def _plan(self, task):
        plan = self.read_plan(task.plan_id)
        if (plan is None or plan.plan_id != task.plan_id or not isinstance(plan.branch, str)
                or not plan.branch.startswith("vorto/") or task.branch and task.branch != plan.branch):
            raise CollaborationConflict("开发计划身份或隔离分支已变化")
        return plan

    def begin(self, task: BackgroundTask) -> None:
        """An answered round must run the exact plan authorized by its answer."""
        if not task.development:
            return
        state = development_state(task)
        self.validate_execution(task)
        if state.get("execution_revision"):
            if state["execution_revision"] != plan_revision(self._plan(task)):
                raise CollaborationConflict("回答后计划已变化；未执行，请重新核对计划")
            state["execution_revision"] = ""
            if not self.ledger.save(task):
                raise OSError("开发问答执行检查点未能保存")

    def stage(self, task: BackgroundTask, round_number: int, plan_id: str, block_id: str,
              revision: str, question: str, options: list[str], context: str, *, tainted: bool = False) -> None:
        with TASK_STATE_LOCK:
            current = self.get(task.owner_session, task.id)
            state = development_state(current)
            plan = self._plan(current)
            block = plan.block(block_id)
            if (current.status != "running" or state["round"] != round_number or round_number >= 3
                    or len(state["questions"]) >= 2 or any(q.get("status") == "open" for q in state["questions"])
                    or plan_id != current.plan_id or revision != plan_revision(plan)
                    or block is None or block.status != "running"):
                raise CollaborationConflict("开发问题的任务、块或计划版本已变化，或已用完问答次数")
            item = new_question(current.id, round_number, "developer", question, options, context)
            item.update(plan_id=plan_id, block_id=block_id, plan_revision=revision, branch=plan.branch)
            current.development = {**state, "questions": [*state["questions"], item],
                                   "tainted": bool(state.get("tainted") or tainted), "waiting": item["id"]}
            current.branch = plan.branch
            if not self.ledger.save(current):
                raise OSError("开发问题未能保存；未进入等待回答")
            # Keep the runner's live record in sync; no blocked event until cleanup.
            task.development, task.branch = current.development, current.branch

    def awaiting(self, task: BackgroundTask) -> bool:
        state = development_state(task)
        questions = state["questions"]
        return bool(questions and questions[-1].get("id") == state.get("waiting")
                    and questions[-1].get("task_id") == task.id and questions[-1].get("status") == "open")

    def answer(self, owner: str, task_id: str, round_number: int, question_id: str,
               answer: str) -> tuple[BackgroundTask, bool]:
        _text(answer, 2000, "回答")
        if type(round_number) is not int or not 1 <= round_number < 3 or not typed_id(question_id, "question"):
            raise CollaborationConflict("必须提供精确问题 ID 和提问轮次")
        with TASK_STATE_LOCK:
            task = self.get(owner, task_id)
            state = development_state(task)
            matches = [q for q in state["questions"] if q.get("id") == question_id]
            if len(matches) != 1:
                raise CollaborationConflict("开发问题不存在或已变化")
            question = matches[0]
            if (question.get("task_id") != task.id or question.get("round") != round_number
                    or question.get("answerer") != "owner" or question.get("plan_id") != task.plan_id):
                raise CollaborationConflict("开发问题身份或轮次不匹配")
            fingerprint = hashlib.sha256(answer.strip().encode()).hexdigest()
            if (question.get("status") == "answered" and question.get("answer_fingerprint") == fingerprint
                    and question.get("resumed_round") == round_number + 1 and state["round"] >= round_number + 1):
                return task, True
            if (task.status != "blocked" or state["round"] != round_number or question.get("status") != "open"
                    or state.get("waiting") != question_id or _expired(question)):
                raise CollaborationConflict("开发问题已回答、取消、过期或轮次已变化")
            if self.runner.is_active(task.id):
                raise CollaborationConflict("开发工作树尚未结束清理，请稍后重试")
            if self.runner.active_count >= 100:
                raise DispatchBusy("后台任务队列已满")
            if any(t.plan_id == task.plan_id and self.runner.is_active(t.id) for t in self.ledger.list()):
                raise CollaborationConflict("该开发计划已有活动任务")
            plan = self._plan(task)
            block = plan.block(question.get("block_id"))
            if (plan.status != "running" or question.get("plan_revision") != plan_revision(plan) or question.get("branch") != plan.branch
                    or block is None or block.status != "running"):
                raise CollaborationConflict("提问后的计划或块已变化；请取消问题并核对最新计划")
            self.validate_execution(task)
            question.update(status="answered", answer=_text(answer, 2000, "回答"), answered_at=_now().isoformat(),
                            answer_fingerprint=fingerprint, resumed_round=round_number + 1)
            state.update(round=round_number + 1, waiting="", execution_revision=plan_revision(plan))
            task.development = state
            task.status, task.result, task.error = "queued", "", ""
            if not self.ledger.save(task):
                raise OSError("开发回答未能保存；未恢复执行")
            self.runner.enqueue_worker(task.id)
            return task, False

    def cancel_waiting(self, task_id: str) -> bool:
        with TASK_STATE_LOCK:
            task = self.ledger.load(task_id)
            if task is None or task.kind not in {"dev", "dev-resume"} or task.status != "blocked":
                return False
            cancel_open_questions(task)
            task.development["waiting"] = ""
            task.status = "cancelled"
            if not self.ledger.save(task):
                raise OSError("开发问题取消状态未能保存")
            if self.on_update:
                self.on_update(task)
            return True
