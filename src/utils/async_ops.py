"""Drain a synchronous operation before exposing coroutine cancellation.

Also hosts the Python 3.10 fallbacks for 3.11+ asyncio APIs (timeout / Task.cancelling).
"""
from __future__ import annotations

import asyncio
import weakref
from typing import Awaitable, Callable, Optional, TypeVar

_T = TypeVar("_T")


async def await_thread(function: Callable[..., _T], /, *args, **kwargs) -> _T:
    """Keep the caller's resource ownership until its thread has stopped.

    Cancelling asyncio.to_thread does not stop its thread. Git/tests must finish
    before the caller releases a lock, removes a worktree or reports a pause.
    """
    operation = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        while not operation.done():
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                continue
            except Exception:  # noqa: BLE001 - cancellation takes precedence.
                break
        # Retrieve late failures without turning cancellation into success.
        if not operation.cancelled():
            operation.exception()
        raise


async def run_with_timeout(awaitable: Awaitable[_T], seconds: float) -> _T:
    """asyncio.timeout 是 3.11+；3.10 回落 wait_for，并把 asyncio.TimeoutError 统一成内置 TimeoutError。"""
    timeout = getattr(asyncio, "timeout", None)
    if timeout is not None:
        async with timeout(seconds):
            return await awaitable
    try:
        return await asyncio.wait_for(awaitable, seconds)
    except asyncio.TimeoutError:
        raise TimeoutError() from None


# 3.10 没有 Task.cancelling() 计数：经 request_cancel 发出的取消记在这里，任务回收后自动消失。
_CANCEL_REQUESTED: "weakref.WeakSet[asyncio.Task]" = weakref.WeakSet()


def request_cancel(task: asyncio.Task) -> None:
    """task.cancel()，并让 3.10 上的 cancel_requested 也能看到这次请求。"""
    if not hasattr(task, "cancelling"):
        _CANCEL_REQUESTED.add(task)
    task.cancel()


def cancel_requested(task: Optional[asyncio.Task]) -> bool:
    """任务是否已被请求取消（含已投递但被吞掉的取消）。

    3.11+ 用 Task.cancelling()；3.10 只认经 request_cancel 发出的取消——直接调 task.cancel()
    的看不到，所以要被这里判定的取消必须走 request_cancel。
    """
    if task is None:
        return False
    cancelling = getattr(task, "cancelling", None)
    if cancelling is not None:
        return cancelling() > 0
    return task in _CANCEL_REQUESTED
