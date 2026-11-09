"""Transport-independent Runtime lifetime. One workspace, one task ledger owner."""
import os
from pathlib import Path
from contextlib import AsyncExitStack, asynccontextmanager
from src.gateway.process_lease import ProcessLease
from src.utils.state_dir import state_dir


class RuntimeServices:
    """Own the single execution pool and dispatch, independent of a transport."""
    def __init__(self, root, *, development_worker, on_update=None, workspace_scope=None):
        from src.workflow.tasks.tasks import TaskRunner
        from src.gateway.workspace_scope import current_workspace_scope, normalize_workspace_scope
        self.root = str(Path(root).resolve())
        self.scope = (current_workspace_scope() if workspace_scope is None else
                      normalize_workspace_scope(workspace_scope, default="general"))
        self.development_worker = development_worker
        self.im_worker = None
        self.runner = TaskRunner(self.root, self.execute, on_update=on_update)

    def register_im_worker(self, worker):
        self.im_worker = worker

    async def execute(self, task, on_progress):
        if self.scope != "project":
            raise RuntimeError("Development tasks require a Project workspace")
        if task.kind == "im-dev":
            if self.im_worker is None:
                raise RuntimeError("IM task worker is unavailable; task was not executed")
            return await self.im_worker(task, on_progress)
        if task.kind not in {"dev", "dev-resume"}:
            raise RuntimeError("Unsupported background task kind")
        return await self.development_worker(task, on_progress)


def scheduling_scope_error() -> str:
    """当前范围能不能开调度；能 → 空串，不能 → 拒绝原因（启动即失败，别静默不跑）。

    通用范围（常驻助理）只跑你交代的定时事项：无人值守会话按通用范围装配（无文件/命令），
    command 作业在执行时拒绝。Heartbeat 会领 BACKLOG 派开发任务，仍只属于项目范围。
    """
    from src.gateway.workspace_scope import GENERAL, PROJECT, current_workspace_scope
    scope = current_workspace_scope()
    heartbeat_on = os.getenv("VORTOONE_HEARTBEAT", "").strip().lower() in ("1", "true", "yes", "on")
    if scope == PROJECT or (scope == GENERAL and not heartbeat_on):
        return ""
    return "Autonomous scheduling requires a Project workspace" + (
        "（Heartbeat 只能在项目范围开）" if scope == GENERAL else "")


@asynccontextmanager
async def runtime_lifespan(root, *, runner, scheduler, audit_dependencies,
                           make_run_manager, make_terminal_manager, register_im_worker, session_actors=None):
    """Recover before serving; startup failures release all owned resources."""
    lease = ProcessLease(state_dir(root) / "runtime.lock")
    import asyncio
    owned = False
    sched_task = None
    watchdog_task = None
    stop_event = asyncio.Event()
    im_task = None
    im_adapter = None
    im_host = None
    run_manager = None
    terminal_manager = None
    receipt_started = False
    execution_identity = None
    try:
        ledger = getattr(runner, "ledger", None)
        if ledger is not None and Path(ledger.repo_root).resolve() != Path(root).resolve():
            raise RuntimeError("Runtime runner belongs to a different workspace")
        lease.acquire()
        # 退出时要调用的清理函数现在就载入，别等到退出那一刻：App 更新会先替换磁盘上的打包文件、再让旧进程退出，
        # 那时才 import 会读到新包、解压失败（zlib.error），之后登记的清理全部跳过（真机引擎日志连续 10 次）。
        from src.agents.tools.website_browser import shutdown_browsers
        from src.agents.tools.android import shutdown_android_sessions
        from src.gateway.configuration_receipt import end_configuration_receipt
        from src.gateway.execution_identity import end_execution_identity
        owned = True   # 放在载入之后：载入失败就不去调没载入的清理函数，租约照样释放
        from src.utils.state_dir import ensure_state_gitignore
        ensure_state_gitignore(str(root))
        from src.gateway.account_registration import AccountRegistration
        instance_id = AccountRegistration(str(root)).ensure_instance()
        # 监护进程看门狗：Desktop 被强杀/崩溃时不留下孤儿 runtime（只在申报了监护 pid 时启动）。
        from src.gateway.supervisor_watchdog import start_watchdog
        watchdog_task = start_watchdog()
        if watchdog_task is not None:
            print("  🐕 看门狗已启动：监护进程退出后 runtime 自动退出")
        recovered = runner.recover()
        if recovered:
            print(f"  ↻ 对账 {len(recovered)} 条后台任务/检查中断记录（不自动重试）")
        dependency_audit = audit_dependencies()
        if dependency_audit and not dependency_audit["complete"]:
            print("  （依赖启动核对未覆盖全部记录，请核对扫描上限或损坏/存储错误；不自动执行）")
        # cron / heartbeat 调度循环：**opt-in**（任一开关开才起，默认全关——不擅自跑自主 LLM 作业）
        if any(os.getenv(k, "").strip().lower() in ("1", "true", "yes", "on")
               for k in ("VORTOONE_CRON", "VORTOONE_HEARTBEAT")):
            refusal = scheduling_scope_error()
            if refusal:
                raise RuntimeError(refusal)
            sched_task = asyncio.create_task(scheduler(stop_event))
            print("  ⏰ cron/heartbeat 调度循环已启动（opt-in）")
        run_manager = make_run_manager()
        recovered_runs = run_manager.recover()
        if recovered_runs:
            print(f"  ↻ 恢复 {len(recovered_runs)} 个中断的 Desktop 运行记录")
        terminal_manager = make_terminal_manager()
        im_channel = os.getenv("VORTOONE_IM", "").strip().lower()
        if os.getenv("VORTOONE_IM_BINDINGS_FILE", "").strip():
            from src.gateway.im_host import start_host
            im_host = await start_host(root, runner=runner, register_worker=register_im_worker, actors=session_actors)
        elif im_channel:
            from src.gateway import im_service
            bridge, im_adapter = im_service.start_embedded(
                im_channel, root, runner=runner, register_worker=register_im_worker)
            im_task = asyncio.create_task(bridge.run())
        from src.gateway.configuration_receipt import begin_configuration_receipt
        begin_configuration_receipt()
        receipt_started = True
        from src.gateway.execution_identity import begin_execution_identity
        execution_identity = begin_execution_identity(root, instance_id)
        yield
    finally:
        # 这里不再 import 任何新模块（见上面 lease.acquire 之后的说明）；owned 为真时清理函数都已载入
        if execution_identity is not None:
            end_execution_identity(root, execution_identity)
        if receipt_started:
            end_configuration_receipt()
        # Every cleanup runs even if another manager raises during shutdown.
        async with AsyncExitStack() as cleanup:
            cleanup.callback(lease.release)
            if owned:
                cleanup.push_async_callback(shutdown_browsers)
                cleanup.push_async_callback(shutdown_android_sessions)
            if terminal_manager is not None:
                cleanup.callback(terminal_manager.shutdown)
            if run_manager is not None:
                cleanup.push_async_callback(run_manager.shutdown)
            if im_host is not None:
                cleanup.push_async_callback(im_host.close)
            if im_adapter is not None:
                cleanup.push_async_callback(im_adapter.close)
            if im_task is not None:
                from src.gateway import im_service
                cleanup.callback(im_service.stop_embedded)
            shutdown = getattr(runner, "shutdown", None)
            if owned and shutdown is not None:
                cleanup.push_async_callback(shutdown)
            stop_event.set()
            pending = [task for task in (watchdog_task, sched_task, im_task) if task is not None]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
