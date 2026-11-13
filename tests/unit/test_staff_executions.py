"""Execution observations live in the original staff task, never authorize work."""
from dataclasses import replace
from functools import partial
import asyncio
import json

import pytest

from src.agents.backends.execution import ExecutionProgress, ExecutionResult, ExecutionSnapshot, ExecutionState
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.service import StaffService, staff_directory
from src.workflow.staff.executions import StaffExecution, execution_projection


class Pool:
    def __init__(self):
        self.jobs = {}

    def enqueue_existing(self, tid, execute, **kwargs):
        self.jobs[tid] = execute
        return True


@pytest.fixture
def staff(tmp_path, monkeypatch):
    monkeypatch.setattr('src.workflow.staff.executions.time', lambda: 90)
    registry = StaffRegistry(tmp_path)
    role = registry.register_role({'id': 'reader', 'title': '阅读', 'brief': '只整理任务文字',
        'grants': {'operations': ['read'], 'tools': [], 'sees': [], 'web': False, 'project': False},
        'engine': ['vortocode'], 'max_steps': 4, 'timeout_seconds': 60})
    member = registry.register_member({'id': 'reader-one', 'name': '阅读专员', 'role': role['id'], 'engine': 'vortocode'})
    service = StaffService(tmp_path, Pool(), execute=None)
    task = service.assign('owner', {'member': member['id'], 'prompt': '整理本任务'})
    work = service.collaboration('owner')
    task = work.start(task.id, 1)
    return tmp_path, service, task, work


def observed(binding, state=ExecutionState.RUNNING, **kwargs):
    return ExecutionSnapshot(kwargs.pop('ref', binding.ref), state, 100, **kwargs)


def test_snapshot_updates_original_task_without_accepting_or_granting_authority(staff):
    root, service, task, work = staff
    binding = StaffExecution(work, task, 'vortocode')
    assert binding.publish(observed(binding, ExecutionState.FINISHED, result=ExecutionResult('model says done')))
    saved = work.get(task.id)
    assert saved.status == 'running' and saved.collaboration['review'] == 'not_submitted'
    assert saved.result == ''
    report = execution_projection(saved)
    assert report['snapshot']['ref']['task_id'] == task.id and report['round'] == 1
    assert report['snapshot']['result']['evidence'] == ()
    row = staff_directory(root)['members'][0]['recent_tasks'][0]
    assert row['execution'] == report and row['status'] == 'running'


def test_late_attempt_round_session_and_terminal_updates_cannot_replace_current(staff):
    root, service, task, work = staff
    old = StaffExecution(work, task, 'vortocode')
    current = StaffExecution(work, task, 'vortocode')
    assert not old.publish(observed(old))
    assert not current.publish(observed(current, ref=replace(current.ref, task_id='another')))
    session = replace(current.ref, session_id='session-a')
    assert current.publish(ExecutionSnapshot(session, ExecutionState.RUNNING, 100))
    assert not current.publish(ExecutionSnapshot(replace(session, session_id='session-b'), ExecutionState.RUNNING, 101))
    assert current.publish(ExecutionSnapshot(session, ExecutionState.FINISHED, 102, result=ExecutionResult('done')))
    assert not current.publish(ExecutionSnapshot(session, ExecutionState.RUNNING, 103))
    work.finish(task.id, 1, '交付待核对')
    task = work.followup(task.id, 1, '返工')
    assert execution_projection(task)['snapshot']['state'] == 'unknown'
    work.start(task.id, 2)
    new = StaffExecution(work, task, 'vortocode')
    assert not current.publish(observed(current))
    assert new.publish(observed(new))
    assert execution_projection(work.get(task.id))['round'] == 2


