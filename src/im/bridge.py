"""IM 桥核心（通道无关）——把主 agent 编排到一个 ChannelAdapter 上。

并发模型直接沿用 Web /agent（realtime.py 已验证）：poll 循环独立于回合；回合作后台任务；同步回调
只往队列 put_nowait、由 drain 协程串行发出（不阻塞、不乱序）；唯一 await 挂起的 confirm 走 Future，
poll 循环收到按钮点击时 set_result 解开——confirm 挂起期间照样收发。串行回合（一次只跑一个）。
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from .channel import ChannelAdapter

_MARKUP = re.compile(r"\[/?[a-zA-Z][^\]]*\]")     # 去 Rich 标记（say 里的 [b]…[/b] 等）
_CONFIRM_TIMEOUT = 600                             # 按钮确认等待上限（秒）；超时=拒绝（安全不放行）


def _strip(text: str) -> str:
    return _MARKUP.sub("", str(text or "")).strip()


class IMBridge:
    def __init__(self, repo_root: str, adapter: ChannelAdapter, owner_id: str, *,
                 channel: str = "im", mode: str = "plan", llm=None):
        self.repo_root = str(repo_root)
        self.adapter = adapter
        self.owner_id = str(owner_id)
        self.mode = mode if mode in ("plan", "build") else "plan"
        self._llm = llm
        self._sid = f"sid-{channel}-{self.owner_id}"     # session_store 要求 sid- 前缀
        self._pending: dict = {}                         # cid -> Future（确认）
        self._turn_task: Optional[asyncio.Task] = None
        self._ignored = 0                                # 非主人消息计数（配对制）
        self._confirm_holder = {"fn": None}
        self._progress_holder = {"fn": None}
        self.agent = self._build_agent()

    # ------------------------------------------------------------ 建 agent（第四端：走同源工厂 + catalog）
    def _build_agent(self):
        from src.agents.main_agent import (MainAgent, build_agent_tools, native_default,
                                           skill_catalog)
        from src.agents.permissions import load_permissions
        from src.agents.project import load_project_instructions

        async def _confirm(message: str) -> bool:
            fn = self._confirm_holder["fn"]
            return bool(await fn(message)) if fn is not None else False

        def _progress(msg: str) -> None:
            fn = self._progress_holder["fn"]
            if fn is not None:
                fn(msg)

        tools = build_agent_tools(self.repo_root, confirm=_confirm, on_progress=_progress,
                                  with_artifacts=False)
        parts = []
        proj = load_project_instructions(self.repo_root)
        if proj:
            parts.append(proj)
        cat = skill_catalog(self.repo_root)
        if cat:
            parts.append(f"【可用技能】(需要时用 use_skill 加载其完整指令再执行)\n{cat}")
        kwargs = dict(plan_tool=True, permissions=load_permissions(self.repo_root),
                      env_context=True, native=native_default())
        if parts:
            kwargs["extra_system"] = "\n\n".join(parts)
        if self._llm is not None:
            kwargs["llm"] = self._llm
        agent = MainAgent(tools, **kwargs)
        self._restore_session(agent)
        return agent

    def _restore_session(self, agent) -> None:
        try:
            from src.web.session_store import load_session
            saved = load_session(self.repo_root, self._sid)
        except Exception:  # noqa: BLE001
            saved = None
        if saved:
            agent.history = saved.get("history") or []
            if saved.get("plan"):
                agent.plan = saved["plan"]

    def _persist(self) -> None:
        try:
            from src.web.session_store import save_session
            save_session(self.repo_root, self._sid, transcript=[],
                         history=self.agent.history, plan=getattr(self.agent, "plan", None))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 主循环
    async def run(self) -> None:
        await self._safe_send(
            f"🤖 VortoCode 已就绪（{self.mode} 模式）· 仓库 {Path(self.repo_root).name}。"
            f"发任务给我跑隔离流水线；/help 看用法。")
        async for ev in self.adapter.poll():
            try:
                await self._on_event(ev)
            except Exception as e:  # noqa: BLE001 —— 单条事件出错不拖垮长轮询
                await self._safe_send(f"（处理消息出错：{e}）")

    async def _on_event(self, ev) -> None:
        if str(ev.sender_id) != self.owner_id:      # 配对制：只服务主人，其它静默忽略并计数
            self._ignored += 1
            return
        if ev.kind == "callback":                   # 按钮点击 → 解开对应确认 Future
            fut = self._pending.get(ev.callback_id)
            if fut is not None and not fut.done():
                fut.set_result(bool(ev.approved))
            await self.adapter.ack_callback(ev)
            return
        if ev.kind == "message":
            text = (ev.text or "").strip()
            if not text:
                return
            if text.startswith("/"):
                await self._handle_command(text)
                return
            if self._turn_task is not None and not self._turn_task.done():
                await self._safe_send("⏳ 上一个任务还在跑，等它完成再发新的（/status 看状态）。")
                return
            self._turn_task = asyncio.create_task(self._run_turn(text))

    async def _handle_command(self, text: str) -> None:
        cmd = text.split()[0].lower()
        if cmd == "/mode":
            arg = text[len("/mode"):].strip()
            self.mode = arg if arg in ("plan", "build") else ("build" if self.mode == "plan" else "plan")
            await self._safe_send(f"模式已切到 **{self.mode}**（plan=只读、build=可写/跑流水线）。")
        elif cmd == "/status":
            busy = self._turn_task is not None and not self._turn_task.done()
            await self._safe_send(
                f"仓库 {Path(self.repo_root).name} · 模式 {self.mode} · "
                f"{'运行中' if busy else '空闲'} · 已忽略非主人消息 {self._ignored} 条")
        elif cmd == "/new":
            self.agent.history = []
            if hasattr(self.agent, "plan"):
                self.agent.plan = None
            self._persist()
            await self._safe_send("已开新会话（清空上下文）。")
        elif cmd == "/help":
            await self._safe_send(
                "直接发任务 → 我跑隔离流水线（分解/实现/自测/落 vorto 分支）。\n"
                "/mode plan|build 切模式 · /status 看状态 · /new 清空会话。\n"
                "写文件/跑命令/开 PR 会发按钮让你确认（人在关口）。")
        else:
            await self._safe_send(f"未知命令 {cmd}。/help 看用法。")

    # ------------------------------------------------------------ 一个回合（queue+drain+Future）
    async def _run_turn(self, text: str) -> None:
        q: asyncio.Queue = asyncio.Queue()
        self._confirm_holder["fn"] = self._make_confirm(q)
        self._progress_holder["fn"] = lambda msg: q.put_nowait(("progress", str(msg)))

        def _say(m: str) -> None:
            q.put_nowait(("progress", _strip(m)))

        async def _inner():
            try:
                reply = await self.agent.run_turn(text, mode=self.mode, say=_say,
                                                  emit=lambda _t: None)
                q.put_nowait(("final", reply))
            except asyncio.CancelledError:
                q.put_nowait(("final", "（已取消）"))
                raise
            except Exception as e:  # noqa: BLE001
                q.put_nowait(("final", f"（执行出错：{e}）"))
            finally:
                q.put_nowait(("__done__", None))

        inner = asyncio.create_task(_inner())
        pid: Optional[str] = None          # 进度消息 id（滚动编辑，防刷屏）
        lines: list = []
        last_edit = 0.0
        try:
            while True:
                kind, payload = await q.get()
                if kind == "__done__":
                    break
                if kind == "progress":
                    lines.append(str(payload))
                    pid, last_edit = await self._push_progress(pid, lines, last_edit)
                elif kind == "confirm":
                    message, cid = payload
                    await self.adapter.send_confirm(message, cid)
                elif kind == "final":
                    if pid is not None:                       # 收尾把进度刷到最终态
                        await self.adapter.edit_text(pid, "✅ " + "\n".join(lines[-12:]))
                    await self._safe_send(str(payload) or "（无输出）")
        finally:
            self._confirm_holder["fn"] = None
            self._progress_holder["fn"] = None
            try:
                await inner
            except asyncio.CancelledError:
                pass
            self._persist()

    async def _push_progress(self, pid, lines, last_edit):
        text = "🏃 " + "\n".join(lines[-12:])
        now = time.monotonic()
        if pid is None:
            return await self.adapter.send_text(text), now
        if now - last_edit >= 2.0:                # 节流：≥2s 才编辑一次（防 Telegram 限流/刷屏）
            await self.adapter.edit_text(pid, text)
            return pid, now
        return pid, last_edit

    def _make_confirm(self, q: asyncio.Queue):
        loop = asyncio.get_event_loop()

        async def _confirm(message: str) -> bool:
            cid = uuid.uuid4().hex[:12]
            fut = loop.create_future()
            self._pending[cid] = fut
            q.put_nowait(("confirm", (message, cid)))        # drain 发带按钮的消息
            try:
                return bool(await asyncio.wait_for(fut, timeout=_CONFIRM_TIMEOUT))
            except Exception:  # noqa: BLE001 —— 超时/取消/出错一律拒绝（安全不放行）
                return False
            finally:
                self._pending.pop(cid, None)

        return _confirm

    async def _safe_send(self, text: str) -> None:
        try:
            await self.adapter.send_text(text)
        except Exception:  # noqa: BLE001
            pass
