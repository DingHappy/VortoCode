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
from typing import Iterable, Optional

from .channel import ChannelAdapter

_MARKUP = re.compile(r"\[/?[a-zA-Z][^\]]*\]")     # 去 Rich 标记（say 里的 [b]…[/b] 等）
_CONFIRM_TIMEOUT = 600                             # 按钮确认等待上限（秒）；超时=拒绝（安全不放行）
_SPLIT = re.compile(r"[,\s;]+")                    # allowFrom 的分隔符（逗号/空白/分号都收）


def _strip(text: str) -> str:
    return _MARKUP.sub("", str(text or "")).strip()


def parse_allow_from(raw: Optional[str]) -> Optional[frozenset]:
    """把一条 allowFrom 配置字符串解析成白名单集合。

    **返回值三态，别把后两种混为一谈**（B6-6 硬化最容易写反的地方）：

    - ``None``（配置项**根本没设**）→ 交回调用方走默认（配对制：只放 owner 一人）；
    - ``frozenset()``（配置项**设了但是空的**，如 ``VORTOCODE_IM_ALLOW_FROM=""`` 或 ``","``）
      → **显式空白名单 = 全拒**，连 owner 也拒。这是 fail-closed 的正确读法：很多 IM 桥把
      "空 = 不限制"当默认，结果任何陌生人都能驱动 agent（OPENCLAW_INTEGRATION 反面教材第 4 条）。
    """
    if raw is None:
        return None
    return frozenset(p for p in _SPLIT.split(str(raw).strip()) if p)


def normalize_allow_from(allow_from: Optional[Iterable], owner_id: str) -> frozenset:
    """算出最终生效的白名单：没配 → 只放 owner（向后兼容现有配对制部署）；配了 → 照配的来（空即全拒）。"""
    if allow_from is None:
        return frozenset({str(owner_id)}) if str(owner_id) else frozenset()
    return frozenset(str(x).strip() for x in allow_from if str(x).strip())


