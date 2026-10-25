"""Backend-control contracts use fake RPC only; no live agent is operated."""
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from src.agents.backends.codex import CodexBackend
from src.agents.backends.control import (
    BackendControlError, ControlReceipt, SessionState, control_session,
)
from src.agents.backends.execution import ExecutionRef, ExecutionSnapshot, ExecutionState


class RPC:
    def __init__(self, phase="running", turn_id="t1"):
        self.calls = []
        self.thread = {"id": "s1", "status": {"type": "active" if phase == "running" else phase}}
        self.turns = [{"id": turn_id, "status": "inProgress" if phase == "running" else "completed"}]
        self.response = {"start": {"turn": {"id": "t2"}}, "steer": {"turnId": turn_id}, "interrupt": {}}

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": self.thread}
        if method == "thread/turns/list":
            return {"data": self.turns}
        result = self.response[method.removeprefix("turn/")]
        if isinstance(result, BaseException):
            raise result
        return result

    @property
    def writes(self):
        return [(method, params) for method, params in self.calls if method.startswith("turn/")]


@pytest.mark.parametrize(("phase", "action", "operation"), [
    ("running", "message", "steer"), ("idle", "message", "start"),
    ("running", "interrupt", "interrupt"),
])
async def test_codex_operations_use_fresh_state_and_preserve_native_permission_settings(phase, action, operation):
    rpc = RPC(phase)
    receipt = await control_session(CodexBackend(rpc), "s1", action=action, text=" continue ", expected_turn_id="t1")
    assert receipt.operation == operation and receipt.backend_id == "codex"
    assert [method for method, _ in rpc.calls] == ["thread/read", "thread/turns/list", f"turn/{operation}"]
    assert rpc.calls[1][1]["itemsView"] == "notLoaded"
    params = rpc.writes[0][1]
    assert set(params) <= {"threadId", "input", "expectedTurnId", "turnId"}
    if operation == "steer":
        assert params["expectedTurnId"] == "t1"
    elif operation == "interrupt":
        assert params["turnId"] == "t1"
        assert not hasattr(receipt, "completed")
    if action == "message":
        assert params["input"][0]["text"] == "continue"


async def test_stale_actions_never_operate_on_a_new_turn():
    rpc = RPC(turn_id="new")
    with pytest.raises(BackendControlError, match="轮次"):
        await control_session(CodexBackend(rpc), "s1", action="message", text="continue", expected_turn_id="old")
    assert not rpc.writes


async def test_idle_session_with_no_previous_turn_can_start():
    rpc = RPC("idle")
    rpc.turns = []
    receipt = await control_session(CodexBackend(rpc), "s1", action="message", text="start")
    assert receipt.turn_id == "t2" and receipt.operation == "start"


@pytest.mark.parametrize("fault", ["thread identity", "turn identity", "bad data", "contradictory state", "unknown state"])
async def test_unverifiable_states_never_trigger_writes(fault):
    rpc = RPC()
    if fault == "thread identity":
        rpc.thread["id"] = "other"
    elif fault == "turn identity":
        rpc.turns[0]["id"] = ""
    elif fault == "bad data":
        rpc.turns = None
    elif fault == "contradictory state":
        rpc.thread["status"]["type"] = "idle"
    else:
        rpc.thread["status"]["type"] = "unrecognized"
    with pytest.raises(BackendControlError):
        await control_session(CodexBackend(rpc), "s1", action="message", text="continue")
    assert not rpc.writes


@pytest.mark.parametrize("response", [None, {}, {"turnId": "other"}])
async def test_bad_steering_receipts_are_uncertain_and_never_retried(response):
    rpc = RPC()
    rpc.response["steer"] = response
    with pytest.raises(BackendControlError, match="不要重复发送"):
        await control_session(CodexBackend(rpc), "s1", action="message", text="continue")
    assert len(rpc.writes) == 1


async def test_send_timeout_is_not_reported_as_success_and_is_not_retried():
    rpc = RPC()
    rpc.response["steer"] = TimeoutError()
    with pytest.raises(BackendControlError, match="结果无法确认"):
        await control_session(CodexBackend(rpc), "s1", action="message", text="continue")
    assert len(rpc.writes) == 1


