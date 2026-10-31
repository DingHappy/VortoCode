"""Trusted recovery decisions and their propagation; entirely offline."""
from dataclasses import replace
from pathlib import Path

import pytest

from src.agents.backends.execution import ExecutionFailure, ExecutionRef, ExecutionResult, ExecutionSnapshot, ExecutionState
from src.agents.backends.recovery import RecoveryRequired, decide_failure_recovery


def failed():
    return ExecutionSnapshot(ExecutionRef("test.vendor", "host/runtime", "attempt"), ExecutionState.FAILED, 1,
                             error=ExecutionFailure("unavailable", "unavailable", effect="not_started"))


def uncertain():
    return replace(failed(), error=ExecutionFailure("rate_limited", "quota", effect="possible"))


def test_only_confirmed_not_started_failure_can_use_a_fallback():
    source = failed()
    decision = decide_failure_recovery(source, expected_ref=source.ref, unavailable=True)
    assert decision.action == "fallback" and decision.reason == "not_started_unavailable"
    assert decide_failure_recovery(source, expected_ref=source.ref, unavailable=False).action == "fail"


@pytest.mark.parametrize("state", list(ExecutionState))
def test_non_failed_states_never_authorize_a_fallback(state):
    source = replace(failed(), state=state)
    decision = decide_failure_recovery(source, expected_ref=source.ref, unavailable=True)
    assert (decision.action == "fallback") == (state == ExecutionState.FAILED)


@pytest.mark.parametrize("effect", ["possible", "unknown"])
def test_availability_does_not_override_effect_uncertainty(effect):
    source = replace(failed(), error=ExecutionFailure("rate_limited", "quota", effect=effect))
    assert decide_failure_recovery(source, expected_ref=source.ref, unavailable=True).action == "inspect"


@pytest.mark.parametrize("field,value", [
    ("backend_id", "other"), ("instance_id", "other-host"), ("run_id", "another-attempt"),
    ("task_id", "another-task"), ("session_id", "another-session"), ("turn_id", "another-turn"),
])
def test_identity_mismatch_is_not_a_retry_grant(field, value):
    source = failed()
    expected = replace(source.ref, **{field: value})
    assert decide_failure_recovery(source, expected_ref=expected, unavailable=True).reason == "identity_unconfirmed"


def test_missing_error_conflicting_result_and_explicit_refusal_are_conservative():
    source = failed()
    for changed in (replace(source, error=None), replace(source, result=ExecutionResult("partial work"))):
        assert decide_failure_recovery(changed, expected_ref=source.ref, unavailable=True).action == "inspect"
    denied = replace(source, error=ExecutionFailure("denied", "no", retryable=False, effect="not_started"))
    assert decide_failure_recovery(denied, expected_ref=source.ref, unavailable=True).action == "fail"
    assert decide_failure_recovery(None, expected_ref=source.ref, unavailable=True).action == "inspect"


@pytest.mark.parametrize("entry", ["isolated", "dependent", "generic"])
async def test_uncertain_attempt_retains_worktree_and_reuse_cannot_delete_it(tmp_path, monkeypatch, entry):
    from src.agents import external_workers as ew, worktree
    from tests.unit.test_worktree import _init_repo

    _init_repo(tmp_path)
    factories, calls = [], []

    async def partial_run(_worker, _message, cwd, **_kwargs):
        calls.append(cwd)
        (Path(cwd) / "partial.txt").write_text("retained change")
        return ew.WorkerRun("codex", started=True, rate_limited=True, exit_code=1, error="quota")

    monkeypatch.setattr(ew, "run_worker", partial_run)

    def build(root):
        return ew.ExternalWorkerAgent("codex", root, write=True, fallback=lambda: factories.append(True))

    async def invoke():
        if entry == "isolated":
            return await worktree.run_isolated_task(str(tmp_path), "wt-recovery", "do work", build)
        if entry == "dependent":
            return await worktree.run_dependent_on_branch(str(tmp_path), "wt-recovery", "recovery-branch",
                                                          "do work", build, "work")
        return await worktree.in_worktree(str(tmp_path), "wt-recovery", lambda path: build(str(path)).run_turn("do work"))

    if entry == "dependent":
        worktree.ensure_branch(tmp_path, "recovery-branch")
        result = await invoke()
        assert result["failure_kind"] == "recovery_required"
    else:
        with pytest.raises(RecoveryRequired):
            await invoke()
    retained = tmp_path / ".vortocode" / "worktrees" / "wt-recovery"
    try:
        assert (retained / "partial.txt").read_text() == "retained change"
        assert not (tmp_path / "partial.txt").exists() and not factories and len(calls) == 1
        with pytest.raises(RuntimeError, match="已存在"):
            await invoke()
        assert (retained / "partial.txt").exists() and len(calls) == 1
    finally:
        worktree.remove_worktree(tmp_path, retained)  # Explicit fixture cleanup, after inspecting the attempt.


