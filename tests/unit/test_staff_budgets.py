"""S5 quotas stop later requests, preserve the continuation and spare unrelated work."""
import asyncio
from datetime import date, timedelta

import pytest

from src.llm.client import add_usage
from src.workflow.staff.budgets import StaffBudgetedLLM
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskLedger, TaskRunner


def setup(root, scope='department'):
    registry = StaffRegistry(str(root))
    registry.register_role({'id': 'reader', 'title': '阅读', 'brief': '只读', 'grants': {
        'operations': ['read'], 'tools': [], 'sees': [], 'web': False, 'project': False},
        'engine': ['vortocode'], 'max_steps': 8, 'timeout_seconds': 600})
    members = [registry.register_member({'id': 'staff-' + name, 'name': name, 'role': 'reader', 'engine': 'vortocode',
        'limits': {'parallel': 1, 'daily_tokens': 6 if scope == 'member' and name == 'one' else 0}}) for name in ['one', 'two']]
    departments = [registry.register_department({'id': 'dept-' + str(index), 'name': str(index), 'duty': '只读',
        'members': [member['id']], 'limits': {'parallel': 1, 'daily_tokens': 6 if scope == 'department' and index == 0 else 0}})
        for index, member in enumerate(members)]
    pool = TaskRunner(str(root), None, max_concurrent=1)
    waiting = asyncio.Queue()
    published = set()
    def updated(task):
        if task.collaboration.get('quota', {}).get('status') == 'waiting' and task.id not in published:
            published.add(task.id); waiting.put_nowait(task)
    calls = []
    class Fake:
        async def chat(self, *args, **kwargs):
            calls.append('chat'); add_usage(4, 2, model='offline-fixture'); return {'content': '已核对'}
        async def stream(self, *args, **kwargs):
            calls.append('stream'); yield '第一段'; yield '第二段'; add_usage(4, 2, model='offline-fixture')
    async def execute(task, work, role, member, check):
        proxy = StaffBudgetedLLM(Fake(), service, task, role, member)
        if task.prompt == '流式一轮':
            return ''.join([chunk async for chunk in proxy.stream([])])
        for _ in range(2 if task.prompt == '两轮' else 1):
            await proxy.chat([])
        return '完成'
    service = StaffService(str(root), pool, execute=execute, on_update=updated)
    if scope in {'global', 'task'}:
        service.configure_budgets({'daily_tokens': 6 if scope == 'global' else 0,
            'task_tokens': 6 if scope == 'task' else 0}, service.budgets.settings()['revision'])
    return service, members, departments, waiting, calls


async def waited(queue):
    return await asyncio.wait_for(queue.get(), 2)


@pytest.mark.parametrize('scope', ['department', 'member', 'task', 'global'])
async def test_reaching_quota_stops_next_request_and_owner_adjustment_resumes_same_task(tmp_path, scope):
    service, members, departments, waiting, calls = setup(tmp_path, scope)
    try:
        task = service.assign('owner', {'member': members[0]['id'], 'prompt': '两轮'})
        blocked = await waited(waiting)
        assert blocked.status == 'blocked' and blocked.collaboration['quota']['scope'] == scope
        assert calls == ['chat'] and TaskLedger(tmp_path).load(task.id).collaboration['usage']['tokens'] == 6
        if scope == 'department':
            service.registry.register_department({**departments[0], 'limits': {'parallel': 1, 'daily_tokens': 12}})
            service.wake_budgets()
        elif scope == 'member':
            # Changing a member version invalidates the old execution instead of widening it.
            current = service.registry.member(members[0]['id'])
            service.registry.register_member({**current, 'limits': {'parallel': 1, 'daily_tokens': 12}})
            service.wake_budgets()
            await service.runner.join(task.id)
            assert TaskLedger(tmp_path).load(task.id).status == 'failed' and calls == ['chat']
            return
        else:
            service.configure_budgets({'daily_tokens': 12 if scope == 'global' else 0,
                'task_tokens': 12 if scope == 'task' else 0}, service.budgets.settings()['revision'])
        await asyncio.wait_for(service.runner.join(task.id), 2)
        current = TaskLedger(tmp_path).load(task.id)
        assert current.status == 'done' and current.collaboration['round'] == 1
        assert calls == ['chat', 'chat'] and current.collaboration['usage']['tokens'] == 12
    finally:
        await service.runner.shutdown()


async def test_department_quota_releases_pool_and_does_not_stop_other_departments_or_chat(tmp_path):
    service, members, _, waiting, calls = setup(tmp_path)
    try:
        first = service.assign('owner', {'member': members[0]['id'], 'prompt': '两轮'})
        await waited(waiting)
        other = service.assign('owner', {'member': members[1]['id'], 'prompt': '一轮'})
        await asyncio.wait_for(service.runner.join(other.id), 2)
        assert TaskLedger(tmp_path).load(other.id).status == 'done'
        add_usage(100, 20, model='foreground-fixture')
        assert service.budgets.usage()['total'] == 12 and calls == ['chat', 'chat']
        assert TaskLedger(tmp_path).load(first.id).status == 'blocked'
    finally:
        await service.runner.shutdown()