async def test_cancelled_control_keeps_cancellation_semantics():
    rpc = RPC()
    rpc.response["steer"] = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await control_session(CodexBackend(rpc), "s1", action="message", text="continue")
    assert len(rpc.writes) == 1


async def test_idle_turn_cannot_be_interrupted():
    rpc = RPC("idle")
    with pytest.raises(BackendControlError, match="已经停了"):
        await control_session(CodexBackend(rpc), "s1", action="interrupt")
    assert not rpc.writes


class AlternateBackend:
    """Different core contract with no Codex RPC names or model dependency."""
    backend_id = "alternate"
    operations = frozenset({"start", "steer", "interrupt"})

    def __init__(self):
        self.state = SessionState(self.backend_id, "s1", "t1", "running")
        self.actions = []

    async def read_state(self, session_id):
        return self.state

    async def start_turn(self, session_id, text):
        self.actions.append(("start", text))
        return ControlReceipt(self.backend_id, session_id, "next", "start")

    async def steer_turn(self, session_id, turn_id, text):
        self.actions.append(("steer", text))
        return ControlReceipt(self.backend_id, session_id, turn_id, "steer")

    async def interrupt_turn(self, session_id, turn_id):
        self.actions.append(("interrupt", turn_id))
        return ControlReceipt(self.backend_id, session_id, turn_id, "interrupt")


async def test_same_controller_works_without_codex_or_main_agent():
    backend = AlternateBackend()
    receipt = await control_session(backend, "s1", action="message", text="update")
    assert receipt.backend_id == "alternate" and backend.actions == [("steer", "update")]
    backend.state = replace(backend.state, phase="idle")
    receipt = await control_session(backend, "s1", action="message", text="next")
    assert receipt.operation == "start"


async def test_unsupported_backend_operations_fail_closed():
    backend = AlternateBackend()
    backend.operations = frozenset({"interrupt"})
    with pytest.raises(BackendControlError, match="不支持"):
        await control_session(backend, "s1", action="message", text="update")
    assert not backend.actions


async def test_a_backend_cannot_return_a_different_sessions_state():
    backend = AlternateBackend()
    backend.state = replace(backend.state, session_id="other")
    with pytest.raises(BackendControlError, match="身份"):
        await control_session(backend, "s1", action="interrupt")
    assert not backend.actions


async def test_wrong_receipt_identity_is_uncertain_and_never_replayed():
    class WrongReceipt(AlternateBackend):
        async def steer_turn(self, session_id, turn_id, text):
            self.actions.append(("steer", text))
            return ControlReceipt("other", session_id, turn_id, "steer")
    backend = WrongReceipt()
    with pytest.raises(BackendControlError, match="不要重复发送"):
        await control_session(backend, "s1", action="message", text="update")
    assert backend.actions == [("steer", "update")]


async def test_codex_reports_a_scoped_wait_and_interrupt_receipt_never_finishes_it():
    rpc = RPC()
    rpc.thread["status"]["activeFlags"] = ["waitingOnApproval"]
    backend = CodexBackend(rpc, instance_id="mac/runtime")
    state = await backend.read_state("s1")
    assert state.execution.state == ExecutionState.WAITING_USER
    assert state.execution.ref.scope == "runtime" and state.execution.result is None
    receipt = await control_session(backend, "s1", action="interrupt")
    assert receipt.execution_ref == state.execution.ref
    assert backend.execution_report.state == ExecutionState.WAITING_USER


async def test_source_identity_mismatch_in_normalized_state_blocks_before_writing():
    backend = AlternateBackend()
    backend.instance_id = "host-A"
    report = ExecutionSnapshot(ExecutionRef("alternate", "host-B", "t1", session_id="s1", turn_id="t1"),
                               ExecutionState.RUNNING, 1)
    backend.state = replace(backend.state, execution=report)
    with pytest.raises(BackendControlError, match="来源"):
        await control_session(backend, "s1", action="interrupt")
    assert not backend.actions


async def test_legacy_and_normalized_state_cannot_disagree():
    backend = AlternateBackend()
    report = ExecutionSnapshot(ExecutionRef("alternate", "host", "t1", session_id="s1", turn_id="t1"),
                               ExecutionState.UNKNOWN, 1)
    backend.state = replace(backend.state, execution=report)
    with pytest.raises(BackendControlError, match="状态"):
        await control_session(backend, "s1", action="interrupt")
    assert not backend.actions
