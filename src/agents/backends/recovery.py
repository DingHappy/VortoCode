"""Pure recovery decisions over trusted execution observations; never schedules work."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.agents.backends.execution import ExecutionRef, ExecutionSnapshot, ExecutionState


@dataclass(frozen=True)
class RecoveryDecision:
    action: Literal["fallback", "inspect", "fail"]
    reason: str


class RecoveryRequired(RuntimeError):
    """Stop automatic repair/review retries and retain the attempt for inspection."""

    def __init__(self, message: str, snapshot: ExecutionSnapshot, workspace: str = ""):
        super().__init__(message)
        self.snapshot = snapshot
        self.workspace = workspace

    def __str__(self) -> str:
        message = super().__str__()
        return message + (f"；保留工作区：{self.workspace}" if self.workspace else "")


def decide_failure_recovery(snapshot: ExecutionSnapshot | None, *, expected_ref: ExecutionRef,
                            unavailable: bool) -> RecoveryDecision:
    """Availability text never overrides identity, state or possible side effects."""
    if not isinstance(snapshot, ExecutionSnapshot) or snapshot.ref != expected_ref:
        return RecoveryDecision("inspect", "identity_unconfirmed")
    if snapshot.state != ExecutionState.FAILED:
        return RecoveryDecision("inspect", "terminal_failure_unconfirmed")
    if snapshot.error is None or snapshot.error.effect != "not_started":
        return RecoveryDecision("inspect", "effects_unconfirmed")
    if snapshot.result is not None:
        return RecoveryDecision("inspect", "conflicting_result")
    if snapshot.error.retryable is False:
        return RecoveryDecision("fail", "retry_disallowed")
    if unavailable is not True:
        return RecoveryDecision("fail", "not_unavailable")
    return RecoveryDecision("fallback", "not_started_unavailable")
