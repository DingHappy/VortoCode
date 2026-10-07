"""Durable delegation lifecycle on the existing task ledger.

Execution completion and parent acceptance are independent. This service has
no model, client, scheduler or WebSocket dependency. Each update reloads the
ledger under a lock and rejects stale rounds, including late worker results.
"""
from __future__ import annotations

from typing import Callable

from src.gateway.audit import _clip_text
from src.gateway.tasks import BackgroundTask, TaskLedger, TASK_STATE_LOCK, _now
from src.utils.ids import typed_id

_LOCK = TASK_STATE_LOCK
_MAX_ROUNDS = 3
_MAX_MESSAGES = 32


def _text(value: object, limit: int = 4000) -> str:
    return _clip_text(value, limit)


class CollaborationConflict(ValueError):
    """Wrong owner, stale execution or invalid lifecycle transition."""


class CollaborationService:
    def __init__(self, repo_root: str, owner: str | Callable[[], str], *,
                 on_update: Callable[[BackgroundTask], None] | None = None):
        self.ledger = TaskLedger(repo_root)
        self._owner = owner
        self._on_update = on_update

    def owner_identity(self) -> str:
        """Resolve the current caller identity without exposing service internals."""
        owner = self._owner() if callable(self._owner) else self._owner
        if not isinstance(owner, str) or not owner.strip():
            raise CollaborationConflict("任务缺少发起 Agent 身份")
        return owner

    def _save(self, task: BackgroundTask) -> BackgroundTask:
        if not self.ledger.save(task):
            raise OSError("任务状态未能持久化；未继续执行")
        self.notify_update(task)
        return task

    def notify_update(self, task: BackgroundTask) -> None:
        """Best-effort transport notification after an independently durable save."""
        if self._on_update is not None:
            try:
                self._on_update(task)
            except Exception:  # noqa: BLE001 - transport failures do not undo durable state.
                pass

    def _load(self, task_id: str, expected_round: int | None = None) -> BackgroundTask:
        if not typed_id(task_id, "task"):
            raise CollaborationConflict("无效任务 ID")
        task = self.ledger.load(task_id)
        if task is None or task.kind != "delegation" or task.owner_session != self.owner_identity():
            raise CollaborationConflict("任务不存在或不属于当前发起 Agent")
        if expected_round is not None and (
            type(expected_round) is not int or expected_round != task.collaboration.get("round")
        ):
            raise CollaborationConflict("任务轮次已变化，请重新读取任务")
        return task

    def _round(self, expected_round: int) -> None:
        if type(expected_round) is not int or expected_round < 1:
            raise CollaborationConflict("必须提供当前执行轮次整数")

    def _message(self, task: BackgroundTask, kind: str, sender: str, body: str) -> None:
        state = task.collaboration
        seq = state.get("message_seq", 0) + 1
        state["message_seq"] = seq
        state["messages"] = (state.get("messages", []) + [{
            "id": f"{task.id}-message-{seq}", "kind": kind, "sender": sender,
            "round": state["round"], "body": _text(body), "created": _now(),
        }])[-_MAX_MESSAGES:]

    def create(self, prompt: str, assignee: str = "", acceptance: list[str] | None = None, *,
               task_id: str | None = None, dispatch: dict | None = None,
               dependencies: dict | None = None, chain_limits: dict | None = None) -> BackgroundTask:
        if not prompt.strip():
            raise CollaborationConflict("任务目标不能为空")
        if acceptance is not None and (
            not isinstance(acceptance, list) or len(acceptance) > 20
            or any(not isinstance(item, str) or not item.strip() for item in acceptance)
        ):
            raise CollaborationConflict("验收标准必须是最多 20 项的非空字符串列表")
        with _LOCK:
            if task_id and (not typed_id(task_id, "task") or self.ledger.load(task_id) is not None):
                raise CollaborationConflict("任务 ID 无效或已存在")
            task = BackgroundTask.new("delegation", _text(prompt), tid=task_id,
                                      owner_session=self.owner_identity())
            task.collaboration = {
                "assignee": assignee, "round": 1, "review": "not_submitted",
                "acceptance": [_text(item, 500) for item in acceptance or []],
                "messages": [], "message_seq": 0, "tainted": False,
            }
            if dispatch is not None:
                task.collaboration["dispatch"] = dispatch
            if dependencies:
                task.dependencies = dependencies
                task.status = "waiting"
            if chain_limits is not None or (dispatch and dispatch.get("source") == "api"):
                from src.gateway.task_chain_budget import prepare
                task.chain_budget = prepare(task, self.ledger, (dependencies or {}).get("requires", []), chain_limits)
            self._message(task, "assigned", task.owner_session, prompt)
            return self._save(task)

    def get(self, task_id: str) -> BackgroundTask:
        with _LOCK:
            return self._load(task_id)

    def list(self) -> list[BackgroundTask]:
        with _LOCK:
            owner = self.owner_identity()
            return [task for task in self.ledger.list()
                    if task.kind == "delegation" and task.owner_session == owner][:100]

    def start(self, task_id: str, expected_round: int) -> BackgroundTask:
        self._round(expected_round)
        with _LOCK:
            task = self._load(task_id, expected_round)
            if task.status != "queued":
                raise CollaborationConflict("任务已经开始或结束，不能重复执行")
            from src.gateway.task_dependencies import has_dependencies, validate_consumed
            if has_dependencies(task):
                validate_consumed(task, self.ledger)
            from src.gateway.task_chain_budget import validate_reserved
            validate_reserved(task, self.ledger)
            task.status = "running"
            self._message(task, "started", task.collaboration["assignee"], "已领取任务")
            return self._save(task)

    def finish(self, task_id: str, expected_round: int, result: str, *,
               error: str = "", cancelled: bool = False, tainted: bool = False) -> BackgroundTask:
        self._round(expected_round)
        with _LOCK:
            task = self._load(task_id, expected_round)
            if task.status != "running":
                raise CollaborationConflict("任务不在运行中，拒绝重复提交结果")
            from src.gateway.task_dependencies import has_dependencies, invalidate
            if has_dependencies(task):
                invalidate(task, self.ledger)
                if task.dependencies["resolution"] == "invalidated":
                    result, error, cancelled = "", task.dependencies["invalidation"]["reason"], False
            task.status = "cancelled" if cancelled else "failed" if error or not result.strip() else "done"
            task.result, task.error = _text(result, 8000), _text(error, 1000)
            task.collaboration["tainted"] = task.collaboration.get("tainted", False) or tainted
            task.collaboration["review"] = "pending" if task.status == "done" else "not_submitted"
            self._message(task, "submitted" if task.status == "done" else task.status,
                          task.collaboration["assignee"], task.error or task.result)
            return self._save(task)

    def review(self, task_id: str, expected_round: int, verdict: str, note: str) -> BackgroundTask:
        self._round(expected_round)
        if verdict not in {"accept", "rework"} or not note.strip():
            raise CollaborationConflict("验收必须提供 accept/rework 和具体理由")
        with _LOCK:
            task = self._load(task_id, expected_round)
            desired = "accepted" if verdict == "accept" else "rework_requested"
            if task.status != "done":
                raise CollaborationConflict("执行未成功提交结果，不能验收")
            self._check_consumed(task)
            if task.collaboration["review"] == desired:
                return task  # Replay of the same decision has no extra side effects.
            if task.collaboration["review"] != "pending":
                raise CollaborationConflict("本轮已经验收，请勿重复改变决策")
            task.collaboration["review"] = desired
            self._message(task, verdict, task.owner_session, note)
            return self._save(task)

    def ask_question(self, task_id: str, expected_round: int, question: str,
                     options: list[str] | None = None, context: str = "", *, tainted: bool = False) -> BackgroundTask:
        from src.gateway.task_questions import ask
        self._round(expected_round)
        with _LOCK:
            task = self._load(task_id, expected_round)
            self._check_consumed(task)
            item = ask(task, question, [] if options is None else options, context, tainted=tainted)
            self._message(task, "question", task.collaboration["assignee"], item["question"])
            return self._save(task)

    def answer_question(self, task_id: str, expected_round: int, question_id: str,
                        text: str) -> tuple[BackgroundTask, bool]:
        from src.gateway.task_questions import answer, validate_answer
        with _LOCK:
            task = self._load(task_id)
            _, replayed = validate_answer(task, expected_round, question_id, text)
            if replayed:
                return task, True
            from src.gateway.task_dependencies import has_dependencies, validate_consumed
            if has_dependencies(task):
                validate_consumed(task, self.ledger)
            from src.gateway.task_chain_budget import reserve
            reserve(task, self.ledger, task.collaboration["round"] + 1)
            replayed = answer(task, expected_round, question_id, text)
            if not replayed:
                self._message(task, "answer", task.owner_session, text)
                self._save(task)
            return task, replayed

    def abort(self, task_id: str, expected_round: int, *, error: str = "", cancelled: bool = False,
              timed_out: bool = False) -> BackgroundTask:
        """Terminate queued/running external work, including cancellation before its first timeslice."""
        self._round(expected_round)
        with _LOCK:
            task = self._load(task_id, expected_round)
            if task.status not in {"waiting", "queued", "running", "blocked"} and not (timed_out and task.status == "cancelled"):
                return task
            from src.gateway.task_questions import cancel_open_questions
            cancel_open_questions(task)
            if isinstance(task.dependencies, dict) and task.dependencies.get("resolution") == "invalidated":
                error, cancelled = task.dependencies["invalidation"]["reason"], False
            task.status = "cancelled" if cancelled else "failed"
            task.error = _text(error, 1000)
            task.collaboration["review"] = "not_submitted"
            self._message(task, task.status, task.owner_session, task.error or "任务已取消")
            return self._save(task)

    def _check_consumed(self, task: BackgroundTask) -> None:
        from src.gateway.task_dependencies import has_dependencies, invalidate, validate_consumed
        if has_dependencies(task):
            if invalidate(task, self.ledger):
                self._message(task, "dependencies_invalidated", task.owner_session, task.dependencies["invalidation"]["reason"])
                self._save(task)
            validate_consumed(task, self.ledger)

    def check_execution_dependencies(self, task_id: str, expected_round: int) -> None:
        """Injected request/tool checkpoint; no model or scheduler dependency."""
        with _LOCK:
            self._check_consumed(self._load(task_id, expected_round))

    def reconcile_dependencies(self, task_id: str, expected_round: int) -> tuple[BackgroundTask, bool]:
        from src.gateway.task_dependencies import invalidate
        self._round(expected_round)
        with _LOCK:
            task = self._load(task_id, expected_round)
            changed = invalidate(task, self.ledger)
            if changed:
                self._message(task, "dependencies_invalidated", task.owner_session, task.dependencies["invalidation"]["reason"])
                self._save(task)
            return task, changed

    def followup(self, task_id: str, expected_round: int, message: str) -> BackgroundTask:
        self._round(expected_round)
        if not message.strip():
            raise CollaborationConflict("补充要求不能为空")
        with _LOCK:
            task = self._load(task_id, expected_round)
            if task.status not in {"done", "failed", "interrupted", "cancelled"}:
                raise CollaborationConflict("任务仍在执行，不能启动后续轮次")
            if task.collaboration["review"] == "accepted":
                raise CollaborationConflict("任务已验收；新的工作请创建新任务")
            if expected_round >= _MAX_ROUNDS:
                raise CollaborationConflict("已达到 3 轮执行上限，请交由用户决策")
            from src.gateway.task_dependencies import has_dependencies, validate_consumed
            if has_dependencies(task):
                validate_consumed(task, self.ledger)
            from src.gateway.task_chain_budget import reserve
            reserve(task, self.ledger, expected_round + 1)
            task.collaboration["round"] += 1
            task.collaboration["review"] = "not_submitted"
            task.status, task.result, task.error = "queued", "", ""
            self._message(task, "followup", task.owner_session, message)
            return self._save(task)

    def release_dependencies(self, task_id: str, expected_round: int) -> tuple[BackgroundTask, bool]:
        """Persist a single explicit release; failed requirements never start work."""
        from src.gateway.task_dependencies import dependency_state, resolve
        self._round(expected_round)
        with _LOCK:
            task = self._load(task_id)
            state = dependency_state(task)
            if state["released_round"] == expected_round and state["resolution"] in {"consumed", "failed", "invalidated"}:
                return task, True
            if task.status != "waiting" or task.collaboration["round"] != expected_round:
                raise CollaborationConflict("任务不在等待依赖或轮次已变化")
            inputs, failure, pending = resolve(task, self.ledger)
            if pending and not failure:
                raise CollaborationConflict("前置任务尚未完成并验收；未推进")
            if not failure:
                from src.gateway.task_chain_budget import reserve
                reserve(task, self.ledger, expected_round)
            state.update(inputs=inputs if not failure else [], released_round=expected_round,
                         resolution="failed" if failure else "consumed")
            task.collaboration["tainted"] |= any(item["tainted"] for item in inputs)
            task.status = "failed" if failure else "queued"
            task.error = _text(failure, 1000)
            self._message(task, "dependency_failed" if failure else "dependencies_consumed", task.owner_session,
                          failure or "已固定并消费前置任务的验收结果版本")
            return self._save(task), False
