"""IM 桥核心（通道无关）——把主 agent 编排到一个 ChannelAdapter 上。

并发模型直接沿用 Web /agent（realtime.py 已验证）：poll 循环独立于回合；回合作后台任务；同步回调
只往队列 put_nowait、由 drain 协程串行发出（不阻塞、不乱序）；唯一 await 挂起的 confirm 走 Future，
poll 循环收到按钮点击时 set_result 解开——confirm 挂起期间照样收发。串行回合（一次只跑一个）。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Iterable, Optional

from .channel import ChannelAdapter
from src.utils.exc_utils import _exc_text

_MARKUP = re.compile(r"\[/?[a-zA-Z][^\]]*\]")     # 去 Rich 标记（say 里的 [b]…[/b] 等）
_CONFIRM_TIMEOUT = 600                             # 按钮确认等待上限（秒）；超时=拒绝（安全不放行）
_SPLIT = re.compile(r"[,\s;]+")                    # allowFrom 的分隔符（逗号/空白/分号都收）

# 流水线的人批入口。**中英两套拼写指同一件事**：ASCII 那套能被 Telegram 的命令补全认出来，
# 中文那套手上快——手机上批东西，少打一个字都是真的省。
_PIPE_LIST = {"/pipe", "/流水线"}
_PIPE_GO = {"/go", "/推"}
_PIPE_VERDICTS = {"/ok": "approve", "/批": "approve",
                  "/no": "reject", "/驳": "reject",
                  "/later": "defer", "/挂": "defer"}


def _persona_path_for(repo_root: str):
    from pathlib import Path
    return Path(repo_root) / ".vortocode" / "persona.md"


def read_persona(repo_root: str) -> str:
    """从工作区读人设。**模块级函数**而不是实例方法——_build_agent 会被契约测试用桩对象
    调用（SimpleNamespace 没有实例方法），把装配路径绑死在实例上会让那层契约失效。"""
    try:
        return _persona_path_for(repo_root).read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        return ""


def _heartbeat_interval() -> float:
    """后台任务"还活着"心跳的间隔（秒）。0/负数 = 关闭。env VORTOCODE_IM_HEARTBEAT_EVERY 调。

    真机 2026-07-26：集成验证一跑就是 4 分钟，加上自测和审查，IM 端能安静七八分钟——
    人看着就是死机，会去 kill 掉一个其实正常的任务。默认 150s 兼顾"有活气"与"不刷屏"。
    """
    import os
    try:
        return float(os.getenv("VORTOCODE_IM_HEARTBEAT_EVERY") or 150.0)
    except ValueError:
        return 150.0


def _fmt_elapsed(seconds: float) -> str:
    m, s = divmod(int(max(0.0, seconds)), 60)
    return f"{m} 分 {s} 秒" if m else f"{s} 秒"


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
                 with_dev: bool = True, persona: str = "",
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
        # 上一回合还在跑时后来的消息**进队列**而不是被拒。原实现直接 return，用户的话就丢了、
        # 还得重打一遍——尤其外包看图会多一次 LLM 往返，"忙"的窗口被拉长，撞上的概率更高
        # （真机 2026-07-27）。有界是为了防刷屏堆积：满了才如实说满了。
        self._pending_msgs: list = []
        self._queue_cap = 3
        self._ignored = 0                                # 白名单外的入站计数
        self._ignored_no_mention = 0                     # 群聊里没 @ 到本机器人的入站计数
        self._confirm_holder = {"fn": None}
        self._progress_holder = {"fn": None}
        # 不支持编辑的通道（钉钉）进度只能发新消息 → 放慢节流免刷屏
        self._edits = getattr(adapter, "edits_supported", True)
        self._progress_interval = 2.0 if self._edits else 10.0
        # 后台任务运行时：serve 内嵌模式注入共享 runner（单一并发池/台账/订阅集，kind="im-dev"
        # 分发回本 bridge 的 worker，见 gateway/im_service）；standalone 懒建自己的（向后兼容）
        # 研究员助手（给同事用）：不给改主项目代码/落分支/开 PR 的工具面
        self._with_dev = bool(with_dev)
        # 人设：由本人首次对话时自述，落在自己的状态目录里（见 _persona_path）
        self._persona = str(persona or "")
        self._runner = runner
        self._shared_runner = runner is not None
        self._task_prog: dict = {}                       # tid -> 上次进度推送时间（节流）
        # 心跳：后台任务的阶段之间可以很久没有新日志（真机 2026-07-26：集成验证一跑 4 分钟，
        # IM 端安静七八分钟，人看着就是死机）。这里按任务记「首次见到/上次播报」，由 _on_task_update
        # 顺带驱动——不另起定时器，避免多一条要管生命周期的后台协程。
        self._task_started: dict = {}                    # tid -> 开跑时刻（算已用时）
        # 两个概念别混：_heartbeat_every 是**多久看一眼**（tick），_heartbeat_quiet 是**安静多久才
        # 准开口**。刚播过真进度就该让位，否则心跳和阶段播报会叠着刷屏。
        self._heartbeat_every = _heartbeat_interval()
        self._heartbeat_quiet = self._heartbeat_every
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._seen_reconnects = 0                        # 心跳里比对通道重连计数 → 触发补发
        self.agent = self._build_agent()

    # ------------------------------------------------------------ 人设（本人自述，不由管理员代填）
    def _persona_path(self):
        return _persona_path_for(self.repo_root)

    def _load_persona(self) -> str:
        """读本助手的人设。**每个助手一个独立工作区**，所以人设天然按人隔离。"""
        return self._persona or read_persona(self.repo_root)

    def _save_persona(self, text: str) -> None:
        p = self._persona_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text.strip() + "\n", encoding="utf-8")
        self._persona = text.strip()

    def persona_prompt(self) -> str:
        """没设人设时的引导语。让**本人自己说**，比管理员替他想准得多。"""
        return ("👋 我是你的专属助手。第一次见面，先让我了解你——直接回一段话就行：\n"
                "  · 我该怎么称呼你？\n"
                "  · 你主要做什么工作？关注哪些领域？\n"
                "  · 希望我帮你做什么？（查资料 / 盯竞品 / 整理文档…）\n\n"
                "之后随时用 /persona 看或改。")

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
        persona = getattr(self, "_persona", "") or read_persona(self.repo_root)
        agent = build_session(self.repo_root, kind="im", confirm=_confirm,
                              with_dev=getattr(self, "_with_dev", True),
                              extra_system=(f"【服务对象】{persona}" if persona else None),
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
            # 模式跟着会话走：`/mode build` 是主人的一次显式决定，不该被一次服务重启抹回 plan
            # （真机 2026-07-27：一天重启七八次，主人每次动手都要重新授权一遍，问"每次都要这样吗"）。
            if saved.get("mode") in ("plan", "build"):
                self.mode = saved["mode"]
            # 升级后复原旧会话：工具清单有变要告诉模型，否则历史里过期的「我做不到」会被
            # 当真话复读（真机 2026-07-27：#243 部署后钉钉里要截图仍被拒，模型把升级前那句
            # 否认逐字复读，连推荐的第三方工具名都一样）。判定在内核，端只调用。
            from src.gateway.agent_session import inject_capability_note
            inject_capability_note(agent, saved)

    def _persist(self) -> None:
        try:
            from src.gateway.agent_session import session_behavior_fp, session_tool_names
            from src.web.session_store import save_session
            save_session(self.repo_root, self._sid, transcript=[],
                         history=self.agent.history, plan=getattr(self.agent, "plan", None),
                         tool_names=session_tool_names(self.agent),
                         behavior_fp=session_behavior_fp(self.agent),
                         mode=self.mode)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 配置自检（只诊断，不改判定）
    def allow_from_warning(self) -> Optional[str]:
        """白名单配错时的一句人话提醒（开机横幅 + /status 都用）；配置正常返回 None。

        **判定逻辑一个字都不改**——下面两种都是显式配置的直接后果，继续按 fail-closed 丢消息。
        这里只解决可诊断性：这两种配错的表现都是"机器人装死"，主人第一反应是"坏了/连不上"，
        排查能耗一整晚。开机就把话说明白，比什么都省事。
        """
        if not self.allow_from:
            # 空白名单是合法的 fail-closed 配置（"空 ≠ 不限制"），但必须说出来。
            return ("⚠ 入站白名单（allowFrom）为空 → 当前**拒绝一切入站消息**，包括你自己。"
                    "清掉 VORTOCODE_IM_ALLOW_FROM（回到只放 owner）或把要放行的 id 填进去。")
        if self.owner_id and self.owner_id not in self.allow_from:
            # 配了白名单却漏了自己：主人的消息、以及**审批 y/n 与按钮点击**都在第一道闸就被丢，
            # 表现为"机器人不理我 + 每个确认都等到 600s 超时被拒"，极难猜到是白名单漏了自己。
            return (f"⚠ 入站白名单（allowFrom）里没有 owner（{self.owner_id}）→ 你自己发的消息和"
                    f"审批（y/n、按钮点击）都会被白名单闸丢掉，表现为「机器人不理人、确认永远超时」。"
                    f"这是显式配置，判定不会为你放宽——把 {self.owner_id} 加进 "
                    f"VORTOCODE_IM_ALLOW_FROM（或通道专属的 *_ALLOW_FROM）。")
        return None

    # ------------------------------------------------------------ 开机横幅（去重）
    def _hello_stamp_path(self) -> Path:
        return Path(self.repo_root) / ".vortocode" / "logs" / "im_hello.json"

    def _should_say_hello(self) -> bool:
        """这次重启值不值得跟主人说一声。

        为什么要判：横幅挂在**每次进程启动**上，而部署就是重启。2026-08-01 一晚部署八次，
        主人的对话里就插了八条一模一样的"已就绪"，把真正的卡片和结果冲散了。
        横幅本身有用（久宕后恢复、白名单配错），坏在**routine 重启也照说不误**。

        判据：距上次说过不足 ``VORTOCODE_IM_HELLO_QUIET_MIN`` 分钟（默认 30）就闭嘴。
        连着部署几次只会看见第一条；真宕了几小时再回来照样报平安。
        设成 0 = 每次都说（今天的行为，留给想要它的人）。

        **有警告时不走这条**（调用方保证）：白名单配错的表现是"机器人装死"，
        那句提醒漏一次就可能让人排查一整晚。
        """
        try:
            quiet_min = float(os.getenv("VORTOCODE_IM_HELLO_QUIET_MIN", "30") or 30)
        except ValueError:
            quiet_min = 30.0
        if quiet_min <= 0:
            return True
        try:
            last = float(json.loads(
                self._hello_stamp_path().read_text(encoding="utf-8")).get("at") or 0)
        except Exception:  # noqa: BLE001 —— 没有/坏了都当"没说过"，宁可多说一句
            return True
        return (time.time() - last) >= quiet_min * 60

    def _mark_hello_said(self) -> None:
        p = self._hello_stamp_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"at": time.time()}), encoding="utf-8")
        except Exception:  # noqa: BLE001 —— 写不下就当没去重，绝不因此拦住启动
            pass

    # ------------------------------------------------------------ 主循环
    async def run(self) -> None:
        # 时间戳用 time.time()（墙钟）不用 monotonic：要跨进程重启比较，monotonic 重启即归零。
        warn = self.allow_from_warning()
        if warn or self._should_say_hello():
            hello = (f"🤖 VortoCode 已就绪（{self.mode} 模式）· 仓库 {Path(self.repo_root).name}。"
                     f"发任务给我跑隔离流水线；/help 看用法。")
            if warn:
                hello += "\n" + warn
            await self._safe_send(hello)
            self._mark_hello_said()
        await self.flush_pending_notices()      # 上次断连/下线期间攒下的通知，开机就补
        if self._heartbeat_every > 0 and self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            async for ev in self.adapter.poll():
                try:
                    await self._on_event(ev)
                except Exception as e:  # noqa: BLE001 —— 单条事件出错不拖垮长轮询
                    await self._safe_send(f"（处理消息出错：{_exc_text(e)}）")
        finally:
            t, self._heartbeat_task = self._heartbeat_task, None
            if t is not None:
                t.cancel()                                # 桥停了心跳也得停，别留孤儿协程

    # ------------------------------------------------------------ 活性 / 未送达补发
    def liveness(self) -> dict:
        """本桥的活性快照（转发通道的计数 + 桥自身状态）。**不含任何凭证/会话标识**。"""
        lv = {}
        try:
            lv = dict(self.adapter.liveness())
        except Exception:  # noqa: BLE001 —— 自定义 adapter 没实现就给空，别让运维面挂掉
            lv = {}
        lv["channel"] = self._sid.split("-")[1] if "-" in self._sid else "im"
        lv["busy"] = self._turn_task is not None and not self._turn_task.done()
        lv["queued"] = len(self._pending_msgs)
        try:
            from src.gateway.notices import pending_undelivered_count
            lv["undelivered"] = pending_undelivered_count(self.repo_root)
        except Exception:  # noqa: BLE001
            lv["undelivered"] = 0
        return lv

    def _pending_undelivered(self) -> int:
        """补发队列里还剩几条。读不到当 0——补发是旁路，绝不能反过来拖垮心跳。"""
        try:
            from src.gateway.notices import pending_undelivered_count
            return int(pending_undelivered_count(self.repo_root) or 0)
        except Exception:  # noqa: BLE001
            return 0

    async def flush_pending_notices(self) -> dict:
        """把断连期间没送到的通知补推一条汇总给主人。

        触发点有两个：桥启动（run 的开头）、心跳循环发现通道重连过。恢复了却要人自己去翻台账，
        等于"补发"永远是人手动干的活——2026-07-28 早上那次补发就是我手动跑脚本干的。
        """
        try:
            from src.gateway.notices import flush_undelivered
            return await flush_undelivered(self.repo_root, self.notify_send)
        except Exception:  # noqa: BLE001 —— 补发是旁路，绝不能拖垮开机/心跳
            return {"sent": 0, "dropped": 0, "kept": 0}

    async def _heartbeat_loop(self) -> None:
        """后台任务安静太久时报个平安：阶段 + 已用时。**只在真有任务在跑时说话**。

        为什么不复用 _on_task_update：安静期恰恰是因为没有 update 事件，事件驱动在这里必哑。
        """
        while True:
            try:
                await asyncio.sleep(self._heartbeat_every)
                # 补发的触发条件有两个，**第二个比第一个重要**：
                #   ① 通道重连过——之前多半有通知没送出去；
                #   ② 队列里就是有东西——不管为什么进去的。
                # 只认①的话，**别的进程排进来的通知永远等不到**：`notify_owner` 是进程内的
                # （_OWNER_NOTIFIER 是 serve 进程的模块全局），而 cron 的 command: 作业是
                # 子进程——`vc pipeline tick` 在那里发现"该你批了"，只能落台账 + 排队。
                # 桥要是连着好几天不断，那条就一直躺着，12 小时后按 STALE_HOURS 当过期丢掉。
                # 结果是"卡住了叫人"从来叫不到人（2026-09-10 真机逮到：作业配好了却是哑的）。
                # 队列为空时 pending_undelivered_count 只读一个小文件，不值得为它省。
                reconnects = int(self.liveness().get("reconnects") or 0)
                queued = self._pending_undelivered()
                if reconnects != self._seen_reconnects or queued:
                    self._seen_reconnects = reconnects
                    await self.flush_pending_notices()
                runner = self._runner
                if runner is None:
                    continue
                now = time.monotonic()
                for task in list(runner.list() or []):
                    if getattr(task, "status", "") != "running":
                        self._task_started.pop(getattr(task, "id", ""), None)
                        continue
                    tid = task.id
                    self._task_started.setdefault(tid, now)
                    last = self._task_prog.get(tid, 0.0)
                    if now - last < self._heartbeat_quiet:      # 刚播过真进度就别插嘴
                        continue
                    self._task_prog[tid] = now
                    stage = _strip((task.log or ["（准备中）"])[-1])[:60]
                    await self._safe_send(
                        f"⏳ {tid} 仍在跑 · {stage} · 已用 "
                        f"{_fmt_elapsed(now - self._task_started[tid])}")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 —— 心跳是附加品，绝不能拖垮桥
                continue

    async def _on_event(self, ev) -> None:
        """**IM 侧唯一的入站闸**（三道，全是默认拒绝方向；通道适配器一律不自己判定）。

        ① 白名单：sender 不在 allow_from 里 → 丢。空白名单 = 谁都不行（含 owner）。
        ② 群提及门：群聊里没显式 @ 到本机器人 → 当没看见。陌生群/被拉进的群不再能驱动 agent。
        ③ 审批仍只认 owner：白名单可以放同事进来聊天，但**批准/拒绝确认是特权动作**，
           只有配对的主人能点——白名单变宽绝不能顺带把审批权变宽。

        回复路由（钉钉 sessionWebhook 等）也只跟**过了闸**的事件走：commit_reply_target 在
        ①② 之后才调——被丢掉的消息若能改写回复目标，陌生人发一条废话就能把 agent 的后续
        产出（进度/结果/确认提问）劫到自己的会话里。
        """
        sender = str(ev.sender_id)
        if sender not in self.allow_from:           # ① 白名单外：静默忽略并计数
            self._ignored += 1
            return
        if getattr(ev, "is_group", False) and not getattr(ev, "mentioned", False):
            self._ignored_no_mention += 1           # ② 群里没 @ 到 → 当没看见
            return
        self.adapter.commit_reply_target(ev)        # 过了闸，才采纳本条的回复路由
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
            imgs = list(getattr(ev, "images", []) or [])
            atts = list(getattr(ev, "files", []) or [])
            bad = str(getattr(ev, "unsupported", "") or "").strip()
            if bad:
                # 附件取不到就**如实说**——静默丢弃等于让人对着石沉大海干等
                await self._safe_send(f"⚠️ {bad}")
            if not text and not imgs and not atts:
                return
            if text.startswith("/"):
                await self._handle_command(text)
                return
            # 研究员助手第一次见面：先请本人自述人设，再开始干活。
            # **不由管理员代填**——本人两句话胜过旁人揣摩三段。
            if not self._with_dev and not self._load_persona():
                if len(text) >= 8:                       # 够长就当作自述收下
                    self._save_persona(text)
                    await self._safe_send(
                        "✅ 记住了，我按这个来。之后 /persona 可随时看或改。\n\n现在说说要我做什么？")
                else:
                    await self._safe_send(self.persona_prompt())
                return
            if self._turn_task is not None and not self._turn_task.done():
                if len(self._pending_msgs) >= self._queue_cap:
                    await self._safe_send(
                        f"⏳ 前面还有 {len(self._pending_msgs)} 条排着，这条没收下——"
                        f"先等等，或 /stop 中断当前任务。")
                    return
                self._pending_msgs.append((text, imgs, atts))
                await self._safe_send(
                    f"📥 已排队（前面 {len(self._pending_msgs) - 1} 条 + 1 个在跑）"
                    f"，跑完自动接着处理。/stop 可中断当前任务。")
                return
            self._start_turn(text, imgs, atts)

    def _start_turn(self, text: str, images: list, files: list) -> None:
        """起一个回合，收尾时自动取下一条排队消息——队列在这里前进，不靠外部驱动。"""
        async def _runner():
            try:
                await self._run_turn(text, images=images, files=files)
            finally:
                if self._pending_msgs:
                    nxt_text, nxt_imgs, nxt_files = self._pending_msgs.pop(0)
                    left = len(self._pending_msgs)
                    await self._safe_send(f"▶️ 接着处理排队的消息"
                                          + (f"（还剩 {left} 条）" if left else "") + "：")
                    self._start_turn(nxt_text, nxt_imgs, nxt_files)
        self._turn_task = asyncio.create_task(_runner())

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
            warn = self.allow_from_warning()             # 白名单配错的自检（开机横幅可能早刷没了）
            if warn:
                msg += "\n" + warn
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
        elif cmd == "/persona":
            arg = text[len("/persona"):].strip()
            if arg:
                self._save_persona(arg)
                await self._safe_send("✅ 人设已更新：\n" + arg[:300])
            else:
                cur = self._load_persona()
                await self._safe_send(("当前人设：\n" + cur) if cur
                                      else "还没设人设。\n" + self.persona_prompt())
        elif cmd == "/stop":
            t = self._turn_task
            n = len(self._pending_msgs)
            self._pending_msgs.clear()             # 中断=连排队的一起清，否则"停"了还会继续冒
            if t is not None and not t.done():
                t.cancel()
                await self._safe_send(f"⏹ 已中断当前任务"
                                      + (f"，并清掉 {n} 条排队消息。" if n else "。"))
            else:
                await self._safe_send("当前没有在跑的任务。" + (f"（清掉了 {n} 条排队）" if n else ""))
        elif cmd in _PIPE_LIST:
            await self._pipe_list(text.split(maxsplit=1)[1].strip()
                                  if len(text.split(maxsplit=1)) > 1 else "")
        elif cmd in _PIPE_GO:
            await self._pipe_go(text.split(maxsplit=1)[1].strip()
                                if len(text.split(maxsplit=1)) > 1 else "")
        elif cmd in _PIPE_VERDICTS:
            await self._pipe_review(_PIPE_VERDICTS[cmd],
                                    text.split(maxsplit=1)[1].strip()
                                    if len(text.split(maxsplit=1)) > 1 else "")
        elif cmd == "/help":
            await self._safe_send(
                "直接发任务 → 我跑隔离流水线（分解/实现/自测/落 vorto 分支）。\n"
                "/task <描述> 后台跑（不占当前会话，进度自动推、完成发开 PR 按钮）· /tasks 看后台任务。\n"
                "/mode plan|build 切模式 · /status 看状态 · /stop 中断当前任务 · /new 清空会话。\n"
                "/pipe 看流水线 · /ok 批（批完自动往下推）· /no <意见> 驳回 · /later 挂起 · "
                "/go 推一道（对外工序会弹按钮）。中文别名 /批 /驳 /挂 /推。\n"
                "写文件/跑命令/开 PR 会发按钮让你确认（人在关口）。")
        else:
            await self._safe_send(f"未知命令 {cmd}。/help 看用法。")

    # ------------------------------------------------------------ 流水线（在手机上批）
    def _live_runs(self) -> list:
        """还没走完的流水线运行，等人批的排最前——手机上第一屏就该是"该你管的"。"""
        from src.gateway.pipeline import PipelineStore
        runs = [r for r in PipelineStore(self.repo_root).list()
                if r.status not in {"done", "abandoned"}]
        return sorted(runs, key=lambda r: (r.status != "awaiting_review", r.created), reverse=False)

    def _resolve_run(self, token: str):
        """把用户随手打的一小截认成某个运行。返回 (run, 错误话术)。

        **run id 可以整个省掉**：只有一条在等你批时，`/ok` 就是批它——手机上让人抄
        `prun-content-ops-ed3c577a` 是折磨。多于一条就把候选列出来让人指定，绝不替他挑一个。
        """
        runs = self._live_runs()
        if not runs:
            return None, "现在没有在跑的流水线。"
        if token:
            hit = [r for r in runs if r.run_id.startswith(token) or token in r.run_id]
            if len(hit) == 1:
                return hit[0], ""
            if not hit:
                return None, f"没找到匹配「{token}」的运行。/pipe 看全部。"
            ids = "\n".join(f"· {r.run_id}" for r in hit[:6])
            return None, f"「{token}」匹配到 {len(hit)} 条，说清楚是哪条：\n{ids}"
        waiting = [r for r in runs if r.status == "awaiting_review"]
        if len(waiting) == 1:
            return waiting[0], ""
        if not waiting:
            return None, "现在没有等你批的工序。/pipe 看流水线状态。"
        ids = "\n".join(f"· {r.run_id} 等批：{(r.awaiting().id if r.awaiting() else '?')}"
                         for r in waiting[:6])
        return None, f"有 {len(waiting)} 条在等你批，说清楚是哪条：\n{ids}"

    async def _pipe_list(self, token: str) -> None:
        if token:
            run, err = self._resolve_run(token)
            if run is None:
                await self._safe_send(err)
                return
            await self._safe_send(self._pipe_detail(run))
            return
        runs = self._live_runs()
        if not runs:
            await self._safe_send("现在没有在跑的流水线。")
            return
        lines = []
        for r in runs[:8]:
            mark = "📋" if r.status == "awaiting_review" else ("⛔" if r.status == "failed" else "▶️")
            tail = f" 等批：{r.awaiting().id}" if r.awaiting() is not None else ""
            lines.append(f"{mark} {r.pipeline} · {r.run_id}\n   {r.status}{tail}")
        await self._safe_send("\n".join(lines))

    def _pipe_detail(self, run) -> str:
        """一条运行的细节。带上产出物摘要与外部来源标记——批之前该看见的就在这一屏。"""
        from src.gateway.products import ProductStore
        store = ProductStore(self.repo_root)
        lines = [f"{run.pipeline} · {run.run_id} · {run.status}"]
        for stage in run.stages:
            line = f"  {stage.status} · {stage.id}"
            if stage.attempts > 1:
                line += f"（第 {stage.attempts} 次）"
            lines.append(line)
            if stage.product_id:
                product = store.load(stage.product_id)
                if product is None:
                    lines.append(f"     ↳ 产出物 {stage.product_id} 读不到")
                else:
                    mark = " ⚠外部来源" if product.tainted else ""
                    lines.append(f"     ↳ {product.summary or product.kind}{mark}")
            if stage.note:
                lines.append(f"     ↳ {stage.note}")
        return "\n".join(lines)

    async def _pipe_go(self, token: str) -> None:
        """手动推一道工序。

        存在的理由是**无人值守推不动的那一类**：对外工序在 cron 里连试都不试（没有确认通道），
        通知会说"这一步要你带授权来"——那句话在手机上得有个落点，否则人只能爬去终端。
        这里人就在关口，对外动作会弹按钮问你。
        """
        runs = self._live_runs()
        if token:
            run, err = self._resolve_run(token)
        elif len(runs) == 1:
            run, err = runs[0], ""
        else:
            run, err = None, ("现在没有在跑的流水线。" if not runs else
                              "有多条在跑，说清楚是哪条：\n"
                              + "\n".join(f"· {r.run_id}" for r in runs[:6]))
        if run is None:
            await self._safe_send(err)
            return
        if run.awaiting() is not None:
            await self._safe_send(f"{run.run_id} 停在「{run.awaiting().id}」等你批，"
                                  f"先 /ok 或 /no <意见>。")
            return
        if self._turn_task is not None and not self._turn_task.done():
            await self._safe_send("有活在跑，先等它跑完或 /stop。")
            return
        await self._safe_send(f"▶️ 推进 {run.run_id}…")
        self._start_pipeline_advance(run.run_id)

    async def _pipe_review(self, verdict: str, rest: str) -> None:
        """人批三档。**判定全在这里确定性地做，不经过模型**——批不批是人的意思，
        让模型去理解一句话再决定，等于把人闸换成一次概率事件。
        """
        from src.gateway.pipeline import review as _review

        token, comment = "", rest.strip()
        first = comment.split(maxsplit=1)[0] if comment else ""
        if first:
            candidate, err = self._resolve_run(first)
            # 第一个词只有**真能认出某条运行**时才当 run id，否则整句都是意见：
            # `/no 角度太窄` 里的"角度太窄"不该被当成 id 去查。
            # 但**长得像 run id 就一律当 run id**（哪怕认不出/认出多条）：否则
            # `/ok prun-content`（匹配到两条）会被当成一句意见，转头去批那条唯一在等的——
            # 用户明明指了名，系统却批了另一个，这比报错难查得多。
            if (candidate is not None and not err) or first.startswith("prun"):
                token = first
                comment = comment[len(first):].strip()

        run, err = self._resolve_run(token)
        if run is None:
            await self._safe_send(err)
            return
        if verdict == "reject" and not comment:
            await self._safe_send("驳回要写一句为什么——不说理由，重跑出来还是原样。\n"
                                  "例：/no 角度太窄，换个切入点")
            return

        result = _review(self.repo_root, run.run_id, verdict=verdict,
                         comment=comment, reviewer="im")
        head = {"approve": "✅ 已批", "reject": "↩️ 已驳回", "defer": "⏸ 已挂起"}[verdict]
        await self._safe_send(f"{head} {run.run_id} · {result.reason}")
        if verdict in {"approve", "reject"} and result.status in {"running", "done"}:
            self._start_pipeline_advance(run.run_id)

    def _start_pipeline_advance(self, run_id: str) -> None:
        """批完立刻往下推一道。

        不推的话，你在手机上点了"批"，然后要等下一次 cron（可能两小时）才有动静——
        那不叫联动，那叫留言板。占用与 agent 回合同一个任务位，所以 /stop 一样能停它。
        """
        if self._turn_task is not None and not self._turn_task.done():
            return                                   # 有活在跑：等它跑完，下一次 tick 会接上
        self._turn_task = asyncio.create_task(self._run_pipeline_advance(run_id))

    async def _run_pipeline_advance(self, run_id: str) -> None:
        """推进一道工序，进度/确认按钮/终态都走与 agent 回合同一套事件流泵（`_drain`）。

        这里**给了确认通道**：对外工序会在你手机上弹按钮，而不是像无人值守那样直接拒绝。
        人就在关口——这正是把它接到 IM 上的意义。
        """
        from src.agents.pipeline_exec import build_stage_executor
        from src.gateway.pipeline import advance

        q: asyncio.Queue = asyncio.Queue()
        confirm = self._make_confirm(q)
        self._confirm_holder["fn"] = confirm
        self._progress_holder["fn"] = lambda m: q.put_nowait(("progress", _strip(str(m))))

        async def _inner():
            try:
                execute = build_stage_executor(
                    self.repo_root, confirm=confirm,
                    on_progress=lambda m: q.put_nowait(("progress", _strip(str(m)))))
                result = await advance(self.repo_root, run_id, execute=execute)
                q.put_nowait(("final", f"{result.status}"
                              + (f" · 跑了 {'、'.join(result.ran)}" if result.ran else "")
                              + (f" · 卡在 {result.blocked_on}" if result.blocked_on else "")
                              + (f" · {result.reason}" if result.reason else "")))
            except asyncio.CancelledError:
                q.put_nowait(("final", "（已取消）"))
                raise
            except Exception as e:  # noqa: BLE001
                q.put_nowait(("final", f"（推进出错：{e}）"))
            finally:
                q.put_nowait(("__done__", None))

        await self._drain(q, asyncio.create_task(_inner()))

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
            loop.create_task(self._safe_send(self._task_final_text(task)))
        elif task.status == "running" and task.log:
            now = time.monotonic()
            if now - self._task_prog.get(task.id, 0.0) >= self._progress_interval:
                self._task_prog[task.id] = now
                loop.create_task(self._safe_send(f"🏃 {task.id}: {_strip(task.log[-1])}"))

    def _task_final_text(self, task) -> str:
        """终态通知文案。抽出来是为了可测：深链（P4）配没配、有没有真的附上，得测得到。

        深链只挂在**终态**和**开跑**两处：这是人最想跳去富界面的两个时刻
        （看 diff/依赖图、盯进度）。进度心跳/普通回复不挂——每条都带就成噪音了。
        """
        from src.gateway.public_url import agent_link

        tail = (task.result or task.error or "").strip()[-500:]
        return f"后台任务 {task.id} · {task.status}\n{tail}{agent_link()}"

    async def _submit_task(self, prompt: str) -> None:
        if not prompt:
            await self._safe_send("用法：/task <要后台跑的任务描述>")
            return
        # 共享 runner（serve 内嵌）：kind="im-dev" 让分发路由回本 bridge 的 worker（保住在跑中的
        # 按钮确认 UX）；standalone 自己的 runner worker 就是 _task_worker，kind 只是标注。
        kind = "im-dev" if self._shared_runner else "dev"
        from src.gateway.public_url import agent_link
        task = await self._get_runner().submit(prompt, kind=kind)
        await self._safe_send(f"✅ 已在后台开跑 {task.id}（不占当前会话；进度会自动推、完成发开 PR 按钮）。"
                              f"\n/tasks 看全部后台任务。{agent_link()}")

    async def _list_tasks(self) -> None:
        tasks = self._get_runner().list()
        if not tasks:
            await self._safe_send("暂无后台任务。/task <描述> 开一个。")
            return
        lines = [f"· {t.summary()}" for t in tasks[:10]]
        await self._safe_send("后台任务：\n" + "\n".join(lines))

    # ------------------------------------------------------------ 一个回合（queue+drain+Future）
    async def _run_turn(self, text: str, images: Optional[list] = None,
                        files: Optional[list] = None) -> None:
        q: asyncio.Queue = asyncio.Queue()
        self._confirm_holder["fn"] = self._make_confirm(q)
        self._progress_holder["fn"] = lambda msg: q.put_nowait(("progress", str(msg)))

        def _say(m: str) -> None:
            q.put_nowait(("progress", _strip(m)))

        # run_turn 用 emit 报告"对话出错: …502…"这类死因，正文则由返回值给出。丢掉 emit 的话，
        # 通道故障就退化成一句"（无输出）"——人看不出是网络挂了还是模型没话说（真机复盘）。
        # 只在返回值为空时拿 emit 兜底，正常回复不受影响（否则最终回复会重复发一遍）。
        emitted: list = []

        async def _inner():
            try:
                # 文件走文本告知路径（agent 用 read_file 自己读）；图片走 run_turn 的
                # images 通道（#186 的多模态入口）。**两者都在污点回合内**——kind="im" 的
                # 会话每回合无条件打污点，图里写的指令因此拿不到任何免确认授权。
                prompt = text
                if files:
                    prompt = ((prompt + "\n\n") if prompt else "") + \
                        "（用户随消息发来文件，已存到：\n" + \
                        "\n".join(f"  {f}" for f in files) + "\n用 read_file 读取。）"
                reply = await self.agent.run_turn(prompt, mode=self.mode, say=_say,
                                                  images=list(images or []) or None,
                                                  emit=lambda t: emitted.append(str(t)))
                if not str(reply or "").strip() and emitted:
                    reply = emitted[-1]                  # 死因兜底：把 emit 的错误原文交出去
                q.put_nowait(("final", reply))
            except asyncio.CancelledError:
                q.put_nowait(("final", "（已取消）"))
                raise
            except Exception as e:  # noqa: BLE001
                q.put_nowait(("final", f"（执行出错：{e}）"))
            finally:
                q.put_nowait(("__done__", None))

        await self._drain(q, asyncio.create_task(_inner()))

    async def _drain(self, q: asyncio.Queue, inner) -> None:
        """把一次长任务的 (progress / confirm / final) 事件流推到通道上，收尾清干净。

        抽出来是因为**不止 agent 回合一种长任务**：流水线推进同样要滚动进度、同样可能在
        对外工序上弹确认按钮、同样得在结束时把进度条刷成终态。上面那套节流/增量/编辑的
        通道差异（真机 2026-07-27 逮到的那条）只该有一份实现——复制第二份的下场是
        改了一处、另一处照旧刷屏。
        """
        pid: Optional[str] = None          # 进度消息 id（滚动编辑，防刷屏）
        lines: list = []
        last_edit = 0.0
        sent = 0                           # 已推送到第几行（不支持编辑的通道据此只发增量）
        try:
            while True:
                kind, payload = await q.get()
                if kind == "__done__":
                    break
                if kind == "progress":
                    lines.append(str(payload))
                    pid, last_edit, sent = await self._push_progress(pid, lines, last_edit, sent)
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

    async def _push_progress(self, pid, lines, last_edit, sent=0):
        """推一次进度。返回 (进度消息 id, 上次推送时刻, 已推送到第几行)。

        两种通道语义不同，**不能共用一份文本**（真机 2026-07-27 逮到）：
        - 支持编辑（Telegram）：原地改同一条消息 → 发**累计窗口**，滚动展示最近 12 行。
        - 不支持编辑（钉钉）：`edit_text` 内部是发新消息 → 发累计窗口就是**把说过的话再说一遍**，
          第 N 次推送重复前 N-1 次的全部内容，工具越多重复越长。所以只发**这次的增量**。
        节流被跳过的那几行不会丢：`sent` 没动，下次一并发出去。
        """
        now = time.monotonic()
        if pid is None:
            return await self.adapter.send_text("🏃 " + "\n".join(lines[-12:])), now, len(lines)
        if now - last_edit < self._progress_interval:    # 节流（支持编辑=2s，不支持=10s 防刷屏）
            return pid, last_edit, sent
        if self._edits:
            await self.adapter.edit_text(pid, "🏃 " + "\n".join(lines[-12:]))
            return pid, now, len(lines)
        fresh = lines[sent:]
        if not fresh:
            return pid, last_edit, sent                  # 没有新东西就别发一条空进度
        await self.adapter.edit_text(pid, "🏃 " + "\n".join(fresh[-12:]))
        return pid, now, len(lines)

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

    async def notify_send(self, text: str) -> None:
        """给 owner 推通知——**不吞异常**版（专供 im_runtime.set_owner_notifier 注册）。

        `_safe_send` 吞异常是对的（交互路径上发送失败不该炸回合），但通知投递恰恰相反：
        投递器需要知道"没送到"才能往台账落"未送达"标记。此前注册的是 `_safe_send`，
        发送失败被吞在半路，`notify_owner` 永远报成功——2026-07-28 早上"台账说投了、
        手机没响、查无痕迹"的后半截就是它。
        """
        await self.adapter.send_text(text)

    async def _safe_send(self, text: str) -> None:
        try:
            await self.adapter.send_text(text)
        except Exception as e:  # noqa: BLE001 —— 交互路径：发送失败不炸回合，但要留日志痕迹
            import logging
            logging.getLogger("vortocode.im").warning("IM 发送失败：%s", str(e)[:160])
