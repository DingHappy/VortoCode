"""ChannelAdapter 抽象——bridge 核心只依赖它，各 IM（Telegram/钉钉…）各实现一份。

设计目标：把「一个 IM 通道」收敛成极小接口，bridge 的会话/确认/进度/配对逻辑全通道无关。
新增一个通道 = 实现本抽象的 5 个方法，不碰 bridge。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator


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


class ChannelAdapter:
    """一个 IM 通道的收发接口。实现方负责 transport；bridge 负责编排。"""

    # 通道是否支持"编辑已发消息"（Telegram 支持 → 进度原地滚动；钉钉不支持 → 进度只能发新消息，
    # bridge 据此放慢进度节流、收尾不再多刷一条）。
    edits_supported: bool = True

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

    async def ack_callback(self, event: ChannelEvent) -> None:
        """回执一次按钮点击（如 Telegram answerCallbackQuery，消掉客户端转圈）。best-effort。"""
        raise NotImplementedError

    async def close(self) -> None:
        """释放资源（关 http session 等）。"""
        raise NotImplementedError
