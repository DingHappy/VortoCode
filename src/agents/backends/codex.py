"""Codex App Server adapter over an injected, already authenticated RPC client.

No process launch, credentials, polling, model calls or approval decisions here.
The observer owns the existing socket connection and action authorization.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Literal, Protocol

from src.agents.backends.control import (
    BackendControlError, ControlOutcomeUnknown, ControlReceipt, Operation, SessionState,
)
from src.agents.backends.execution import (
    ExecutionProgress, ExecutionRef, ExecutionSnapshot, ExecutionState, process_instance,
)


class RPCCaller(Protocol):
    async def call(self, method: str, params: dict) -> Any: ...


def codex_execution_state(status: str, turn_status: str, flags: list[str]) -> ExecutionState:
    """Only mutually consistent native observations establish an execution state."""
    if status == "active" and turn_status == "inProgress":
        return (ExecutionState.WAITING_USER if any(flag in flags for flag in
                ("waitingOnApproval", "waitingOnUserInput")) else ExecutionState.RUNNING)
    if status == "idle":
        return {"completed": ExecutionState.FINISHED, "failed": ExecutionState.FAILED,
                "interrupted": ExecutionState.CANCELLED}.get(turn_status, ExecutionState.UNKNOWN)
    return ExecutionState.UNKNOWN


class CodexBackend:
    backend_id = "codex"
    operations: frozenset[Operation] = frozenset({"start", "steer", "interrupt"})

    def __init__(self, client: RPCCaller, *, instance_id: str | None = None):
        if instance_id is not None and (not isinstance(instance_id, str) or not instance_id.strip()):
            raise ValueError("instance_id must be a nonempty host-owned identity")
        self.client = client
        self.instance_id = instance_id or f"{process_instance('codex.appserver')}:{id(client)}"
        self.identity_scope = "runtime" if instance_id else "process"
        self.execution_report: ExecutionSnapshot | None = None
        self._read_generation = 0

    def _ref(self, session_id: str, turn_id: str) -> ExecutionRef:
        return ExecutionRef(self.backend_id, self.instance_id, turn_id,
                            session_id=session_id, turn_id=turn_id, scope=self.identity_scope)

    async def read_state(self, session_id: str) -> SessionState:
        # A pending or failed read cannot keep presenting an earlier success.
        # Concurrent reads return their own state, but only the latest request
        # may publish the adapter's current observation.
        self._read_generation += 1
        generation = self._read_generation
        self.execution_report = None
        result = await self.client.call("thread/read", {"threadId": session_id})
        thread = result.get("thread") if isinstance(result, dict) else None
        if not isinstance(thread, dict) or thread.get("id") != session_id:
            raise BackendControlError("Codex 返回的会话身份不匹配，未执行")
        # Only status is needed for control: do not fetch full conversation/items.
        page = await self.client.call("thread/turns/list", {
            "threadId": session_id, "limit": 1, "itemsView": "notLoaded", "sortDirection": "desc"})
        turns = page.get("data") if isinstance(page, dict) else None
        if not isinstance(turns, list) or any(not isinstance(turn, dict) for turn in turns):
            raise BackendControlError("Codex 返回的轮次状态无法核对，未执行")
        turn = turns[0] if turns else {}
        turn_id = turn.get("id", "")
        if not isinstance(turn_id, str) or (turns and not turn_id):
            raise BackendControlError("Codex 返回的轮次身份无法核对，未执行")
        status = thread.get("status")
        kind = status.get("type") if isinstance(status, dict) else None
        phase: Literal["running", "idle", "unknown"] = "unknown"
        if kind == "active" and turn.get("status") == "inProgress" and turn_id:
            phase = "running"
        elif kind == "idle" and (not turns or turn.get("status") in {"completed", "failed", "interrupted"}):
            phase = "idle"
        snapshot = None
        if turn_id:
            flags = status.get("activeFlags") or [] if isinstance(status, dict) else []
            state = codex_execution_state(str(kind), str(turn.get("status") or ""), flags)
            snapshot = ExecutionSnapshot(self._ref(session_id, turn_id), state, time.time(),
                native_state=f"{kind}:{turn.get('status', '')}",
                progress=ExecutionProgress("等待 Codex 原生批准或回答" if state == ExecutionState.WAITING_USER else ""),
                features=frozenset({"execution.observe", "control.start", "control.steer", "control.interrupt"}))
        if generation == self._read_generation:
            self.execution_report = snapshot
        return SessionState(self.backend_id, session_id, turn_id, phase, snapshot)

    async def _send(self, operation: Operation, session_id: str, *, turn_id: str = "",
                    text: str = "") -> ControlReceipt:
        if not session_id or (operation != "start" and not turn_id) or (operation != "interrupt" and not text.strip()):
            raise BackendControlError("Codex 操作缺少会话、轮次或正文，未执行")
        params: dict = {"threadId": session_id}
        if operation == "interrupt":
            params["turnId"] = turn_id
        else:
            params["input"] = [{"type": "text", "text": text, "text_elements": []}]
            if operation == "steer":
                params["expectedTurnId"] = turn_id
        try:
            result = await self.client.call(f"turn/{operation}", params)
        except (TimeoutError, asyncio.TimeoutError) as error:
            raise ControlOutcomeUnknown("Codex 响应超时，发送结果无法确认；请先核对会话，不要重复发送") from error
        except RuntimeError:
            raise                      # Codex 回了错误：明确拒绝，没有执行
        except Exception as error:     # noqa: BLE001 —— 发出后连接断了：可能已送达
            raise ControlOutcomeUnknown("发送时与 Codex 的连接中断，结果无法确认；请先核对会话，不要重复发送") from error
        if not isinstance(result, dict):
            raise ControlOutcomeUnknown("请求可能已送达，但 Codex 回执无法确认；请先核对会话，不要重复发送")
        if operation == "start":
            turn = result.get("turn")
            accepted_turn = turn.get("id") if isinstance(turn, dict) else None
        elif operation == "steer":
            accepted_turn = result.get("turnId")
        else:
            accepted_turn = turn_id  # {} acknowledges the request, not completed cancellation.
        if not isinstance(accepted_turn, str) or not accepted_turn or (operation != "start" and accepted_turn != turn_id):
            raise ControlOutcomeUnknown("请求可能已送达，但 Codex 轮次回执不匹配；请先核对会话，不要重复发送")
        return ControlReceipt(self.backend_id, session_id, accepted_turn, operation,
                              self._ref(session_id, accepted_turn))

    async def start_turn(self, session_id: str, text: str) -> ControlReceipt:
        return await self._send("start", session_id, text=text)

    async def steer_turn(self, session_id: str, turn_id: str, text: str) -> ControlReceipt:
        return await self._send("steer", session_id, turn_id=turn_id, text=text)

    async def interrupt_turn(self, session_id: str, turn_id: str) -> ControlReceipt:
        return await self._send("interrupt", session_id, turn_id=turn_id)
