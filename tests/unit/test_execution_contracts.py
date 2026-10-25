"""Execution ABI compatibility and evidence/identity contracts, entirely offline."""
from __future__ import annotations

import asyncio
import json
from dataclasses import FrozenInstanceError, replace

import pytest

from src.agents.backends.execution import (
    ExecutionEvidence, ExecutionFailure, ExecutionProgress, ExecutionRef, ExecutionReporter,
    ExecutionResult, ExecutionSnapshot, ExecutionState, ExecutionUsage,
)


def ref(**changes):
    return replace(ExecutionRef("vendor.future", "host-A/runtime-1", "attempt-1",
                               session_id="session", turn_id="turn", task_id="task", scope="runtime"), **changes)


def snapshot(**changes):
    return replace(ExecutionSnapshot(ref(), ExecutionState.RUNNING, 100,
        features=frozenset({"execution.observe", "vendor.future.feature"})), **changes)


def test_v1_round_trip_preserves_identity_progress_evidence_and_unknown_usage():
    evidence = ExecutionEvidence("e1", ref(), "check", "passed", 90, "tests ran", "abc", "abc")
    source = snapshot(state=ExecutionState.FINISHED, progress=ExecutionProgress("done", 2, 2),
        result=ExecutionResult("finished, awaiting acceptance", (evidence,), ExecutionUsage(10, 20, None)))
    exported = source.to_dict()
    decoded = ExecutionSnapshot.from_dict(json.loads(json.dumps(exported)))
    assert decoded == source
    assert decoded.result.usage.cost_usd is None
    assert not hasattr(decoded, "achieved")
    exported["ref"]["instance_id"] = "elsewhere"
    assert source.ref.instance_id == "host-A/runtime-1"


def test_new_backends_and_additive_fields_do_not_require_changing_the_core_contract():
    wire = snapshot(ref=ref(backend_id="another.team.backend")).to_dict()
    wire["optional_future_field"] = {"mode": "new"}
    wire["ref"]["optional_future_identity"] = "ignored"
    decoded = ExecutionSnapshot.from_dict(wire)
    assert decoded.ref.backend_id == "another.team.backend"
    assert "vendor.future.feature" in decoded.features


def test_new_v1_states_remain_unknown_and_retain_the_native_name():
    wire = snapshot().to_dict()
    wire["state"] = "vendor_new_state"
    decoded = ExecutionSnapshot.from_dict(wire)
    assert decoded.state == ExecutionState.UNKNOWN and decoded.native_state == "vendor_new_state"


@pytest.mark.parametrize("version", [0, 2, "1", True, None])
def test_unknown_or_ill_typed_versions_are_rejected(version):
    wire = snapshot().to_dict()
    wire["schema_version"] = version
    with pytest.raises(ValueError, match="version"):
        ExecutionSnapshot.from_dict(wire)


@pytest.mark.parametrize("field", ["backend_id", "instance_id", "run_id"])
def test_missing_identity_is_never_filled_with_a_guess(field):
    wire = snapshot().to_dict()
    del wire["ref"][field]
    with pytest.raises(ValueError):
        ExecutionSnapshot.from_dict(wire)


@pytest.mark.parametrize("invalid", [True, -1, float("nan"), float("inf"), "100"])
def test_invalid_observation_times_are_rejected(invalid):
    with pytest.raises(ValueError):
        snapshot(observed_at=invalid)


@pytest.mark.parametrize("invalid", [True, -1, 1.5, "100", float("inf")])
def test_token_measurements_do_not_accept_booleans_negative_values_or_coercion(invalid):
    with pytest.raises(ValueError):
        ExecutionUsage(input_tokens=invalid)


def test_unknown_provider_usage_stays_unknown_but_measured_zero_is_preserved():
    assert ExecutionUsage.reported({}, None) == ExecutionUsage()
    assert ExecutionUsage.reported({"input_tokens": "9", "output_tokens": True}, float("nan")) == ExecutionUsage()
    assert ExecutionUsage.reported({"input_tokens": 0, "output_tokens": 0}, 0) == ExecutionUsage(0, 0, 0)
    assert ExecutionUsage.reported({"input_tokens": 10 ** 1000}, 10 ** 1000) == ExecutionUsage()


def test_snapshots_are_immutable_and_cannot_smuggle_another_hosts_evidence():
    source = snapshot()
    with pytest.raises(FrozenInstanceError):
        source.state = ExecutionState.FINISHED
    evidence = ExecutionEvidence("e", ref(instance_id="host-B"), "check", "passed", 100)
    with pytest.raises(ValueError, match="another execution"):
        snapshot(result=ExecutionResult("done", (evidence,)))


@pytest.mark.parametrize("change", ["host", "run", "revision", "missing_revision", "failed"])
def test_evidence_cannot_be_reused_for_a_different_execution_or_revision(change):
    evidence = ExecutionEvidence("e", ref(), "check", "passed", 100, revision="abc", verified_revision="abc")
    target, revision = ref(), "abc"
    if change == "host":
        target = ref(instance_id="host-B")
    elif change == "run":
        target = ref(run_id="new-attempt")
    elif change == "revision":
        revision = "new-commit"
    elif change == "missing_revision":
        evidence = replace(evidence, verified_revision="")
    else:
        evidence = replace(evidence, verdict="failed")
    assert not evidence.matches_revision(target, revision)


