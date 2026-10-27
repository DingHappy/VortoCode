"""Ephemeral activity counters for a supervised runtime's shutdown preflight.

The owners keep their existing work queues. This module stores no tasks, text,
credentials or shutdown authority, and does not grant an atomic shutdown lease.
"""
from contextlib import contextmanager

_writes = 0


@contextmanager
def track_runtime_write():
    global _writes
    _writes += 1
    try:
        yield
    finally:
        _writes -= 1


def runtime_write_count() -> int:
    return _writes
