"""Lightweight runtime hooks shared by web tasks and the embedded IM bridge.

This module intentionally has no dependency on web routers or concrete IM
services. It is the narrow registration point that keeps the task runtime from
knowing whether owner notifications are delivered by Telegram, DingTalk, or
some later channel.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

OwnerNotifier = Callable[[str], Awaitable[object]]

_OWNER_NOTIFIER: OwnerNotifier | None = None


def set_owner_notifier(notifier: OwnerNotifier | None) -> None:
    """Register/unregister the current best-effort owner notification sink."""
    global _OWNER_NOTIFIER
    _OWNER_NOTIFIER = notifier


async def notify_owner(text: str) -> bool:
    """Send a best-effort owner notification if a bridge registered one."""
    if _OWNER_NOTIFIER is None:
        return False
    try:
        await _OWNER_NOTIFIER(str(text))
        return True
    except Exception:  # noqa: BLE001 - notification failures must not break schedulers
        return False