def test_progress_consistency_and_error_uncertainty_are_explicit():
    with pytest.raises(ValueError):
        ExecutionProgress("impossible", 3, 2)
    error = ExecutionFailure("timeout", "receipt unknown", effect="possible")
    decoded = ExecutionSnapshot.from_dict(snapshot(error=error).to_dict())
    assert decoded.error.retryable is None and decoded.error.effect == "possible"


@pytest.mark.parametrize("bad", [[], {"schema_version": 1, "ref": {}},
    {"schema_version": 1, "ref": {}, "result": {"evidence": [{}]}}])
def test_malformed_wire_data_fails_as_a_contract_error(bad):
    with pytest.raises(ValueError):
        ExecutionSnapshot.from_dict(bad)


def describe(reporter: ExecutionReporter):
    report = reporter.execution_report
    return report.to_dict() if report else None


def test_a_future_adapter_only_needs_the_read_only_report_protocol():
    class FutureCore:
        execution_report = snapshot(ref=ref(backend_id="a-new-core"))
    assert describe(FutureCore())["ref"]["backend_id"] == "a-new-core"


class LLM:
    def __init__(self, *replies):
        self.replies = iter(replies)

    async def chat(self, messages, **kwargs):
        return {"content": next(self.replies)}


async def test_native_core_reports_progress_and_returned_text_without_inventing_acceptance(monkeypatch):
    from src.agents.agent_loop import MainAgent
    from src.agents.tool import Tool
    monkeypatch.setenv("DEFAULT_MODEL", "unknown-test-model")
    async def read(args):
        return "source"
    agent = MainAgent([Tool("inspect", "Inspect", {}, read)], max_steps=2,
        llm=LLM('{"tool":"inspect","args":{}}', "tests passed according to model"))
    owned = ref(backend_id="vortocode")
    assert await agent.run_turn("inspect", execution_ref=owned) == "tests passed according to model"
    report = agent.execution_report
    assert report.ref == owned and report.state == ExecutionState.FINISHED
    assert report.result.evidence == () and "inspect" in report.progress.summary
    assert describe(agent)["schema_version"] == 1


async def test_native_new_turn_gets_a_new_attempt_and_empty_result_stays_unknown(monkeypatch):
    from src.agents.agent_loop import MainAgent
    agent = MainAgent([], llm=LLM("first"))
    await agent.run_turn("first")
    first = agent.execution_report.ref
    async def empty(*args, **kwargs):
        return ""
    monkeypatch.setattr(agent, "_run_turn_body", empty)
    await agent.run_turn("second")
    assert agent.execution_report.ref.run_id != first.run_id
    assert agent.execution_report.ref.scope == "process"
    assert agent.execution_report.state == ExecutionState.UNKNOWN
    assert agent.execution_report.error.code == "no_result"


@pytest.mark.parametrize("kind", ["failure", "suspension", "cancellation"])
async def test_native_failed_suspended_and_cancelled_turns_keep_original_control_flow(monkeypatch, kind):
    from src.agents.agent_loop import MainAgent
    from src.agents.tool import ToolTurnYield
    agent = MainAgent([])
    error = {"failure": RuntimeError("boom"), "suspension": ToolTurnYield("need input"),
             "cancellation": asyncio.CancelledError()}[kind]
    async def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(agent, "_run_turn_body", fail)
    with pytest.raises(type(error)):
        await agent.run_turn("go")
    assert agent.execution_report.state == {"failure": ExecutionState.FAILED,
        "suspension": ExecutionState.WAITING_USER, "cancellation": ExecutionState.CANCELLED}[kind]
    assert agent.execution_report.result is None
    if kind == "suspension":
        assert agent.execution_report.error is None and agent.execution_report.progress.summary == "need input"


async def test_native_report_is_running_until_the_turn_exits(monkeypatch):
    from src.agents.agent_loop import MainAgent
    agent = MainAgent([])
    ready = asyncio.Event()
    async def wait(*args, **kwargs):
        ready.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(agent, "_run_turn_body", wait)
    task = asyncio.create_task(agent.run_turn("go"))
    await ready.wait()
    assert agent.execution_report.state == ExecutionState.RUNNING
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert agent.execution_report.state == ExecutionState.CANCELLED


async def test_invalid_native_identity_never_runs_a_new_turn_or_reuses_an_old_report(monkeypatch):
    from src.agents.agent_loop import MainAgent
    agent = MainAgent([], llm=LLM("old"))
    await agent.run_turn("old")
    assert agent.execution_report is not None
    with pytest.raises(ValueError, match="identity"):
        await agent.run_turn("new", execution_ref=False)
    assert agent.execution_report is None


def test_known_wire_field_types_are_strict_even_when_values_are_falsy():
    wire = snapshot().to_dict()
    wire["native_state"] = False
    with pytest.raises(ValueError):
        ExecutionSnapshot.from_dict(wire)
