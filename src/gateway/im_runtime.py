"""Lightweight runtime hooks shared by web tasks and the embedded IM bridge.

This module intentionally has no dependency on web routers or concrete IM
services. It is the narrow registration point that keeps the task runtime from
knowing whether owner notifications are delivered by Telegram, DingTalk, or
some later channel.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

OwnerNotifier = Callable[[str], Awaitable[object]]

_OWNER_NOTIFIER: OwnerNotifier | None = None


def set_owner_notifier(notifier: OwnerNotifier | None) -> None:
    """Register/unregister the current best-effort owner notification sink."""
    global _OWNER_NOTIFIER
    _OWNER_NOTIFIER = notifier


_OWNER_MEDIA_SENDER = None          # 与 _OWNER_NOTIFIER 同构：桥起来时注册，停时注销


def set_owner_media_sender(sender) -> None:
    """注册"给 owner 发媒体"的出口（签名 async (path, caption, kind) -> bool）。"""
    global _OWNER_MEDIA_SENDER
    _OWNER_MEDIA_SENDER = sender


async def send_owner_media(path: str, caption: str = "", kind: str = "image") -> bool:
    """把一张图/一个文件发给已配对 owner。没有桥或通道不支持 → False（调用方降级成文本）。

    **收件人恒为 owner**，不接受调用方指定——这是它能作为出站面的前提：目标固定，
    与 web_fetch 那种"URL 由模型决定"的真外传通道性质不同。
    """
    if _OWNER_MEDIA_SENDER is None:
        return False
    try:
        return bool(await _OWNER_MEDIA_SENDER(str(path), str(caption), str(kind)))
    except Exception:  # noqa: BLE001 —— 发媒体失败不该掀翻调用方
        return False


def owner_notify_url() -> str:
    """同机持有 IM 的那个服务（本进程没有 IM 桥时，通知交给它转发）。"""
    import os
    return os.getenv("VORTOCODE_OWNER_NOTIFY_URL", "").strip().rstrip("/")


async def relay_owner_notice(text: str, *, source: str = "relay") -> bool:
    """POST 到 owner_notify_url 的 /api/im/notify。对端只在本地投递、不再转发，所以不会成环。"""
    url = owner_notify_url()
    if not url:
        return False
    import os

    import aiohttp

    token = os.getenv("VORTOCODE_API_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), trust_env=False) as client:
            async with client.post(f"{url}/api/im/notify", json={"text": str(text), "source": source},
                                   headers=headers) as response:
                return response.status < 400
    except Exception:  # noqa: BLE001 - notification failures must not break schedulers
        return False


async def notify_owner(text: str, *, relay: bool = True) -> bool:
    """Send a best-effort owner notification: this process's bridge, else the configured relay."""
    if _OWNER_NOTIFIER is None:
        return await relay_owner_notice(text) if relay else False
    try:
        await _OWNER_NOTIFIER(str(text))
        return True
    except Exception:  # noqa: BLE001 - notification failures must not break schedulers
        return False


def start_confirmation_notice(message: str) -> asyncio.Task | None:
    """Remind the owner without granting another channel approval authority.

    The requesting client retains the confirmation future. Its caller cancels
    unfinished delivery when that confirmation closes, preventing late reminders.
    """
    if _OWNER_NOTIFIER is None:
        return None
    text = ("⚠ VortoCode 需要操作确认，请回到发起请求的客户端允许或拒绝。\n"
            "这条消息仅作提醒，在 TG 回复不会批准这项操作。\n" + str(message)[:1000])
    return asyncio.create_task(notify_owner(text))
