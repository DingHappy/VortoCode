"""任务上下文的调度（ADR-014 S1c）：成员交回结果就叫醒协调人，同一任务同一时间只跑一个协调回合。

- 成员子任务进运行时的共享后台池（``TaskRunner.enqueue_existing``），并发受池上限与根的 ``parallel`` 双重约束。
- ``wake``：有新情况（任务开始、成员交回 / 失败、主人留言）才叫醒协调人；回合进行中又来的新情况攒着，
  回合结束后再叫醒一次。没有定时空转。
- ``kick``：只把能开始的成员子任务放进池子，不叫醒协调人（派活工具用它）。
- ``recover``：启动时按 ``TeamService.recover_after_restart`` 处理被打断的协调回合；被打断的成员子任务由启动
  扫描标为 interrupted，协调人醒来时会看到。
- 协调回合的唤醒额度用完、无进展判定、验收证据都在 ``TeamService`` 里，本模块不重复判断。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_RUNNER: Optional["TeamRunner"] = None


def set_team_runner(runner: Optional["TeamRunner"]) -> None:
    global _RUNNER
    _RUNNER = runner


def get_team_runner() -> Optional["TeamRunner"]:
    return _RUNNER


def describe(child: Any) -> str:
    member = child.collaboration.get("assignee") or "成员"
    number = child.collaboration.get("round")
    if child.status == "done":
        return f"成员「{member}」交回了 {child.id}（第 {number} 轮），用 team_result 查看"
    if child.status == "cancelled":
        return f"成员「{member}」的 {child.id} 已取消"
    return f"成员「{member}」的 {child.id} 第 {number} 轮{'失败' if child.status == 'failed' else '中断'}：{child.error[:120]}"


class TeamRunner:
    def __init__(self, repo_root: str, runner: Any, *,
                 member: Optional[Callable[..., Awaitable[Any]]] = None,
                 coordinator: Optional[Callable[..., Awaitable[Optional[str]]]] = None,
                 on_update: Optional[Callable[[Any], None]] = None):
        from src.agents.team_coordinator import coordinator_turn
        from src.agents.team_members import run_member
        from src.workflow.tasks.team import TeamService
        self.repo_root = str(repo_root)
        self.runner = runner
        self.service = TeamService(self.repo_root, on_update=on_update)
        self._member = member or run_member
        self._coordinator = coordinator or coordinator_turn
        self._pending: Dict[str, List[str]] = {}
        self._driving: Dict[str, asyncio.Task] = {}

    # ── 成员 ──────────────────────────────────────────────────────────
    def kick(self, root_id: str) -> None:
        """把能开始的成员子任务放进后台池。"""
        try:
            root = self.service.get(root_id)
            children = self.service.runnable(root)
        except Exception as error:  # noqa: BLE001
            logger.warning("任务上下文 %s 读不到：%s", root_id, type(error).__name__)
            return
        for child in children:
            if self.runner.is_active(child.id):
                continue
            self.runner.enqueue_existing(child.id, self._member_job(root_id, child.id),
                                         on_cancel=self._member_stopped(root_id, child.id, cancelled=True),
                                         on_failure=lambda error, cid=child.id: self._member_stopped(
                                             root_id, cid, error=f"{type(error).__name__}: {error}")())

    def _member_job(self, root_id: str, child_id: str) -> Callable[[], Awaitable[None]]:
        async def job() -> None:
            child = await self._member(self.repo_root, root_id, child_id)
            self.wake(root_id, describe(child))
        return job

    def _member_stopped(self, root_id: str, child_id: str, *, cancelled: bool = False,
                        error: str = "") -> Callable[[], None]:
        def stopped() -> None:
            try:
                root = self.service.get(root_id)
                work = self.service.collaboration(root)
                child = work.get(child_id)
                if child.status in {"queued", "running"}:
                    child = work.abort(child_id, child.collaboration["round"], cancelled=cancelled,
                                       error=error[:300] or ("" if cancelled else "成员执行异常"))
                self.wake(root_id, describe(child))
            except Exception as failure:  # noqa: BLE001
                logger.warning("成员子任务 %s 收尾失败：%s", child_id, type(failure).__name__)
        return stopped

    # ── 协调人 ────────────────────────────────────────────────────────
    def wake(self, root_id: str, event: str) -> None:
        self._pending.setdefault(root_id, []).append(event)
        task = self._driving.get(root_id)
        if task is None or task.done():
            self._driving[root_id] = asyncio.get_running_loop().create_task(self._drive(root_id))

    async def _drive(self, root_id: str) -> None:
        from src.workflow.tasks.errors import CollaborationConflict
        while root_id in self._pending:
            events = self._pending.pop(root_id)
            self.kick(root_id)
            try:
                root = self.service.get(root_id)
            except CollaborationConflict:
                return
            if root.status != "active":
                return                       # 等你 / 待验收 / 已结束：主人的动作会再叫醒
            try:
                await self._coordinator(self.repo_root, root_id, events, kick=self.kick)
            except CollaborationConflict as error:
                logger.info("任务上下文 %s 协调人未醒来：%s", root_id, error)
                return
            except Exception as error:  # noqa: BLE001 —— 一个任务出错不拖垮其它任务
                logger.warning("任务上下文 %s 协调回合异常：%s", root_id, type(error).__name__)
                return
            self.kick(root_id)

    # ── 启动与停止 ─────────────────────────────────────────────────────
    async def recover(self) -> None:
        from src.workflow.tasks.team import LIVE
        for root in self.service.list():
            if root.status not in LIVE:
                continue
            outcome = self.service.recover_after_restart(root.id)
            if outcome == "rerun":
                self.wake(root.id, "上次协调回合被重启打断。先用 team_status 核对已经派出和交回的情况，再继续")
                continue
            if outcome == "failed" or root.status != "active":
                continue
            cut = [child for child in self.service.children(root) if child.status == "interrupted"]
            if cut:
                self.wake(root.id, "；".join(describe(child) for child in cut))
            else:
                self.kick(root.id)

    def stop(self, root_id: str, reason: str = "主人停止") -> Any:
        root, pending = self.service.stop(root_id, reason)
        for child_id in pending:
            self.runner.cancel(child_id)
        driving = self._driving.pop(root_id, None)
        self._pending.pop(root_id, None)
        if driving is not None and not driving.done():
            driving.cancel()
        return root

    async def close(self) -> None:
        for task in list(self._driving.values()):
            if not task.done():
                task.cancel()
        self._driving.clear()
