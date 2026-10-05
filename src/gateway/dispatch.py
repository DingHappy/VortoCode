"""Callable task dispatch service; HTTP and model execution are injected adapters.

Request identity is durable in the existing task record. One runtime owns the
ledger and the shared TaskRunner pool; this is not multi-tenant authentication.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Awaitable, Callable

from src.gateway.collaboration import CollaborationConflict, CollaborationService
from src.gateway.tasks import BackgroundTask, TaskRunner, TASK_STATE_LOCK
from src.utils.async_ops import run_with_timeout
from src.utils.ids import typed_id

_LOCK = TASK_STATE_LOCK
_IDENTIFIER = re.compile(r"[A-Za-z0-9._-]{1,120}")
_LOGGER = logging.getLogger(__name__)


class DispatchBusy(ValueError):
    pass


def session_identity(session: str) -> str:
    if not isinstance(session, str):
        raise ValueError("必须提供 session")
    value = session[4:] if session.startswith("sid-") else session
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("session 必须为 1 到 120 位字母、数字、点、下划线或连字符")
    return "sid-" + value


class DispatchService:
    def __init__(self, repo_root: str, runner: TaskRunner, *,
                 execute: Callable[[BackgroundTask, CollaborationService], Awaitable[None]],
                 validate_agent: Callable[[str], None],
                 on_update: Callable[[BackgroundTask], None] | None = None):
        if Path(repo_root).resolve() != Path(runner.ledger.repo_root).resolve():
            raise ValueError("下派服务与后台台账必须属于同一工作区")
        self.repo_root = repo_root
        self.runner = runner
        self.execute = execute
        self.validate_agent = validate_agent
        self.on_update = on_update

    def collaboration(self, session: str) -> CollaborationService:
        return CollaborationService(self.repo_root, session_identity(session), on_update=self.notify_update)

    def notify_update(self, task: BackgroundTask) -> None:
        """Saved collaboration events also coordinate descendants without a client."""
        try:
            if self.on_update is not None:
                self.on_update(task)
        except Exception:  # noqa: BLE001 - transport cannot prevent domain coordination.
            _LOGGER.warning("Task update transport failed: %s", task.id, exc_info=True)
        self.observe_dependency_change(task.id)

    def observe_dependency_change(self, task_id: str) -> dict | None:
        from src.gateway.task_dependents import coordinate
        if not typed_id(task_id, "task"):
            raise CollaborationConflict("无效前置任务 ID")
        return coordinate(self, task_id)

    def audit_dependencies(self, session: str | None = None) -> dict | None:
        """One bounded audit at startup/an existing user turn; never admits work."""
        from src.gateway.task_dependents import coordinate
        return coordinate(self, owner=session_identity(session) if session is not None else None)

    def _dependency_source(self, session: str, task_id: str, round_number: int):
        task = self.get(session, task_id)
        if (type(round_number) is not int or type(task.collaboration.get("round")) is not int
                or not 1 <= round_number <= 3 or round_number != task.collaboration.get("round")):
            raise CollaborationConflict("前置任务轮次已变化，请重新读取")
        return task

    def dependents(self, session: str, task_id: str, round_number: int) -> dict:
        from src.gateway.task_dependents import discover
        with _LOCK:
            task = self._dependency_source(session, task_id, round_number)
            scan = discover(self.runner.ledger, task.id, task.owner_session)
            return {**scan.summary(), "source_task_id": task.id, "source_round": round_number,
                    "tasks": [{"task_id": item.id, "round": item.collaboration.get("round"),
                               "status": item.status, "resolution": item.dependencies.get("resolution")
                               if isinstance(item.dependencies, dict) else None} for item in scan.tasks]}

    def reconcile_dependents(self, session: str, task_id: str, round_number: int) -> dict:
        from src.gateway.task_dependents import discover, reconcile
        with _LOCK:
            task = self._dependency_source(session, task_id, round_number)
            report = reconcile(self, discover(self.runner.ledger, task.id, task.owner_session))
            return {**report, "source_task_id": task.id, "source_round": round_number}

    def dependency_scan(self, session: str, task_id: str, round_number: int, request_id: str, **options) -> dict:
        from src.gateway.task_dependents import coverage_page
        return coverage_page(self, session, task_id, round_number, request_id, **options)

    def dependency_scan_status(self, session: str, task_id: str, round_number: int, scan_id=None):
        from src.gateway.task_dependents import coverage_status
        return coverage_status(self, session, task_id, round_number, scan_id)

    def coverage_records(self, session: str, task_id: str, *, offset=0, retirement_offset=0):
        from src.gateway.task_scan import records
        return records(self, session, task_id, offset=offset, retirement_offset=retirement_offset)

    def observe_coverage(self, session: str, task_id: str, **contract):
        from src.gateway.task_scan import observe
        return observe(self, session, task_id, **contract)

    def archive_coverage(self, session: str, task_id: str, round_number: int, scan_id: str, **options):
        from src.gateway.task_scan import retain
        return retain(self, session, task_id, round_number, scan_id, **options)

    def release_coverage(self, session: str, task_id: str, round_number: int, scan_id: str, **options):
        from src.gateway.task_scan import release
        return release(self, session, task_id, round_number, scan_id, **options)

    def coverage_retirement(self, session: str, task_id: str, round_number: int, scan_id: str):
        from src.gateway.task_scan import read_retirement
        return read_retirement(self, session, task_id, round_number, scan_id)

    def export_coverage(self, session: str, task_id: str, round_number: int, archive_id: str, artifact=None):
        from src.gateway.task_scan import export
        return export(self, session, task_id, round_number, archive_id, artifact)

    def verify_coverage_file(self, session: str, task_id: str, round_number: int, archive_id: str, **contract):
        from src.gateway.task_scan import verify_scoped_file
        return verify_scoped_file(self, session, task_id, round_number, archive_id, **contract)

    def verify_coverage_preservation(self, session: str, task_id: str, round_number: int, archive_id: str, **contract):
        from src.gateway.task_scan import verify_scoped_preservation
        return verify_scoped_preservation(self, session, task_id, round_number, archive_id, **contract)

    def get(self, session: str, task_id: str) -> BackgroundTask:
        task = self.collaboration(session).get(task_id)
        if task.collaboration.get("dispatch", {}).get("source") != "api":
            raise CollaborationConflict("该任务不属于下派服务入口")
        return task

    def list(self, session: str) -> list[BackgroundTask]:
        return [task for task in self.collaboration(session).list()
                if task.collaboration.get("dispatch", {}).get("source") == "api"]

    def submit(self, *, session: str, request_id: str, prompt: str, agent: str = "",
               acceptance: list[str] | None = None, max_steps: int = 12,
               timeout_seconds: int = 300, depends_on: list[dict] | None = None,
               chain_limits: dict | None = None) -> tuple[BackgroundTask, bool]:
        from src.gateway.task_dependencies import normalize_requirements, validate_graph
        owner = session_identity(session)
        if not isinstance(request_id, str) or not _IDENTIFIER.fullmatch(request_id):
            raise ValueError("必须提供 1 到 120 位的 request_id（字母、数字、点、下划线或连字符）")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            raise ValueError("prompt 必须为 1 到 4000 字符的非空文本")
        if not isinstance(agent, str) or len(agent) > 120:
            raise ValueError("agent 必须为不超过 120 字符的角色名")
        if type(max_steps) is not int or not 1 <= max_steps <= 12:
            raise ValueError("max_steps 必须为 1 到 12 的整数")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600:
            raise ValueError("timeout_seconds 必须为 1 到 600 的整数")
        acceptance = [] if acceptance is None else acceptance
        if (not isinstance(acceptance, list) or len(acceptance) > 20
                or any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in acceptance)):
            raise ValueError("acceptance 必须为最多 20 项、每项 1 到 500 字符的非空文本列表")
        contract = {"prompt": prompt.strip(), "agent": agent.strip(), "acceptance": acceptance,
                    "max_steps": max_steps, "timeout_seconds": timeout_seconds}
        requirements = normalize_requirements([] if depends_on is None else depends_on)
        if requirements:
            contract["depends_on"] = requirements
        if chain_limits is not None:
            from src.gateway.task_chain_budget import normalize_limits
            contract["chain_limits"] = normalize_limits(chain_limits)
        digest = hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        key = hashlib.sha256(json.dumps([owner, request_id]).encode()).hexdigest()[:32]
        task_id = "task-dispatch-" + key
        service = self.collaboration(owner)
        with _LOCK:
            existing = service.ledger.load(task_id)
            if existing is not None:
                task = self.get(owner, task_id)
                if task.collaboration["dispatch"].get("fingerprint") != digest:
                    raise CollaborationConflict("request_id 已用于不同的任务合同")
                # A replay never schedules again, including after a process crash.
                return task, True
            if service.ledger.has_record(task_id):
                raise CollaborationConflict("请求对应的持久记录损坏，拒绝重新创建")
            self.validate_agent(contract["agent"])
            if self.runner.active_count >= 100:
                raise DispatchBusy("后台任务队列已满，请稍后重试")
            if requirements:
                validate_graph(service.ledger, owner, task_id, requirements)
                if sum(t.status == "waiting" for t in service.ledger.list()) >= 100:
                    raise DispatchBusy("等待依赖的任务已满")
            dependencies = ({"version": 1, "task_id": task_id, "owner_session": owner,
                             "requires": requirements, "inputs": [], "resolution": "waiting", "released_round": 0}
                            if requirements else None)
            task = service.create(contract["prompt"], contract["agent"], acceptance, task_id=task_id,
                                  dispatch={"source": "api", "fingerprint": digest,
                                            "max_steps": max_steps, "timeout_seconds": timeout_seconds,
                                            **({"depends_on": requirements} if requirements else {})},
                                  dependencies=dependencies, chain_limits=contract.get("chain_limits"))
            if not requirements:
                self._schedule(task, service)
            return task, False

    def _schedule(self, task: BackgroundTask, service: CollaborationService) -> None:
        round_number = task.collaboration["round"]

        async def execute():
            from src.gateway.task_dependencies import has_dependencies, validate_consumed
            current = service.get(task.id)
            if has_dependencies(current):
                validate_consumed(current, service.ledger)
            from src.gateway.task_chain_budget import validate_reserved
            validate_reserved(current, service.ledger)
            self.validate_agent(current.collaboration["assignee"])
            timeout = current.collaboration["dispatch"]["timeout_seconds"]
            try:
                await run_with_timeout(self.execute(current, service), timeout)
            finally:
                # Release the slot only after the injected executor has unwound.
                latest = service.get(task.id)
                if isinstance(latest.dependencies, dict) and latest.dependencies.get("resolution") == "invalidated":
                    service.abort(task.id, round_number, error=latest.dependencies["invalidation"]["reason"])

        self.runner.enqueue_existing(
            task.id, execute,
            on_cancel=lambda: service.abort(task.id, round_number, cancelled=True),
            on_failure=lambda error: service.abort(
                task.id, round_number, timed_out=isinstance(error, TimeoutError),
                error="下派任务超过执行时限" if isinstance(error, TimeoutError)
                else f"下派执行失败：{type(error).__name__}: {error}"),
        )

    def review(self, session: str, task_id: str, round_number: int, verdict: str, note: str) -> BackgroundTask:
        self.get(session, task_id)
        return self.collaboration(session).review(task_id, round_number, verdict, note)

    def reconcile_dependencies(self, session: str, task_id: str, round_number: int) -> tuple[BackgroundTask, bool, bool]:
        """Persist invalidation before requesting cancellation; never starts work."""
        with _LOCK:
            self.get(session, task_id)
            service = self.collaboration(session)
            task, changed = service.reconcile_dependencies(task_id, round_number)
            stopped = False
            if task.dependencies["resolution"] == "invalidated":
                stopped = self.runner.cancel(task.id)
                if not stopped:
                    task = service.abort(task.id, round_number, error=task.dependencies["invalidation"]["reason"])
            return task, changed, stopped

    def followup(self, session: str, task_id: str, round_number: int, message: str) -> BackgroundTask:
        with _LOCK:
            previous = self.get(session, task_id)
            self.validate_agent(previous.collaboration["assignee"])
            if self.runner.is_active(task_id):
                raise CollaborationConflict("任务仍在执行，请稍后重新读取")
            if self.runner.active_count >= 100:
                raise DispatchBusy("后台任务队列已满，请稍后重试")
            service = self.collaboration(session)
            task = service.followup(task_id, round_number, message)
            self._schedule(task, service)
            return task

    async def cancel(self, session: str, task_id: str, round_number: int) -> BackgroundTask:
        service = self.collaboration(session)
        task = self.get(session, task_id)
        if type(round_number) is not int or round_number != task.collaboration["round"]:
            raise CollaborationConflict("任务轮次已变化，请重新读取")
        if task.status not in {"waiting", "queued", "running", "blocked", "cancelled"}:
            raise CollaborationConflict("任务已结束，不能取消")
        if self.runner.cancel(task_id):
            await self.runner.join(task_id)
        return service.abort(task_id, round_number, cancelled=True)

    def release(self, session: str, task_id: str, round_number: int) -> tuple[BackgroundTask, bool]:
        from src.gateway.task_dependencies import dependency_state
        with _LOCK:
            previous = self.get(session, task_id)
            state = dependency_state(previous)
            if type(round_number) is not int or not 1 <= round_number <= 3:
                raise CollaborationConflict("必须提供精确任务轮次")
            if state["released_round"] == round_number and state["resolution"] in {"consumed", "failed", "invalidated"}:
                return previous, True
            self.validate_agent(previous.collaboration["assignee"])
            if self.runner.is_active(task_id):
                raise CollaborationConflict("该任务仍在执行")
            if self.runner.active_count >= 100:
                raise DispatchBusy("后台任务队列已满")
            service = self.collaboration(session)
            task, replayed = service.release_dependencies(task_id, round_number)
            if task.status == "queued" and not replayed:
                self._schedule(task, service)
            return task, replayed

    def answer_question(self, session: str, task_id: str, round_number: int, question_id: str,
                        answer: str) -> tuple[BackgroundTask, bool]:
        from src.gateway.task_questions import validate_answer
        with _LOCK, TASK_STATE_LOCK:
            previous = self.get(session, task_id)
            _, replayed = validate_answer(previous, round_number, question_id, answer)
            if replayed:
                return previous, True
            self.validate_agent(previous.collaboration["assignee"])
            if self.runner.is_active(task_id):
                raise CollaborationConflict("任务正在结束提问前的执行，请稍后重试")
            if self.runner.active_count >= 100:
                raise DispatchBusy("后台任务队列已满，请稍后重试")
            service = self.collaboration(session)
            task, replayed = service.answer_question(task_id, round_number, question_id, answer)
            if not replayed:
                self._schedule(task, service)
            return task, replayed
