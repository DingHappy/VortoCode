"""Minimal control contract for existing sessions, independent of model/transport.

This is not an execution pool or session store. The host owns authorization and
selects a backend; the backend retains its native context, tools and approvals.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from src.agents.backends.execution import ExecutionRef, ExecutionSnapshot, ExecutionState

Operation = Literal["start", "steer", "interrupt"]


class BackendControlError(RuntimeError):
    """An operation was refused or its receipt cannot be verified."""


class ControlOutcomeUnknown(BackendControlError):
    """The request may have reached the core, but its receipt cannot be verified.

    Distinct from a refusal: the caller must not report it as "not sent", and must not retry it."""


@dataclass(frozen=True)
class SessionState:
    backend_id: str
    session_id: str
    turn_id: str
    phase: Literal["running", "idle", "unknown"]
    execution: ExecutionSnapshot | None = None


@dataclass(frozen=True)
class ControlReceipt:
    """Backend acceptance only, never evidence of task completion or cancellation."""
    backend_id: str
    session_id: str
    turn_id: str
    operation: Operation
    execution_ref: ExecutionRef | None = None


class SessionControlBackend(Protocol):
    backend_id: str
    operations: frozenset[Operation]

    async def read_state(self, session_id: str) -> SessionState: ...
    async def start_turn(self, session_id: str, text: str) -> ControlReceipt: ...
    async def steer_turn(self, session_id: str, turn_id: str, text: str) -> ControlReceipt: ...
    async def interrupt_turn(self, session_id: str, turn_id: str) -> ControlReceipt: ...


async def control_session(backend: SessionControlBackend, session_id: str, *,
                          action: Literal["message", "interrupt"], text: str = "",
                          expected_turn_id: str | None = None) -> ControlReceipt:
    """Execute one host-approved action against fresh, precisely scoped state.

    No retry, fallback backend, permission override, or implicit session creation.
    Idle-to-start has no universal compare-and-swap: adapters must not claim one.
    """
    if not session_id or action not in {"message", "interrupt"}:
        raise BackendControlError("会话或操作类型无效，未执行")
    if action == "message" and not text.strip():
        raise BackendControlError("要发的话是空的")
    needed = {"interrupt"} if action == "interrupt" else {"start", "steer"}
    if not needed.intersection(backend.operations):
        raise BackendControlError("当前核心不支持这项操作")
    state = await backend.read_state(session_id)
    if state.backend_id != backend.backend_id or state.session_id != session_id:
        raise BackendControlError("运行端返回的会话身份不匹配，未执行")
    if state.execution is not None:
        snapshot = state.execution
        normalized_phase = ("running" if snapshot.state in {ExecutionState.RUNNING, ExecutionState.WAITING_USER}
                            else "idle" if snapshot.state in {ExecutionState.FINISHED, ExecutionState.FAILED, ExecutionState.CANCELLED}
                            else "unknown")
        if (snapshot.ref.backend_id != backend.backend_id or snapshot.ref.session_id != session_id
                or snapshot.ref.turn_id != state.turn_id or normalized_phase != state.phase
                or (getattr(backend, "instance_id", snapshot.ref.instance_id) != snapshot.ref.instance_id)):
            raise BackendControlError("执行来源或状态不匹配，未执行")
    if expected_turn_id is not None and state.turn_id != expected_turn_id:
        raise BackendControlError("原轮次已经结束或变化，未操作新的这一轮")
    if state.phase not in {"running", "idle"} or (state.phase == "running" and not state.turn_id):
        raise BackendControlError("无法确认当前运行状态，未执行")
    operation: Operation = ("interrupt" if action == "interrupt" else
                            "steer" if state.phase == "running" else "start")
    if operation not in backend.operations:
        raise BackendControlError("当前核心不支持这项操作")
    if operation == "interrupt":
        if state.phase != "running":
            raise BackendControlError("它已经停了")
        receipt = await backend.interrupt_turn(session_id, state.turn_id)
    elif operation == "steer":
        receipt = await backend.steer_turn(session_id, state.turn_id, text.strip())
    else:
        receipt = await backend.start_turn(session_id, text.strip())
    if (receipt.backend_id != backend.backend_id or receipt.session_id != session_id
            or receipt.operation != operation or not receipt.turn_id
            or (operation != "start" and receipt.turn_id != state.turn_id)):
        raise ControlOutcomeUnknown("请求可能已送达，但回执身份不匹配；请先核对会话，不要重复发送")
    if receipt.execution_ref is not None:
        ref = receipt.execution_ref
        if (ref.backend_id != backend.backend_id or ref.session_id != session_id or ref.turn_id != receipt.turn_id
                or (state.execution is not None and (ref.instance_id != state.execution.ref.instance_id
                    or ref.scope != state.execution.ref.scope
                    or (operation != "start" and ref.key != state.execution.ref.key)))):
            raise ControlOutcomeUnknown("请求可能已送达，但执行来源回执不匹配；请先核对会话，不要重复发送")
    return receipt
