"""The common entry delegates once; capability declarations never grant authority."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.agents.backends.entry import ExecutionBinding, OneShotRequest, execute_once
from src.agents.backends.execution import ExecutionRef


def request(**kwargs):
    return replace(OneShotRequest(ExecutionRef('opencode', 'fixture-instance', 'attempt', task_id='task'),
        'modify the isolated copy', 30), **kwargs)


async def test_entry_keeps_result_identity_and_checks_authority_before_and_after_one_invocation():
    events, result = [], object()
    async def run(call):
        events.append(('run', call.ref))
        return result
    binding = ExecutionBinding('opencode', frozenset({'execution.run', 'result.text'}), run)
    call = request()
    assert await execute_once(binding, call, check=lambda: events.append('check')) is result
    assert events == ['check', ('run', call.ref), 'check']


@pytest.mark.parametrize('changes', [
    {'ref': ExecutionRef('unbound-program', 'fixture-instance', 'attempt')},
    {'ref': ExecutionRef('opencode', 'fixture-instance', 'attempt', session_id='existing')},
    {'ref': ExecutionRef('opencode', 'fixture-instance', 'attempt', turn_id='existing')},
    {'required_features': frozenset({'control.resume'})},
])
async def test_invalid_or_unsupported_request_never_starts_a_program(changes):
    events = []
    async def forbidden(call):
        events.append('run')
    binding = ExecutionBinding('opencode', frozenset({'execution.run'}), forbidden)
    with pytest.raises(ValueError):
        await execute_once(binding, request(**changes), check=lambda: events.append('check'))
    assert not events


@pytest.mark.parametrize('timeout', [0, -1, True, float('nan'), float('inf'), 10 ** 1000])
def test_request_requires_a_finite_positive_deadline(timeout):
    with pytest.raises(ValueError):
        request(timeout=timeout)


@pytest.mark.parametrize('error', [RuntimeError('unconfirmed receipt'), TimeoutError('deadline'), asyncio.CancelledError()])
async def test_errors_and_cancellation_propagate_without_retry_fallback_or_new_attempt(error):
    calls = []
    async def run(call):
        calls.append(call.ref)
        raise error
    binding = ExecutionBinding('opencode', frozenset({'execution.run'}), run)
    call = request()
    with pytest.raises(type(error)) as raised:
        await execute_once(binding, call, check=lambda: None)
    assert raised.value is error and calls == [call.ref]


@pytest.mark.parametrize('revoke_after_start', [False, True])
async def test_capability_support_does_not_bypass_current_role_authorization(revoke_after_start):
    calls = []
    async def run(call):
        calls.append(call)
        return 'result'
    def check():
        if not revoke_after_start or calls:
            raise ValueError('role changed')
    binding = ExecutionBinding('opencode', frozenset({'execution.run'}), run)
    with pytest.raises(ValueError, match='role changed'):
        await execute_once(binding, request(), check=check)
    assert len(calls) == int(revoke_after_start)


async def test_developer_binding_preserves_original_role_model_environment_and_observer(monkeypatch, tmp_path):
    from src.agents import team_engines
    calls, report = [], lambda snapshot: None
    original = SimpleNamespace(text='finished', error='', networked=False)
    async def old_runner(*args, **kwargs):
        calls.append((args, kwargs))
        return original
    monkeypatch.setattr(team_engines, 'run_external', old_runner)
    call = request()
    result = await team_engines.run_developer('opencode', 'chosen-model', call.prompt, timeout=call.timeout,
        child_id='task', workdir=str(tmp_path), execution_ref=call.ref, on_snapshot=report, check=lambda: None)
    assert result is original and len(calls) == 1
    args, options = calls[0]
    assert args == ('opencode', 'developer', 'chosen-model', call.prompt)
    assert options['timeout'] == 30 and options['workdir'] == str(tmp_path) and options['child_id'] == 'task'
    assert options['execution_ref'] == call.ref and options['on_snapshot'] is report
    with pytest.raises(team_engines.EngineUnavailable):
        await team_engines.run_developer('deepseek-harness', '', 'prompt', timeout=30,
            child_id='task', workdir=str(tmp_path), check=lambda: None)
    assert len(calls) == 1


@pytest.mark.parametrize('task_id', ['', 'another-task'])
async def test_developer_binding_refuses_a_different_task_before_start(monkeypatch, tmp_path, task_id):
    from src.agents import team_engines
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(args)
    monkeypatch.setattr(team_engines, 'run_external', forbidden)
    with pytest.raises(ValueError, match='task'):
        await team_engines.run_developer('opencode', '', 'prompt', timeout=30, child_id='task',
            workdir=str(tmp_path), execution_ref=replace(request().ref, task_id=task_id), check=lambda: None)
    assert calls == []
