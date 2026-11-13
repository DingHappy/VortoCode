"""One-shot entry over an explicitly supplied trusted binding.

No registry, process pool, task store, permission policy, retry or fallback.
Session control retains its separate lifecycle contract until migrated.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Generic, TypeVar

from src.agents.backends.execution import ExecutionRef

T = TypeVar('T')


def _features(value):
    if not isinstance(value, frozenset) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError('execution features must be an immutable set of names')


@dataclass(frozen=True)
class OneShotRequest:
    ref: ExecutionRef
    prompt: str
    timeout: float
    required_features: frozenset[str] = frozenset({'execution.run'})

    def __post_init__(self):
        if not isinstance(self.ref, ExecutionRef) or not isinstance(self.prompt, str):
            raise ValueError('invalid execution request')
        try:
            valid = (not isinstance(self.timeout, bool) and isinstance(self.timeout, (int, float))
                     and math.isfinite(self.timeout) and self.timeout > 0)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError('one-shot execution requires a finite positive timeout')
        _features(self.required_features)


@dataclass(frozen=True)
class ExecutionBinding(Generic[T]):
    backend_id: str
    features: frozenset[str]
    invoke: Callable[[OneShotRequest], Awaitable[T]] = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.backend_id, str) or not self.backend_id.strip() or not callable(self.invoke):
            raise ValueError('invalid trusted execution binding')
        _features(self.features)


async def execute_once(binding: ExecutionBinding[T], request: OneShotRequest, *, check: Callable[[], None]) -> T:
    """Delegate exactly once, keeping authority checks in the original host.

    Features describe support, never approval or availability. The host supplies
    the implementation directly; no remote name is used to load a program.
    """
    if request.ref.backend_id != binding.backend_id:
        raise ValueError('execution identity does not match the trusted binding')
    if request.ref.session_id or request.ref.turn_id:
        raise ValueError('one-shot execution cannot resume an existing session or turn')
    if 'execution.run' not in binding.features or not request.required_features <= binding.features:
        raise ValueError('execution binding does not support the required capabilities')
    check()
    result = await binding.invoke(request)
    check()
    return result
