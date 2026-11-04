"""任务上下文的调度（ADR-014 S1c）：成员交回结果就叫醒协调人，同一任务同一时间只跑一个协调回合。

- 成员子任务进运行时的共享后台池（``TaskRunner.enqueue_existing``），并发受池上限与根的 ``parallel`` 双重约束。
- ``wake``：有新情况（任务开始、成员交回 / 失败、主人留言）才叫醒协调人；回合进行中又来的新情况攒着，
  回合结束后再叫醒一次。没有定时空转。
- ``kick``：只把能开始的成员子任务放进池子，不叫醒协调人（派活工具用它）。
- ``recover``：启动时按 ``TeamService.recover_after_restart`` 处理被打断的协调回合；被打断的成员子任务由启动
  扫描标为 interrupted，协调人醒来时会看到。
- 无进展判定、验收证据都在 ``TeamService`` 里，本模块不重复判断。不设额度。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_RUNNER: Optional["TeamRunner"] = None
STALL_NUDGES = 3          # 同一次推进里最多补叫这么多次（正常情况下 end_wake 先把它转成「等你」）
NUDGE = ("上一回合结束时没有成员在跑，也没有等主人或提交验收，之后不会再有事件叫醒你。"
         "现在决定下一步：派活或返工、交叉审查、team_ask_owner 问主人，或者 team_submit 提交验收。")


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
        self._closing = False

    # ── 成员 ──────────────────────────────────────────────────────────
    def kick(self, root_id: str) -> None:
        """把能开始的成员子任务放进后台池。"""
        if self._closing:
            return
        try:
            root = self.service.get(root_id)
            children = self.service.runnable(root)
        except Exception as error:  # noqa: BLE001
            logger.warning("任务上下文 %s 读不到：%s", root_id, type(error).__name__)
            return
        for child in children:
            if self.runner.is_active(child.id):
                continue
            number = child.collaboration["round"]
            self.runner.enqueue_existing(child.id, self._member_job(root_id, child.id, number),
                                         on_cancel=self._member_stopped(root_id, child.id, number, cancelled=True),
                                         on_failure=lambda error, cid=child.id, n=number: self._member_stopped(
                                             root_id, cid, n, error=f"{type(error).__name__}: {error}")())

    def _member_job(self, root_id: str, child_id: str, expected_round: int) -> Callable[[], Awaitable[None]]:
        async def job() -> None:
            root = self.service.get(root_id)
            work = self.service.collaboration(root)
            current = work.get(child_id)
            if current.collaboration["round"] != expected_round or current.status != "queued":
                return
            child = await self._member(self.repo_root, root_id, child_id)
            if work.get(child_id).collaboration["round"] != expected_round:
                return
            self._note(root_id, child)
            self.wake(root_id, describe(child))
        return job

    def _member_stopped(self, root_id: str, child_id: str, expected_round: int, *, cancelled: bool = False,
                        error: str = "") -> Callable[[], None]:
        def stopped() -> None:
            try:
                root = self.service.get(root_id)
                work = self.service.collaboration(root)
                child = work.get(child_id)
                if child.collaboration["round"] != expected_round:
                    return
                if child.status in {"queued", "running"}:
                    child = work.abort(child_id, expected_round, cancelled=cancelled,
                                       error=error[:300] or ("" if cancelled else "成员执行异常"))
                self._note(root_id, child)
                self.wake(root_id, describe(child))
            except Exception as failure:  # noqa: BLE001
                logger.warning("成员子任务 %s 收尾失败：%s", child_id, type(failure).__name__)
        return stopped

    def _note(self, root_id: str, child: Any) -> None:
        try:
            self.service.note_member_result(root_id, child)
        except Exception as error:  # noqa: BLE001 —— 记不上不影响叫醒协调人
            logger.warning("任务上下文 %s 记录成员结果失败：%s", root_id, type(error).__name__)

    # ── 协调人 ────────────────────────────────────────────────────────
    def wake(self, root_id: str, event: str) -> None:
        if self._closing:
            return
        self._pending.setdefault(root_id, []).append(event)
        task = self._driving.get(root_id)
        if task is None or task.done():
            task = asyncio.get_running_loop().create_task(self._drive(root_id))
            self._driving[root_id] = task
            task.add_done_callback(lambda completed: self._drive_finished(root_id, completed))

    def _drive_finished(self, root_id: str, completed: asyncio.Task) -> None:
        if self._driving.get(root_id) is completed:
            self._driving.pop(root_id, None)
        if not completed.cancelled():
            error = completed.exception()
            if error is not None:
                logger.warning("任务上下文 %s 调度回合异常结束：%s", root_id, type(error).__name__)

    async def _drive(self, root_id: str) -> None:
        from src.workflow.tasks.errors import CollaborationConflict
        nudges = 0
        while not self._closing and root_id in self._pending:
            events = self._pending.pop(root_id)
            nudges = nudges + 1 if events == [NUDGE] else 0
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
            if root_id not in self._pending and nudges < STALL_NUDGES and self._stuck(root_id):
                # 回合结束时既没人在跑、也没等主人或交验收：不会再有事件叫醒它，这里补一次。
                # 连续没进展由 TeamService.end_wake 转「等你」；这里同一次推进最多补叫 STALL_NUDGES 次，不会空转。
                self._pending[root_id] = [NUDGE]

    def _stuck(self, root_id: str) -> bool:
        try:
            root = self.service.get(root_id)
            if root.status != "active":
                return False
            children = self.service.children(root)
        except Exception:  # noqa: BLE001
            return False
        return not any(child.status in {"queued", "running"} or self.runner.is_active(child.id) for child in children)

    # ── 启动与停止 ─────────────────────────────────────────────────────
    async def recover(self) -> None:
        from src.workflow.tasks.team import LIVE
        if self._closing:
            return
        for root in self.service.list():
            if root.status not in LIVE:
                continue
            outcome = self.service.recover_after_restart(root.id)
            if outcome == "rerun":
                self.wake(root.id, "上次协调回合被重启打断。先用 team_status 核对已经派出和交回的情况，再继续")
                continue
            if outcome == "failed" or root.status != "active":
                continue
            cut = [child for child in self.service.children(root)
                   if child.status == "interrupted" or (child.status in {"done", "failed", "cancelled"}
                                                        and child.collaboration.get("review") != "accepted")]
            if cut:
                self.wake(root.id, "；".join(describe(child) for child in cut))
            elif root.team.get("inbox"):
                self.wake(root.id, "仍有主人留言未完成交付；先核对现有任务状态和执行结果，再处理留言")
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
        self._closing = True
        self._pending.clear()
        tasks = list(self._driving.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._driving.clear()
