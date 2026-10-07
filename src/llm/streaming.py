"""Stream cleanup and an explicit interrupt distinct from display failures."""
from __future__ import annotations

import inspect
import logging
from typing import Callable


class StreamInterrupted(Exception):
    def __init__(self, reason: Exception):
        super().__init__(str(reason))
        self.reason = reason


def checked_callback(callback: Callable[[str], None] | None, check: Callable[[], None] | None):
    if check is None:
        return callback

    def emit(delta: str) -> None:
        try:
            check()
        except Exception as error:
            raise StreamInterrupted(error) from error
        if callback is not None:
            callback(delta)

    return emit


async def close_stream(stream) -> None:
    close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
    if close is not None:
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - preserve the original execution failure.
            logging.getLogger(__name__).warning("LLM stream cleanup failed", exc_info=True)
