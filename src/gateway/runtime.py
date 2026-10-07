"""Transport-independent Runtime lifetime. One workspace, one task ledger owner."""
import os
from pathlib import Path
from contextlib import AsyncExitStack, asynccontextmanager
from src.gateway.process_lease import ProcessLease


class RuntimeServices:
    """Own the single execution pool and dispatch, independent of a transport."""
    def __init__(self, root, *, development_worker, on_update=None, workspace_scope=None):
        from src.gateway.tasks import TaskRunner
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


@asynccontextmanager
async def runtime_lifespan(root, *, runner, scheduler, audit_dependencies,
                           make_run_manager, make_terminal_manager, register_im_worker):
    """Recover before serving; startup failures release all owned resources."""
    lease = ProcessLease(Path(root) / ".vortocode" / "runtime.lock")
    import asyncio
    owned = False
    sched_task = None
    watchdog_task = None
    stop_event = asyncio.Event()
    im_task = None
    im_adapter = None
    run_manager = None
    terminal_manager = None
    try:
        ledger = getattr(runner, "ledger", None)
        if ledger is not None and Path(ledger.repo_root).resolve() != Path(root).resolve():
            raise RuntimeError("Runtime runner belongs to a different workspace")
        lease.acquire()
        owned = True
        from src.utils.state_dir import ensure_state_gitignore
        ensure_state_gitignore(str(root))
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
               for k in ("VORTOCODE_CRON", "VORTOCODE_HEARTBEAT")):
            from src.gateway.workspace_scope import current_workspace_scope
            if current_workspace_scope() != "project":
                raise RuntimeError("Autonomous scheduling requires a Project workspace")
            sched_task = asyncio.create_task(scheduler(stop_event))
            print("  ⏰ cron/heartbeat 调度循环已启动（opt-in）")
        run_manager = make_run_manager()
        recovered_runs = run_manager.recover()
        if recovered_runs:
            print(f"  ↻ 恢复 {len(recovered_runs)} 个中断的 Desktop 运行记录")
        terminal_manager = make_terminal_manager()
        im_channel = os.getenv("VORTOCODE_IM", "").strip().lower()
        if im_channel:
            from src.gateway import im_service
            bridge, im_adapter = im_service.start_embedded(
                im_channel, root, runner=runner, register_worker=register_im_worker)
            im_task = asyncio.create_task(bridge.run())
        yield
    finally:
        # Every cleanup runs even if another manager raises during shutdown.
        async with AsyncExitStack() as cleanup:
            cleanup.callback(lease.release)
            if terminal_manager is not None:
                cleanup.callback(terminal_manager.shutdown)
            if run_manager is not None:
                cleanup.push_async_callback(run_manager.shutdown)
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
