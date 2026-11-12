"""L2: durable never-started proof, conservative restart, explicit owner recovery."""
from datetime import datetime
from copy import deepcopy
import asyncio

import pytest

from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskLedger, TaskRunner
from tests.unit.test_staff_routines import registered


class WaitingPool:
    def enqueue_existing(self, *args, **kwargs):
        return True

    def notify_existing(self, *args):
        pass


def pending(root, *, routine=False):
    registry, routines, data = registered(root)
    staff = StaffService(str(root), WaitingPool(), execute=None)
    if routine:
        routines.register(data)
        tid, = routines.dispatch(staff, datetime.fromisoformat('2026-11-11T09:01:00+00:00'))
        task = TaskLedger(str(root)).load(tid)
    else:
        task = staff.assign('owner', {'member': data['member'], 'prompt': '核对资料'})
    return staff, registry, routines, data, task


async def test_restart_requeues_the_same_proven_never_started_task_once(tmp_path, monkeypatch):
    from src.scheduling.staff_runner import set_staff_service
    _, _, routines, data, task = pending(tmp_path, routine=True)
    calls = []
    async def execute(root, current, work, role, member, check, **kwargs):
        check(); calls.append(current.id); return '核对完成'
    monkeypatch.setattr('src.agents.staff.execute_staff', execute)
    pool = TaskRunner(str(tmp_path), None)
    try:
        service = set_staff_service(tmp_path, pool)
        service.recover()
        assert pool.is_active(task.id)
        assert not routines.get(data['id'])['state']['error']
        await pool.join(task.id)
        assert calls == [task.id] and pool.ledger.load(task.id).status == 'done'
        assert len(pool.ledger.list()) == 1
    finally:
        set_staff_service(tmp_path)
        await pool.shutdown()


@pytest.mark.parametrize('evidence', ['missing', 'started', 'log', 'model', 'usage', 'approval', 'round', 'member', 'role', 'department'])
def test_unknown_or_previously_started_work_stays_interrupted(tmp_path, evidence):
    staff, registry, _, _, task = pending(tmp_path)
    if evidence == 'missing': task.collaboration.pop('queue_proof', None)
    elif evidence == 'started': staff.collaboration('owner').start(task.id, 1); task = TaskLedger(str(tmp_path)).load(task.id)
    elif evidence == 'log': task.log.append('一次动作结果未知')
    elif evidence == 'model': task.collaboration['model_target'] = {'model': 'fixture'}
    elif evidence == 'usage': task.collaboration['usage'] = {'tokens': 0, 'calls': 1}
    elif evidence == 'approval': task.collaboration['approval'] = {'status': 'pending'}
    elif evidence == 'round': task.collaboration['round'] = 2
    elif evidence == 'role':
        assigned = registry.role(task.collaboration['dispatch']['role'])
        registry.register_role({**assigned, 'brief': '主人修改后的职责'})
    elif evidence == 'department':
        registry.register_department({'id': 'dept-new', 'name': '新部门', 'duty': '核对资料',
                                      'members': [task.collaboration['dispatch']['member']]})
    else:
        member = registry.member(task.collaboration['dispatch']['member'])
        registry.set_member_status(member['id'], 'paused', member['revision'])
    TaskLedger(str(tmp_path)).save(task)
    staff.recover()
    assert TaskLedger(str(tmp_path)).load(task.id).status == 'interrupted'


def test_routine_recovery_keeps_a_proven_queue_in_its_original_slot(tmp_path):
    _, _, routines, data, task = pending(tmp_path, routine=True)
    before = deepcopy(routines.get(data['id'])['state'])
    routines.recover()
    assert TaskLedger(str(tmp_path)).load(task.id).status == 'queued'
    assert routines.get(data['id'])['state'] == before


