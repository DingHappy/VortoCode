"""Drain a synchronous operation before exposing coroutine cancellation.

Also hosts the Python 3.10 fallbacks for 3.11+ asyncio APIs (timeout / Task.cancelling).
"""
from __future__ import annotations

import asyncio
import weakref
from typing import Any, AsyncContextManager, Awaitable, Callable, Optional, TypeVar

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



class _TimeoutScope310:
    """3.10 版 asyncio.timeout：到点取消当前任务，在块出口把这次取消换成内置 TimeoutError。

    与 3.11+ 的差别：到点与外部取消恰好同时发生时分不清，按超时处理（与 async-timeout 库同限）。
    """

    def __init__(self, seconds: Optional[float]) -> None:
        self._seconds = seconds
        self._expired = False
        self._handle: Optional[asyncio.TimerHandle] = None

    def _expire(self, task: asyncio.Task) -> None:
        self._expired = True
        task.cancel()

    async def __aenter__(self) -> "_TimeoutScope310":
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("timeout_scope() must be used inside a task")
        if self._seconds is not None:
            self._handle = asyncio.get_running_loop().call_later(
                max(self._seconds, 0), self._expire, task)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if self._handle is not None:
            self._handle.cancel()
        if self._expired and exc_type is asyncio.CancelledError:
            raise TimeoutError() from exc
        # 3.11 起 asyncio.TimeoutError 就是内置 TimeoutError；3.10 是两个类，块内（如 aiohttp 自带超时）
        # 抛出的统一成内置类，调用方按 3.11 语义 except TimeoutError 才接得住。
        if exc_type is asyncio.TimeoutError and not issubclass(asyncio.TimeoutError, TimeoutError):
            raise TimeoutError() from exc
        return False


def timeout_scope(seconds: Optional[float]) -> AsyncContextManager[Any]:
    """`async with timeout_scope(s):` 等价 3.11+ 的 `async with asyncio.timeout(s):`，3.10 也可用。"""
    timeout = getattr(asyncio, "timeout", None)
    if timeout is not None:
        return timeout(seconds)
    return _TimeoutScope310(seconds)

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
