"""ChannelAdapter 抽象——bridge 核心只依赖它，各 IM（Telegram/钉钉…）各实现一份。

设计目标：把「一个 IM 通道」收敛成极小接口，bridge 的会话/确认/进度/配对逻辑全通道无关。
新增一个通道 = 实现本抽象的 5 个方法，不碰 bridge。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional


@dataclass
class ChannelEvent:
    """从通道 poll 出来的一个事件（已归一化，与具体 IM 协议无关）。

    **适配器只如实申报事实，不做安全判定**（与 gate.py 同一分工）：`is_group`/`mentioned` 是
    通道能看到的客观事实，「群里没 @ 到就不响应」这条规矩由 bridge 一处执行（`_on_event`），
    否则每加一个通道就得重写一遍门——那正是"加一端漏一端"的老路。

    两个新字段的默认值都取**最保守**的一侧：私聊（is_group=False）不需要 @；一旦通道申报
    is_group=True 而没申报 mentioned，事件就被丢掉。新通道忘了填只会更严，不会更松。
    """
    kind: str                     # "message"（用户发来文本）| "callback"（点了内联按钮）
    sender_id: str = ""           # 发送者 id（白名单用：只认 allow_from 里的人）
    text: str = ""                # kind=message：用户文本
    callback_id: str = ""         # kind=callback：对应哪个确认请求（bridge 生成的 cid）
    approved: bool = False        # kind=callback：批准/拒绝
    ack: object = None            # kind=callback：通道侧回执令牌（如 Telegram callback_query.id），交回 ack_callback
    is_group: bool = False        # 该事件是否来自群聊/多人会话（私聊=False）
    mentioned: bool = False       # 群聊里本条是否**显式 @ 了本机器人**（私聊无意义）
    reply_to: object = None       # 本条消息的通道侧回复路由令牌（如钉钉 sessionWebhook）——只申报，
                                  # 采纳与否由 bridge 过闸后调 commit_reply_target 决定
    images: list = field(default_factory=list)   # 随消息带来的**图片**本地路径（已下载落盘）
    files: list = field(default_factory=list)    # 随消息带来的**文件**本地路径
    unsupported: str = ""         # 通道认出了附件但取不到（下载失败/类型不支持）→ 如实告知用户，
                                  # 绝不静默丢弃（"发过去石沉大海"是最差的体验，2026-07-26 的老毛病）


class ChannelAdapter:
    """一个 IM 通道的收发接口。实现方负责 transport；bridge 负责编排。"""

    # 通道是否支持"编辑已发消息"（Telegram 支持 → 进度原地滚动；钉钉不支持 → 进度只能发新消息，
    # bridge 据此放慢进度节流、收尾不再多刷一条）。
    edits_supported: bool = True

    # ------------------------------------------------------------ 活性（谁都别自己发明一套）
    #
    # 2026-07-28 的事故是"**发不出去**"，它的镜像盲区是"**连接死了收不到**"：长连一旦彻底断掉，
    # poll 循环自己退避重连（这是对的），但**没有任何面能看出来**——表现又是"机器人装死"，
    # 而这套系统里"装死"是最贵的故障。所以活性计数上收到基类一份，两个通道只管在
    # 收到帧/重连时打点，怎么判"多久算死"由读的人决定。
    #
    # 用 monotonic 记时刻：墙钟会被改（昨晚刚给 VM 改过时区），拿它算"多久没动静"会算出负数。

    def _lv(self) -> dict:
        """活性状态（惰性建，适配器不必在 __init__ 里 super()）。"""
        st = getattr(self, "_lv_state", None)
        if st is None:
            st = {"connected": False, "reconnects": 0, "last_frame": None,
                  "connected_since": None, "last_error": ""}
            self._lv_state = st
        return st

    def note_connected(self) -> None:
        """建连成功。第二次及以后计入 reconnects（首连不算重连）。"""
        st = self._lv()
        if st["connected_since"] is not None or st["reconnects"] or st["last_frame"] is not None:
            st["reconnects"] += 1
        st["connected"] = True
        st["connected_since"] = time.monotonic()

    def note_frame(self) -> None:
        """**收到任何一帧**（含心跳 ping / 空的长轮询返回）——这才是"线还活着"的证据。

        刻意不是"收到用户消息"：主人一夜不说话是常态，拿它当活性会天天误报。
        """
        st = self._lv()
        st["last_frame"] = time.monotonic()
        st["connected"] = True

    def note_disconnected(self, why: str = "") -> None:
        st = self._lv()
        st["connected"] = False
        st["connected_since"] = None
        if why:
            st["last_error"] = str(why)[:200]

    def liveness(self) -> dict:
        """活性快照（**不含任何凭证/会话标识**，可以安全经 API 吐给运维面）。

        `*_age` 是秒龄，None = 从没发生过。判"多久算死"交给调用方（doctor / 决策队列），
        这里只如实报事实。
        """
        st = self._lv()
        now = time.monotonic()

        def _age(t: Optional[float]) -> Optional[float]:
            return None if t is None else round(max(0.0, now - t), 1)

        return {"connected": bool(st["connected"]),
                "reconnects": int(st["reconnects"]),
                "last_frame_age": _age(st["last_frame"]),
                "connected_age": _age(st["connected_since"]),
                "last_error": st["last_error"]}

    async def poll(self) -> AsyncIterator[ChannelEvent]:
        """长轮询/长连接，持续 yield 归一化事件（纯出站）。断线自行退避重连，不抛给 bridge。"""
        raise NotImplementedError
        yield  # pragma: no cover  （让类型上是 async generator）

    async def send_text(self, text: str) -> str:
        """发一条文本消息，返回 message_id（供后续 edit_text 滚动更新进度）。"""
        raise NotImplementedError

    async def edit_text(self, message_id: str, text: str) -> None:
        """编辑已发出的消息（进度滚动更新，避免刷屏）。失败 best-effort 吞掉。"""
        raise NotImplementedError

    async def send_confirm(self, text: str, callback_id: str) -> None:
        """发一条带 [✅ 批准 | ❌ 拒绝] 内联按钮的消息；按钮回传 callback_id（bridge 据此匹配 Future）。"""
        raise NotImplementedError

    async def send_image(self, path: str, caption: str = "") -> bool:
        """发一张图片。返回 True=已发出；False=本通道不支持（调用方据此降级成文本，别当失败）。

        为什么需要它：agent 已经能无头截网页、把 Word/PPT 转成图（2026-07-26 在 VM 里验通），
        但**送不到人手机上**——能力做出来了、交付通道是断的。审批一段长 diff 时，一张渲染好的
        图远比一大段文本可读。

        默认"不支持"而不是抛异常：新通道不实现也不会把 bridge 打挂，只是退回文本。
        """
        return False

    async def send_file(self, path: str, caption: str = "") -> bool:
        """发一个文件（同 send_image 的约定：False = 本通道不支持，不是失败）。"""
        return False

    async def ack_callback(self, event: ChannelEvent) -> None:
        """回执一次按钮点击（如 Telegram answerCallbackQuery，消掉客户端转圈）。best-effort。"""
        raise NotImplementedError

    def commit_reply_target(self, event: ChannelEvent) -> None:
        """采纳一条**已过入站闸**事件的回复路由（bridge 在三道闸之后调用，是唯一调用点）。

        默认无路由状态可更新——固定回 owner 的通道（Telegram）不用实现。有会话级路由的通道
        （钉钉 sessionWebhook）**只能**在这里更新回复目标，绝不在 poll/收帧阶段更新：否则
        白名单外的任何一条入站消息都能把后续回复劫到自己的会话（内容外泄 + 把主人的确认打聋）。
        """

    async def close(self) -> None:
        """释放资源（关 http session 等）。"""
        raise NotImplementedError