async def test_development_repair_does_not_repeat_a_recovery_required_attempt(tmp_path, monkeypatch):
    from src.agents import worktree
    from src.agents.dev_tools import build_dev_tools

    calls, progress = [], []
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "4")

    async def blocked(*_args, **_kwargs):
        calls.append(True)
        raise RecoveryRequired("先核对原尝试", uncertain(), str(tmp_path / "retained"))

    monkeypatch.setattr(worktree, "run_isolated_task", blocked)
    tool = next(item for item in build_dev_tools(str(tmp_path), on_progress=progress.append) if item.name == "dev_isolated")
    result = await tool.handler({"description": "fix the behavior"})
    assert result["ok"] is False and "需人工核对" in result["text"]
    assert len(calls) == 1 and not any(line.startswith("↻") for line in progress)


async def test_review_keeps_other_findings_without_retrying_or_repairing_an_uncertain_view(tmp_path):
    from src.agents import review

    calls, repairs, incomplete = [], [], []

    async def blocked(*_args, **_kwargs):
        calls.append("blocked")
        raise RecoveryRequired("先核对原尝试", uncertain())

    async def findings(*_args, **_kwargs):
        calls.append("good")
        return [{"file": "a.py", "severity": "P1", "issue": "retained finding", "evidence": "reproduced"}]

    async def repair(*args, **kwargs):
        repairs.append(True)

    text, blocked_flag = await review.run_gate(str(tmp_path), "branch", "main", repair=repair,
        reviewers={"uncertain": blocked, "confirmed": findings}, on_incomplete=incomplete.append)
    assert calls == ["blocked", "good"] and not repairs and blocked_flag
    assert "retained finding" in text and "需人工核对" in text and "uncertain" in incomplete[0]["review"]


async def test_uncertain_rereview_is_not_repeated_and_does_not_clear_the_original_finding(tmp_path):
    from src.agents import review

    calls, repairs, incomplete = [], [], []

    async def reviewer(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            return [{"file": "a.py", "severity": "P1", "issue": "original finding", "evidence": "reproduced"}]
        raise RecoveryRequired("先核对复审", uncertain())

    async def repair(*args, **kwargs):
        repairs.append(True)

    text, blocked = await review.run_gate(str(tmp_path), "branch", "main", repair=repair,
                                          reviewer=reviewer, on_incomplete=incomplete.append)
    assert len(calls) == 2 and len(repairs) == 1 and blocked
    assert "original finding" in text and "rereview" in incomplete[-1]


async def test_dependent_repair_stops_after_one_recovery_required_result(tmp_path, monkeypatch):
    from src.agents import decompose, worktree
    from src.agents.dev_tools import build_dev_tools
    from src.agents.task_analyzer import SubTask
    from tests.unit.test_worktree import _init_repo

    _init_repo(tmp_path)
    monkeypatch.setenv("VORTOCODE_DEV_ATTEMPTS", "4")
    calls = []

    async def split(*args, **kwargs):
        return {"descriptions": ["part A"], "independent": [SubTask(id="a", title="A")],
                "deferred": [SubTask(id="b", title="B", dependencies=["a"])], "total": 2}

    async def isolated(*args, **kwargs):
        return "diff\n", "done", {"ok": True, "output": ""}

    async def dependent(*args, **kwargs):
        calls.append(True)
        return {"ok": False, "conclusion": "", "failure_kind": "recovery_required", "output": "需人工核对"}

    monkeypatch.setattr(decompose, "decompose_for_parallel", split)
    monkeypatch.setattr(worktree, "run_isolated_task", isolated)
    monkeypatch.setattr(worktree, "run_dependent_on_branch", dependent)
    monkeypatch.setattr(worktree, "apply_diffs_to_branch", lambda *args, **kwargs:
                        {"ok": True, "branch": "vorto/test", "applied": ["A"], "failed": [], "integration": None})
    monkeypatch.setattr(worktree, "verify_branch", lambda *args, **kwargs: {"ok": True, "output": "", "cmd": "test"})
    tool = next(item for item in build_dev_tools(str(tmp_path)) if item.name == "dev_auto")
    result = await tool.handler({"task": "implement two parts"})
    assert len(calls) == 1 and "需人工核对" in result
