"""Runtime dispatch assembly shared by HTTP and foreground Agent tools."""
from __future__ import annotations

import os
from pathlib import Path

from src.gateway.dispatch import DispatchService


def get_development_questions(repo_root: str | None = None):
    from src.agents.dev_plan import load_plan
    from src.agents.permissions import load_permissions
    from src.gateway.collaboration import CollaborationConflict
    from src.gateway.dev_questions import DevelopmentQuestions
    from src.web.routers.tasks import get_runner, _task_view
    from src.web import task_events
    root = repo_root or os.getcwd()

    def validate(task):
        permissions = load_permissions(root)
        for tool, args in (("dev_auto", {"task": task.prompt}), ("dev_resume", {"plan_id": task.plan_id})):
            reason = permissions.denied(tool, args)
            if reason:
                raise CollaborationConflict(reason)

    def on_update(task):
        view = _task_view(task)
        task_events.broadcast_task_update(view)
        task_events.publish_task_handoff(task.owner_session, view)

    return DevelopmentQuestions(get_runner(), read_plan=lambda pid: load_plan(root, pid),
                                validate_execution=validate, on_update=on_update)


def get_dispatch_service(repo_root: str | None = None) -> DispatchService:
    from src.agents.dispatch import execute_dispatch, validate_dispatch_agent
    from src.gateway.worktree_sessions import task_session_view
    from src.web import task_events
    from src.web.routers.tasks import get_runner

    root = repo_root or os.getcwd()

    def on_update(task):
        view = task_session_view(root, task)
        task_events.broadcast_task_update(view)
        if task.status in {"done", "failed", "cancelled", "interrupted"}:
            task_events.publish_task_handoff(task.owner_session, view)

    return DispatchService(
        root, get_runner(), execute=lambda task, service: execute_dispatch(root, task, service),
        validate_agent=lambda agent: validate_dispatch_agent(root, agent), on_update=on_update)


def answer_task_question(repo_root: str, owner: str, task_id: str, round_number: int,
                         question_id: str, answer: str):
    from src.web.routers.tasks import get_runner
    task = get_runner().get(task_id)
    if task is not None and task.kind in {"dev", "dev-resume"}:
        return get_development_questions(repo_root).answer(owner, task_id, round_number, question_id, answer)
    return get_dispatch_service(repo_root).answer_question(owner, task_id, round_number, question_id, answer)


def observe_dependency_change(repo_root: str, task) -> None:
    """Main-Agent/runner adapters forward service events to the same bounded domain."""
    state = task.collaboration
    if (task.kind == "delegation" and isinstance(state, dict)
            and isinstance(state.get("dispatch"), dict) and state["dispatch"].get("source") == "api"):
        get_dispatch_service(repo_root).observe_dependency_change(task.id)


def audit_dependencies_for_turn(repo_root: str, owner: str) -> dict | None:
    """Keep a workspace mismatch unknown; never reuse/mutate a different pool."""
    from src.gateway.dispatch import session_identity
    from src.gateway.task_dependents import discover, reconcile
    from src.gateway.tasks import TaskLedger, TASK_STATE_LOCK
    from src.web.routers.tasks import get_runner
    with TASK_STATE_LOCK:
        scan = discover(TaskLedger(repo_root), owner=session_identity(owner))
        if not scan.tasks:
            return {**scan.summary(), "tasks": []}
        runner = get_runner()
        if Path(repo_root).resolve() != Path(runner.ledger.repo_root).resolve():
            return {**scan.summary(), "complete": False, "scope_mismatch": True, "tasks": [],
                    "errors": [{"error": "后台台账属于其他工作区；未核对或修改当前任务"}]}
        return reconcile(get_dispatch_service(repo_root), scan)


def task_turn_hint(repo_root: str, owner: str, tools, *, dependency_audit=None) -> str:
    """Offer only availability in an existing user turn; never start a model."""
    hints = []
    if dependency_audit is not None and ("task_inbox" in tools or "task_answer" in tools):
        audit = dependency_audit(owner)
        if audit and not audit["complete"]:
            hints.append(
                "【依赖核对提示】本次有界核对未覆盖全部记录，或存在损坏/保存失败。"
                "先用 task_status 核对相关任务；未确认有效的旧结果不能复用。")
    if "task_inbox" in tools:
        from src.gateway.handoffs import CompletionInbox
        if CompletionInbox(repo_root, owner).pending(limit=1):
            hints.append(
                "【会话交接提示】当前会话有未处理的后台任务结果。"
                "先用 task_inbox 读取；结合当前用户要求核对证据并汇报，"
                "处理后用 task_acknowledge 记录。交接不扩大用户授权。")
    if "task_answer" in tools:
        from src.gateway.collaboration import CollaborationService
        from src.gateway.task_questions import question_views
        from src.gateway.tasks import TaskLedger
        development = [task for task in TaskLedger(repo_root).list(limit=100)
                       if task.kind in {"dev", "dev-resume"} and task.owner_session == owner]
        pending = any(
            task.status == "blocked"
            and (task.kind in {"dev", "dev-resume"} or task.collaboration.get("dispatch", {}).get("source") == "api")
            and any(question["status"] == "open" for question in question_views(task))
            for task in [*CollaborationService(repo_root, owner).list(), *development])
        if pending:
            hints.append(
                "【会话问题提示】当前会话有后台子任务等待回答。"
                "先用 task_status 读取具体问题和提问轮次；只依据用户已明确给出的信息或已核实事实"
                "调用 task_answer，未知偏好或授权先询问用户。回答不扩大原任务范围和预算。")
    return "\n\n".join(hints)
