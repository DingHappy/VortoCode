"""后台任务事件桥。

tasks 路由负责后台任务运行时，realtime 路由负责 WebSocket 协议。二者都只依赖本模块，
避免路由之间互相导入形成循环依赖。
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from src.gateway import protocol as P
from src.web.state import manager

_snapshot_provider: Callable[[], list] | None = None
_session_event_publisher: Callable[[str, dict], Awaitable[None]] | None = None


def set_task_snapshot_provider(provider: Callable[[], list] | None) -> None:
    """登记任务快照读取函数（由 tasks 路由设置；测试可置空）。"""
    global _snapshot_provider
    _snapshot_provider = provider


def set_session_event_publisher(
    publisher: Callable[[str, dict], Awaitable[None]] | None,
) -> None:
    """登记会话级事件泵；由 realtime 提供，避免 tasks 路由反向导入它。"""
    global _session_event_publisher
    _session_event_publisher = publisher


def task_snapshot() -> list:
    """返回当前后台任务快照；未登记/出错时安全降级为空列表。"""
    if _snapshot_provider is None:
        return []
    try:
        return list(_snapshot_provider())
    except Exception:  # noqa: BLE001
        return []


def broadcast_task_update(task: dict) -> None:
    """把一条后台任务状态变更广播给所有连着的 WS 客户端（best-effort）。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(manager.broadcast(P.make_event(P.TASK_UPDATE, data=task)))


def publish_task_handoff(session: str, task: dict) -> None:
    """把终态任务交接投递给所属会话，并进入该会话的持久 cursor journal。"""
    if not session or _session_event_publisher is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_session_event_publisher(
        session,
        P.make_event(P.TASK_HANDOFF, data=task),
    ))


def broadcast_notice(text: str) -> None:
    """把一条后台通知（cron 结果 / heartbeat 发现）广播给所有连着的 WS 客户端（best-effort）。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(manager.broadcast(P.make_event(P.NOTICE, data={"text": str(text)[:2000]})))
