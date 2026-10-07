"""HTTP/Desktop binding for the shared Runtime lifecycle."""
import os
from contextlib import asynccontextmanager


_SERVICES = None
_HANDOFF_STATUSES = {"done", "failed", "cancelled", "paused", "interrupted"}


def make_development_worker(root):
    from src.agents.main_agent import build_dev_tools
    from src.agents.dev_plan import load_plan
    from src.gateway.development_worker import execute_development
    from src.web.task_dispatch import get_development_questions

    async def worker(task, on_progress):
        return await execute_development(
            root, task, on_progress, build_tools=build_dev_tools, load_plan=load_plan,
            questions_factory=lambda: get_development_questions(root))
    return worker


def get_services():
    """Bind one Runtime to the launcher workspace; never redirect its ledger via cwd."""
    global _SERVICES
    if _SERVICES is None:
        from src.gateway.runtime import RuntimeServices
        root = os.getcwd()

        def on_update(task):
            from src.gateway.goals import sync_goal_from_task
            from src.gateway.worktree_sessions import task_session_view
            from src.web import task_events
            from src.web.task_dispatch import observe_dependency_change
            try:
                sync_goal_from_task(root, task)
            except Exception:  # Goal projection cannot fail the executing task.
                pass
            view = task_session_view(root, task)
            task_events.broadcast_task_update(view)
            if task.owner_session and task.status in _HANDOFF_STATUSES:
                task_events.publish_task_handoff(task.owner_session, view)
            observe_dependency_change(root, task)

        _SERVICES = RuntimeServices(root, development_worker=make_development_worker(root),
                                    on_update=on_update)
    return _SERVICES


@asynccontextmanager
async def runtime_lifespan():
    from src.gateway.runtime import runtime_lifespan as host
    from src.web.routers.tasks import get_runner, scheduler_loop, register_im_worker
    from src.web.task_dispatch import get_dispatch_service
    from src.web.routers.runs import get_run_manager
    from src.web.routers.terminals import get_terminal_manager
    async with host(os.getcwd(), runner=get_runner(), scheduler=scheduler_loop,
                    audit_dependencies=lambda: get_dispatch_service().audit_dependencies(),
                    make_run_manager=get_run_manager, make_terminal_manager=get_terminal_manager,
                    register_im_worker=register_im_worker):
        yield
