"""Drain a synchronous operation before exposing coroutine cancellation."""
from __future__ import annotations

import asyncio
from typing import Callable, TypeVar

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