async def test_streaming_usage_is_recorded_under_the_same_department_and_member(tmp_path):
    service, members, _, _, calls = setup(tmp_path)
    try:
        task = service.assign('owner', {'member': members[1]['id'], 'prompt': '流式一轮'})
        await service.runner.join(task.id)
        assert TaskLedger(tmp_path).load(task.id).result == '第一段第二段'
        usage = service.budgets.usage()
        assert calls == ['stream'] and usage['departments']['dept-1'] == 6 and usage['members'][members[1]['id']] == 6
    finally:
        await service.runner.shutdown()


async def test_daily_reset_can_resume_a_waiting_department_without_replaying_the_previous_call(tmp_path, monkeypatch):
    service, members, _, waiting, calls = setup(tmp_path)
    today = [date(2026, 11, 10)]
    service.budgets.today = lambda: today[0]
    from src.llm import usage_ledger
    monkeypatch.setattr(usage_ledger, 'date', type('ClockDate', (date,), {'today': classmethod(lambda cls: today[0])}))
    try:
        task = service.assign('owner', {'member': members[0]['id'], 'prompt': '两轮'})
        await waited(waiting)
        today[0] += timedelta(days=1); service.wake_budgets()
        await asyncio.wait_for(service.runner.join(task.id), 2)
        assert calls == ['chat', 'chat']
        assert service.budgets.usage()['total'] == 6
        assert TaskLedger(tmp_path).load(task.id).collaboration['usage']['tokens'] == 12
    finally:
        await service.runner.shutdown()


async def test_cancelled_quota_wait_cannot_resume_after_settings_change(tmp_path):
    service, members, _, waiting, calls = setup(tmp_path)
    try:
        task = service.assign('owner', {'member': members[0]['id'], 'prompt': '两轮'})
        await waited(waiting)
        service.runner.cancel(task.id); await service.runner.join(task.id)
        service.configure_budgets({'daily_tokens': 0, 'task_tokens': 0}, service.budgets.settings()['revision'])
        assert TaskLedger(tmp_path).load(task.id).status == 'cancelled' and calls == ['chat']
    finally:
        await service.runner.shutdown()


async def test_unreadable_usage_never_resets_a_quota_to_zero(tmp_path, monkeypatch):
    from src.llm import usage_ledger
    service, members, _, _, calls = setup(tmp_path)
    monkeypatch.setenv('VORTOONE_USAGE_DIR', str(tmp_path / 'usage-fixture'))
    path = usage_ledger._path(); path.parent.mkdir(); path.write_text('{broken')
    try:
        task = service.assign('owner', {'member': members[0]['id'], 'prompt': '一轮'})
        await service.runner.join(task.id)
        assert TaskLedger(tmp_path).load(task.id).status == 'failed' and not calls
    finally:
        await service.runner.shutdown()


async def test_missing_usage_receipt_stops_more_calls_and_remains_explicit(tmp_path, monkeypatch):
    service, members, _, _, calls = setup(tmp_path)
    monkeypatch.setattr('src.llm.usage_ledger.record', lambda *args, **kwargs: None)
    try:
        task = service.assign('owner', {'member': members[0]['id'], 'prompt': '两轮'})
        await service.runner.join(task.id)
        current = TaskLedger(tmp_path).load(task.id)
        assert calls == ['chat'] and current.status == 'failed'
        assert current.collaboration['usage']['complete'] is False
        assert service.budgets.status(current, service.registry.member(members[0]['id']))['scope'] == 'accounting'
    finally:
        await service.runner.shutdown()


async def test_quota_is_rechecked_after_reacquiring_the_shared_slot(tmp_path, monkeypatch):
    service, members, _, waiting, calls = setup(tmp_path, 'global')
    parked_again = asyncio.Event()
    stages = []
    def update(task):
        if task.status == 'blocked' and task.collaboration.get('quota', {}).get('status') == 'waiting':
            stages.append(task.id)
            if len(stages) == 2: parked_again.set()
    service.runner.subscribe(update)
    try:
        task = service.assign('owner', {'member': members[0]['id'], 'prompt': '两轮'})
        await waited(waiting)
        from src.workflow.tasks.tasks import _ExecutionCapacity
        from src.llm.usage_ledger import record
        acquire = _ExecutionCapacity.acquire
        settled = []
        async def settle_during_reacquisition(capacity):
            await acquire(capacity)
            if not settled:
                # A previously admitted peer request settles after the quota check,
                # while this continuation was reacquiring shared capacity.
                settled.append(True)
                record(4, 2, source=f'staff:{service.budgets.instance}:dept-1:staff-two:task-0000000000')
        monkeypatch.setattr(_ExecutionCapacity, 'acquire', settle_during_reacquisition)
        service.configure_budgets({'daily_tokens': 12, 'task_tokens': 0}, service.budgets.settings()['revision'])
        await asyncio.wait_for(parked_again.wait(), 2)
        assert calls == ['chat'] and TaskLedger(tmp_path).load(task.id).status == 'blocked'
    finally:
        await service.runner.shutdown()