def test_old_missing_corrupt_and_other_instance_records_are_readable_and_not_live(staff, monkeypatch):
    root, service, task, work = staff
    assert execution_projection(task) is None
    binding = StaffExecution(work, task, 'vortocode')
    assert binding.publish(observed(binding))
    monkeypatch.setattr('src.workflow.staff.executions.process_instance', lambda _: 'restarted')
    assert not binding.publish(observed(binding))
    projection = execution_projection(work.get(task.id))
    assert projection['stale'] and projection['snapshot']['state'] == 'unknown'
    assert projection['snapshot']['native_state'] == 'stale_instance'
    work.update_staff_action(task.id, 1, lambda current: current.collaboration.update(execution={'snapshot': {'schema_version': 99}}))
    assert execution_projection(work.get(task.id)) is None
    assert staff_directory(root)['members'][0]['recent_tasks'][0]['execution'] is None


def test_summary_is_bounded_redacted_and_old_observations_cannot_regress(staff):
    root, service, task, work = staff
    binding = StaffExecution(work, task, 'vortocode')
    assert binding.publish(observed(binding, progress=ExecutionProgress('OPENAI_API_KEY=sk-' + 'a' * 80 + 'x' * 8000)))
    report = execution_projection(work.get(task.id))
    assert len(report['snapshot']['progress']['summary']) <= 500
    assert 'sk-' + 'a' * 80 not in json.dumps(report)
    assert not binding.publish(ExecutionSnapshot(binding.ref, ExecutionState.RUNNING, 99))


async def test_native_staff_execution_attaches_final_report_to_same_task(staff, monkeypatch):
    from src.agents.staff import execute_staff
    root, service, task, work = staff
    class LLM:
        async def chat(self, messages, **kwargs):
            return {'content': '整理结果'}
        async def close(self):
            pass
    monkeypatch.setattr('src.llm.background.background_client', LLM)
    service.execute = partial(execute_staff, root)
    member, role = service.execution_identity(task)
    assert await service.execute(task, work, role, member, lambda: None) == '整理结果'
    saved = work.get(task.id)
    report = execution_projection(saved)['snapshot']
    assert report['state'] == 'finished' and report['ref']['backend_id'] == 'vortocode'
    assert report['ref']['task_id'] == task.id and saved.status == 'running'


async def test_native_waiting_report_does_not_invoke_action_before_original_confirmation(staff, monkeypatch):
    from src.agents.staff import execute_staff
    from src.agents.tool import Tool
    root, service, task, work = staff
    invoked, waiting, decide = [], asyncio.Event(), asyncio.Future()
    async def apply(args):
        invoked.append(args)
        return 'saved'
    from src.workflow.staff.registry import tool_catalog
    catalog = tool_catalog(root)
    monkeypatch.setattr('src.workflow.staff.registry.tool_catalog', lambda _: {
        **catalog,
        'fixture_action': Tool('fixture_action', 'fixture', {}, apply, operation_kind='own_data')})
    # This test exercises the observer around the existing confirmation callback;
    # budget accounting and owner-route identity have separate behavior contracts.
    monkeypatch.setattr('src.workflow.staff.budgets.StaffBudgetedLLM', lambda client, *args: client)
    member, role = service.execution_identity(task)
    role['grants'].update(operations=['read', 'own_data'], tools=['fixture_action'])
    class LLM:
        def __init__(self):
            self.replies = iter(['{"tool":"fixture_action","args":{}}', '整理结果'])
        async def chat(self, messages, **kwargs):
            return {'content': next(self.replies)}
        async def close(self):
            pass
    class Approval:
        async def wait_for_budget(self, *args):
            pass
        async def confirm_action(self, *args):
            waiting.set()
            return await decide
    monkeypatch.setattr('src.llm.background.background_client', LLM)
    run = asyncio.create_task(execute_staff(root, task, work, role, member, lambda: None, approval_service=Approval()))
    await asyncio.wait_for(waiting.wait(), 2)
    assert not invoked
    assert execution_projection(work.get(task.id))['snapshot']['state'] == 'waiting_user'
    decide.set_result(True)
    assert await run == '整理结果' and invoked == [{}]
    assert execution_projection(work.get(task.id))['snapshot']['state'] == 'finished'
