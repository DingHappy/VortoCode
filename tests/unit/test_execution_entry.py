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


@pytest.mark.parametrize('worker', ['codex', 'claude'])
@pytest.mark.parametrize('mode', ['build', 'plan'])
async def test_cli_agent_delegates_one_bound_attempt_with_original_context(monkeypatch, tmp_path, worker, mode):
    from src.agents import external_workers as cli
    from src.agents.backends.execution import ExecutionState
    entered, invoked, progress = [], [], []
    ref = ExecutionRef(worker, 'fixture-host', 'fixture-attempt', task_id='task', scope='runtime')

    async def through_entry(binding, call, *, check):
        entered.append((binding.backend_id, call))
        return await execute_once(binding, call, check=check)

    async def runner(backend, prompt, cwd, **options):
        invoked.append((backend, prompt, cwd, options))
        owned = options['execution_ref']
        run = cli.WorkerRun(backend, final_text='checked result', exit_code=0, started=True,
            run_id=owned.run_id, instance_id=owned.instance_id, task_id=owned.task_id, identity_scope=owned.scope)
        options['on_event'](cli.WorkerEvent('command', 'pytest'))
        options['on_snapshot'](run.execution_report)
        return run

    monkeypatch.setattr(cli, 'execute_once', through_entry, raising=False)
    monkeypatch.setattr(cli, 'run_worker', runner)
    monkeypatch.setattr(cli, 'worker_timeout', lambda: 37)
    agent = cli.ExternalWorkerAgent(worker, str(tmp_path), write=True, system='fixed instructions', on_progress=progress.append)
    assert await agent.run_turn('implement', mode=mode, execution_ref=ref) == 'checked result'
    assert len(entered) == len(invoked) == 1
    backend, call = entered[0]
    assert backend == worker and call.ref == ref and call.prompt == 'implement' and call.timeout == 37
    actual, prompt, cwd, options = invoked[0]
    assert (actual, prompt, cwd) == (worker, call.prompt, str(tmp_path))
    assert options['write'] is (mode == 'build') and options['system'] == 'fixed instructions'
    assert options['timeout'] == 37 and options['execution_ref'] == ref
    assert progress and agent.execution_report.ref == ref
    assert agent.execution_report.state == ExecutionState.FINISHED


async def test_cli_entry_rechecks_unattended_capabilities_without_starting_or_falling_back(monkeypatch, tmp_path):
    from src.agents import external_workers as cli
    from src.agents.capabilities import UNATTENDED_PROFILE
    invoked = []
    async def forbidden(*args, **kwargs):
        invoked.append('run')
    monkeypatch.setattr(cli, 'run_worker', forbidden)
    agent = cli.ExternalWorkerAgent('codex', str(tmp_path), write=True,
        capabilities=SimpleNamespace(profile=UNATTENDED_PROFILE), fallback=lambda: invoked.append('fallback'))
    with pytest.raises(cli.ExternalWorkerError, match='无人值守'):
        await agent.run_turn('implement')
    assert not invoked and agent.last_run is None


@pytest.mark.parametrize('phase,action,operation', [
    ('idle', 'message', 'start'), ('running', 'message', 'steer'), ('running', 'interrupt', 'interrupt'),
])
async def test_session_entry_preserves_native_control_and_acceptance_only(phase, action, operation):
    from src.agents.backends.codex import CodexBackend
    from src.agents.backends.entry import SessionRequest, control_existing
    from tests.unit.test_agent_backends import RPC
    rpc = RPC(phase)
    request = SessionRequest('codex', 's1', action, text=' continue ', expected_turn_id='t1')
    receipt = await control_existing(CodexBackend(rpc), request)
    assert receipt.operation == operation and receipt.session_id == 's1'
    assert not hasattr(receipt, 'completed')
    assert [name for name, _ in rpc.calls] == ['thread/read', 'thread/turns/list', f'turn/{operation}']
    assert set(rpc.writes[0][1]) <= {'threadId', 'input', 'expectedTurnId', 'turnId'}


async def test_session_entry_refuses_a_different_backend_before_read_or_write():
    from src.agents.backends.codex import CodexBackend
    from src.agents.backends.control import BackendControlError
    from src.agents.backends.entry import SessionRequest, control_existing
    from tests.unit.test_agent_backends import RPC
    rpc = RPC()
    with pytest.raises(BackendControlError, match='身份'):
        await control_existing(CodexBackend(rpc), SessionRequest('other', 's1', 'message', text='continue'))
    assert not rpc.calls


async def test_session_entry_never_replays_an_uncertain_receipt_or_cancellation():
    from src.agents.backends.codex import CodexBackend
    from src.agents.backends.control import ControlOutcomeUnknown
    from src.agents.backends.entry import SessionRequest, control_existing
    from tests.unit.test_agent_backends import RPC
    for response, error in [(None, ControlOutcomeUnknown), (asyncio.CancelledError(), asyncio.CancelledError)]:
        rpc = RPC()
        rpc.response['steer'] = response
        with pytest.raises(error):
            await control_existing(CodexBackend(rpc), SessionRequest('codex', 's1', 'message', text='continue', expected_turn_id='t1'))
        assert len(rpc.writes) == 1


async def test_observer_uses_session_entry_and_retains_original_action_round(monkeypatch):
    from src.agents.backends.entry import control_existing
    from src.scheduling import codex_observer as observer
    from tests.unit.test_agent_backends import RPC
    entries = []
    async def through_entry(backend, request):
        entries.append(request)
        return await control_existing(backend, request)
    monkeypatch.setattr(observer, 'control_existing', through_entry, raising=False)
    rpc = RPC(turn_id='new')
    action = {'thread_id': 's1', 'kind': 'message', 'text': 'continue', 'turn_id': 'old'}
    threads = [{'id': 's1', 'status': 'active', 'turn': {'id': 'old', 'status': 'inProgress'}}]
    result = await observer.run_action(rpc, action, threads)
    assert len(entries) == 1 and entries[0].expected_turn_id == 'old'
    assert not result['ok'] and not result['uncertain'] and not rpc.writes