class IMBridge:
    def __init__(self, repo_root: str, adapter: ChannelAdapter, owner_id: str, *,
                 channel: str = "im", mode: str = "plan", llm=None, runner=None,
                 allow_from: Optional[Iterable] = None):
        self.repo_root = str(repo_root)
        self.adapter = adapter
        self.owner_id = str(owner_id)
        # 入站白名单：没配 → 只放 owner（配对制原样）；配了空 → 全拒。判定见 normalize_allow_from。
        self.allow_from = normalize_allow_from(allow_from, self.owner_id)
        self.mode = mode if mode in ("plan", "build") else "plan"
        self._llm = llm
        self._sid = f"sid-{channel}-{self.owner_id}"     # session_store 要求 sid- 前缀
        self._pending: dict = {}                         # cid -> Future（确认）
        self._turn_task: Optional[asyncio.Task] = None
        self._ignored = 0                                # 白名单外的入站计数
        self._ignored_no_mention = 0                     # 群聊里没 @ 到本机器人的入站计数
        self._confirm_holder = {"fn": None}
        self._progress_holder = {"fn": None}
        # 不支持编辑的通道（钉钉）进度只能发新消息 → 放慢节流免刷屏
        self._edits = getattr(adapter, "edits_supported", True)
        self._progress_interval = 2.0 if self._edits else 10.0
        # 后台任务运行时：serve 内嵌模式注入共享 runner（单一并发池/台账/订阅集，kind="im-dev"
        # 分发回本 bridge 的 worker，见 gateway/im_service）；standalone 懒建自己的（向后兼容）
        self._runner = runner
        self._shared_runner = runner is not None
        self._task_prog: dict = {}                       # tid -> 上次进度推送时间（节流）
        self.agent = self._build_agent()

    # ------------------------------------------------------------ 建 agent（第四端：走 gateway 单一工厂）
    def _build_agent(self):
        """薄壳：装配走 gateway 的单一工厂（kind="im"，最小面）；端侧只留 holder 模式
        （confirm/progress 每回合重绑到当前聊天，见 _run_turn）+ 会话磁盘复原。"""
        from src.gateway.agent_session import build_session

        async def _confirm(message: str) -> bool:
            fn = self._confirm_holder["fn"]
            return bool(await fn(message)) if fn is not None else False

        def _progress(msg: str) -> None:
            fn = self._progress_holder["fn"]
            if fn is not None:
                fn(msg)

        # IM 有配对的主人在手机那头（send_confirm 按钮/文本应答）→ 问得到人；无自动放行。
        # 显式声明：内核 gate 默认最严格（问不到人），忘了声明只会更严、不会更松。
        # untrusted_input=True：IM 入站是彻头彻尾的外部不可信内容（转发的网页、群里别人贴的文本、
        # 冒充运维的指令），**每个回合一开始就打污点**——污点态下一切免确认授权失效，
        # 记忆写入也降级（指令性文本不进长期记忆）。这条对 IM 无例外，见 OPENCLAW_INTEGRATION 第 2 条。
        agent = build_session(self.repo_root, kind="im", confirm=_confirm,
                              on_progress=_progress, llm=self._llm, can_ask_human=True,
                              untrusted_input=True)
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
        hello = (f"🤖 VortoCode 已就绪（{self.mode} 模式）· 仓库 {Path(self.repo_root).name}。"
                 f"发任务给我跑隔离流水线；/help 看用法。")
        if not self.allow_from:
            # 空白名单是合法的 fail-closed 配置，但必须**说出来**——否则表现为"机器人装死"，
            # 排查成本极高。开机就讲清楚：不是坏了，是白名单空 = 全拒。
            hello += ("\n⚠ 入站白名单（allowFrom）为空 → 当前**拒绝一切入站消息**，包括你自己。"
                      "清掉 VORTOCODE_IM_ALLOW_FROM（回到只放 owner）或把要放行的 id 填进去。")
        await self._safe_send(hello)
        async for ev in self.adapter.poll():
            try:
                await self._on_event(ev)
            except Exception as e:  # noqa: BLE001 —— 单条事件出错不拖垮长轮询
                await self._safe_send(f"（处理消息出错：{e}）")

    async def _on_event(self, ev) -> None:
        """**IM 侧唯一的入站闸**（三道，全是默认拒绝方向；通道适配器一律不自己判定）。

        ① 白名单：sender 不在 allow_from 里 → 丢。空白名单 = 谁都不行（含 owner）。
        ② 群提及门：群聊里没显式 @ 到本机器人 → 当没看见。陌生群/被拉进的群不再能驱动 agent。
        ③ 审批仍只认 owner：白名单可以放同事进来聊天，但**批准/拒绝确认是特权动作**，
           只有配对的主人能点——白名单变宽绝不能顺带把审批权变宽。
        """
        sender = str(ev.sender_id)
        if sender not in self.allow_from:           # ① 白名单外：静默忽略并计数
            self._ignored += 1
            return
        if getattr(ev, "is_group", False) and not getattr(ev, "mentioned", False):
            self._ignored_no_mention += 1           # ② 群里没 @ 到 → 当没看见
            return
        if ev.kind == "callback":                   # 按钮点击 → 解开对应确认 Future
            if sender != self.owner_id:             # ③ 审批只认主人
                self._ignored += 1
                return
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
            msg = (f"仓库 {Path(self.repo_root).name} · 模式 {self.mode} · "
                   f"{'运行中' if busy else '空闲'} · 白名单 {len(self.allow_from)} 人 · "
                   f"已忽略白名单外 {self._ignored} 条、群里没 @ 我 {self._ignored_no_mention} 条")
            recent = self._recent_plan()                 # 最近的 dev_auto 计划进度（可 dev_resume 续跑）
            if recent:
                msg += f"\n最近计划：{recent}"
            await self._safe_send(msg)
        elif cmd == "/new":
            self.agent.history = []
            if hasattr(self.agent, "plan"):
                self.agent.plan = None
            self._persist()
            await self._safe_send("已开新会话（清空上下文）。")
        elif cmd == "/task":
            await self._submit_task(text[len("/task"):].strip())
        elif cmd == "/tasks":
            await self._list_tasks()
        elif cmd == "/help":
            await self._safe_send(
                "直接发任务 → 我跑隔离流水线（分解/实现/自测/落 vorto 分支）。\n"
                "/task <描述> 后台跑（不占当前会话，进度自动推、完成发开 PR 按钮）· /tasks 看后台任务。\n"
                "/mode plan|build 切模式 · /status 看状态 · /new 清空会话。\n"
                "写文件/跑命令/开 PR 会发按钮让你确认（人在关口）。")
        else:
            await self._safe_send(f"未知命令 {cmd}。/help 看用法。")

    # ------------------------------------------------------------ 后台任务（不占回合）
    def _get_runner(self):
        if self._runner is None:
            from src.gateway import TaskRunner
            self._runner = TaskRunner(self.repo_root, self._task_worker,
                                      on_update=self._on_task_update)
        return self._runner

    async def _task_worker(self, task, on_progress):
        """后台 dev 任务：跑 dev_auto（open_pr=True + 后台确认门 + draft）；集成绿→按钮→draft PR。"""
        from src.agents.dev_plan import load_plan
        from src.agents.main_agent import build_dev_tools
        tools = {t.name: t for t in build_dev_tools(self.repo_root, on_progress=on_progress,
                                                    confirm=self._bg_confirm, draft_pr=True)}
        # task-scoped plan_id 钉住本次计划，不靠"全局最新 plan"猜（并发多任务会串单，#128 评审）
        pid = f"bg-{task.id}"
        result = await tools["dev_auto"].handler({"task": task.prompt, "open_pr": True, "plan_id": pid})
        plan = load_plan(self.repo_root, pid)
        if plan is not None:
            task.plan_id = plan.plan_id
            task.branch = plan.branch
        return result

    async def _bg_confirm(self, message: str) -> bool:
        """回合外确认（后台任务用）：发按钮 → 等 owner 点击（poll 循环的 callback 分支解 Future）。超时=拒绝。"""
        cid = uuid.uuid4().hex[:12]
        fut = asyncio.get_event_loop().create_future()
        self._pending[cid] = fut
        try:
            await self.adapter.send_confirm(_strip(message), cid)
            return bool(await asyncio.wait_for(fut, timeout=_CONFIRM_TIMEOUT))
        except Exception:  # noqa: BLE001 —— 超时/取消/出错一律拒绝（安全不放行）
            return False
        finally:
            self._pending.pop(cid, None)

    def _on_task_update(self, task) -> None:
        """runner 的状态回调（同步）：进度节流推送、终态发结论。调度到事件循环上异步发。"""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        if task.status in ("done", "failed", "cancelled", "interrupted"):
            tail = (task.result or task.error or "").strip()[-500:]
            loop.create_task(self._safe_send(f"后台任务 {task.id} · {task.status}\n{tail}"))
        elif task.status == "running" and task.log:
            now = time.monotonic()
            if now - self._task_prog.get(task.id, 0.0) >= self._progress_interval:
                self._task_prog[task.id] = now
                loop.create_task(self._safe_send(f"🏃 {task.id}: {_strip(task.log[-1])}"))

    async def _submit_task(self, prompt: str) -> None:
        if not prompt:
            await self._safe_send("用法：/task <要后台跑的任务描述>")
            return
        # 共享 runner（serve 内嵌）：kind="im-dev" 让分发路由回本 bridge 的 worker（保住在跑中的
        # 按钮确认 UX）；standalone 自己的 runner worker 就是 _task_worker，kind 只是标注。
        kind = "im-dev" if self._shared_runner else "dev"
        task = await self._get_runner().submit(prompt, kind=kind)
        await self._safe_send(f"✅ 已在后台开跑 {task.id}（不占当前会话；进度会自动推、完成发开 PR 按钮）。"
                              f"\n/tasks 看全部后台任务。")

    async def _list_tasks(self) -> None:
        tasks = self._get_runner().list()
        if not tasks:
            await self._safe_send("暂无后台任务。/task <描述> 开一个。")
            return
        lines = [f"· {t.summary()}" for t in tasks[:10]]
        await self._safe_send("后台任务：\n" + "\n".join(lines))

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
                    if pid is not None and self._edits:       # 收尾把进度刷到最终态（仅支持编辑的通道）
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
        if now - last_edit >= self._progress_interval:   # 节流（支持编辑=2s，不支持=10s 防刷屏）
            await self.adapter.edit_text(pid, text)       # 不支持编辑的通道 edit_text 内部发新消息
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

    def _recent_plan(self) -> Optional[str]:
        """最近一条 dev_auto 计划的一行概况（供 /status）；无/出错 → None。未完成的可 dev_resume 续跑。"""
        try:
            from src.agents.dev_plan import list_plans
            plans = list_plans(self.repo_root)
        except Exception:  # noqa: BLE001
            return None
        return plans[0]["summary"] if plans else None

    async def _safe_send(self, text: str) -> None:
        try:
            await self.adapter.send_text(text)
        except Exception:  # noqa: BLE001
            pass