def test_recovery_batch_requires_owner_and_exact_displayed_versions(tmp_path):
    from src.workflow.staff.recovery import StaffRecovery
    from src.workflow.staff.owner_confirmation import owner_route
    staff, _, _, _, task = pending(tmp_path)
    task.status = 'interrupted'; TaskLedger(str(tmp_path)).save(task)
    recovery = StaffRecovery(staff)
    snapshot = recovery.snapshot()
    selected = [{'id': task.id, 'digest': snapshot['items'][0]['digest']}]
    with pytest.raises(ValueError, match='主人'):
        recovery.decide(selected, 'abandon', snapshot['instance'])
    task.log.append('核对期间更新'); TaskLedger(str(tmp_path)).save(task)
    with owner_route(tmp_path), pytest.raises(ValueError, match='变化'):
        recovery.decide(selected, 'abandon', snapshot['instance'])
    assert TaskLedger(str(tmp_path)).load(task.id).status == 'interrupted'


def owner_batch(staff, action, **changes):
    from src.workflow.staff.recovery import StaffRecovery
    from src.workflow.staff.owner_confirmation import owner_route
    recovery = StaffRecovery(staff)
    snapshot = recovery.snapshot()
    with owner_route(staff.root):
        return recovery.decide([{'id': row['id'], 'digest': row['digest']} for row in snapshot['items']],
            action, changes.get('instance', snapshot['instance']))


@pytest.mark.parametrize('action', ['rework', 'abandon'])
def test_owner_batch_updates_each_original_task_without_replaying_an_old_card(tmp_path, action):
    staff, _, _, _, task = pending(tmp_path)
    task.status = 'interrupted'
    task.collaboration['approval'] = {'status': 'interrupted', 'payload': {'round': 1}}
    task.collaboration['approval_history'] = [{'tool': 'fixture_change', 'status': 'approved'}]
    TaskLedger(str(tmp_path)).save(task)
    from src.workflow.staff.recovery import StaffRecovery
    shown = StaffRecovery(staff).snapshot()['items'][0]
    assert shown['execution']['approval_history'][0]['status'] == 'approved'
    assert '不代表' in shown['effects']
    result = owner_batch(staff, action)
    assert result['results'] == [{'id': task.id, 'status': 'applied'}]
    current = TaskLedger(str(tmp_path)).load(task.id)
    assert current.status == ('queued' if action == 'rework' else 'cancelled')
    assert current.collaboration['round'] == (2 if action == 'rework' else 1)
    assert current.collaboration['approval']['status'] == 'interrupted'
    assert len(TaskLedger(str(tmp_path)).list()) == 1


def test_changed_instance_and_failed_audit_cannot_recover_work(tmp_path, monkeypatch):
    staff, _, _, _, task = pending(tmp_path)
    task.status = 'interrupted'; TaskLedger(str(tmp_path)).save(task)
    with pytest.raises(ValueError, match='实例'):
        owner_batch(staff, 'rework', instance='previous-process')
    monkeypatch.setattr('src.gateway.audit.append_audit', lambda *args, **kwargs: False)
    result = owner_batch(staff, 'rework')
    assert result['results'][0]['status'] == 'failed' and '审计' in result['results'][0]['error']
    assert TaskLedger(str(tmp_path)).load(task.id).status == 'interrupted'


def test_one_changed_selection_stops_the_whole_batch_before_any_write(tmp_path):
    from src.workflow.staff.recovery import StaffRecovery
    from src.workflow.staff.owner_confirmation import owner_route
    staff, _, _, data, task = pending(tmp_path)
    other = staff.assign('owner', {'member': data['member'], 'prompt': '另一件事'})
    for current in (task, other):
        current.status = 'interrupted'; TaskLedger(str(tmp_path)).save(current)
    recovery = StaffRecovery(staff); snapshot = recovery.snapshot()
    other.log.append('另一个进程补充记录'); TaskLedger(str(tmp_path)).save(other)
    with owner_route(tmp_path), pytest.raises(ValueError, match='变化'):
        recovery.decide([{'id': x['id'], 'digest': x['digest']} for x in snapshot['items']], 'abandon', snapshot['instance'])
    assert all(t.status == 'interrupted' for t in TaskLedger(str(tmp_path)).list())


def test_interrupted_team_member_requires_owner_and_keeps_the_same_round_on_denial(tmp_path):
    from tests.unit.test_team_staff_contract import specialist
    from src.workflow.tasks.team import TeamService
    specialist(tmp_path)
    teams = TeamService(str(tmp_path))
    root = teams.create('owner', objective='核对公开资料', acceptance=['核对结果'],
        members=[{'member': 'staff-reader'}, {'name': '审查', 'role': 'reviewer'}], reason='independent_review')
    task = teams.assign(root.id, 'reader', '核对资料', '核对报告')
    task.status = 'interrupted'; teams.ledger.save(task)
    with pytest.raises(ValueError, match='主人'):
        teams.review(root.id, task.id, 'rework', '模型建议重跑')
    assert teams.ledger.load(task.id).collaboration['round'] == 1


def test_owner_can_rework_the_original_routine_slot_without_creating_a_second_task(tmp_path):
    staff, _, routines, data, task = pending(tmp_path, routine=True)
    staff.collaboration('owner').start(task.id, 1)
    routines.recover()
    original = task.collaboration['dispatch']['routine']['slot']
    assert owner_batch(staff, 'rework')['results'][0]['status'] == 'applied'
    current = TaskLedger(str(tmp_path)).load(task.id)
    assert current.collaboration['round'] == 2 and current.status == 'queued'
    assert routines.get(data['id'])['state']['task'] == task.id
    assert routines.get(data['id'])['state']['cursor'] == original
    assert routines.dispatch(staff, datetime.fromisoformat('2026-11-12T09:01:00+00:00')) == []


async def test_requeued_work_checks_latest_quota_before_any_model_or_tool(tmp_path, monkeypatch):
    from src.scheduling.staff_runner import set_staff_service
    _, _, _, _, task = pending(tmp_path)
    called, checked = [], asyncio.Event()
    async def execute(*args, **kwargs): called.append(True); return 'done'
    monkeypatch.setattr('src.agents.staff.execute_staff', execute)
    def exhausted(self, current, member):
        checked.set(); return {'scope': 'global', 'reason': 'fixture exhausted', 'day': '2026-11-11'}
    monkeypatch.setattr('src.workflow.staff.budgets.StaffBudgets.status', exhausted)
    pool = TaskRunner(str(tmp_path), None)
    try:
        set_staff_service(tmp_path, pool)
        await asyncio.wait_for(checked.wait(), 1)
        await asyncio.sleep(0)
        assert called == [] and pool.ledger.load(task.id).status == 'blocked'
    finally:
        set_staff_service(tmp_path)
        await pool.shutdown()


def test_recovery_http_writes_need_an_explicit_owner_token_and_exact_versions(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.server import app
    from src.workflow.staff.recovery import StaffRecovery
    staff, _, _, _, task = pending(tmp_path)
    task.status = 'interrupted'; TaskLedger(str(tmp_path)).save(task)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VORTOONE_API_TOKEN', 'fixture-admin')
    monkeypatch.setattr('src.scheduling.staff_runner.get_staff_service', lambda _: staff)
    client, headers = TestClient(app), {'Authorization': 'Bearer fixture-admin'}
    snapshot = StaffRecovery(staff).snapshot()
    data = {'selected': [{'id': task.id, 'digest': snapshot['items'][0]['digest']}], 'instance': snapshot['instance'], 'action': 'abandon'}
    assert client.post('/api/staff/recovery/decision', json=data).status_code == 401
    assert client.post('/api/staff/recovery/decision', headers=headers, json={**data, 'grants': {}}).status_code == 422
    assert client.get('/api/staff/recovery', headers=headers).json() == snapshot
    assert client.post('/api/staff/recovery/decision', headers=headers, json=data).json()['results'][0]['status'] == 'applied'
    assert client.post('/api/staff/recovery/decision', headers=headers, json=data).status_code == 409
