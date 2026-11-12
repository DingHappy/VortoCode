"""S7 schedules create existing collaboration tasks, without new authority."""
import asyncio
from datetime import datetime, timedelta
import pytest

from tests.unit.staff_owner_helpers import owner_decide, proposal_body

from src.scheduling import cron


def write_jobs(root, text):
    path = root / '.vortoone' / 'cron.yaml'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


async def test_due_jobs_can_progress_while_another_prompt_is_waiting(tmp_path):
    write_jobs(tmp_path, 'jobs:\n  - name: slow\n    schedule: every 1h\n    prompt: slow\n'
                         '  - name: quick\n    schedule: every 1h\n    prompt: quick\n')
    release, quick = asyncio.Event(), asyncio.Event()
    async def run_session(root, prompt, **kwargs):
        if prompt == 'slow':
            await release.wait()
        else:
            quick.set()
        return prompt
    running = asyncio.create_task(cron.run_due(str(tmp_path), datetime(2026, 11, 11, 9), run_session=run_session))
    try:
        await asyncio.wait_for(quick.wait(), 1)
    finally:
        release.set()
        await running


def test_missed_five_field_schedule_is_due_once_after_restart(tmp_path):
    write_jobs(tmp_path, 'jobs:\n  - name: daily\n    schedule: "0 9 * * *"\n    prompt: report\n')
    now = datetime(2026, 11, 11, 9, 3)
    state = cron.CronState(str(tmp_path))
    state.mark('daily', now - timedelta(days=1))
    assert [job.name for job in cron.due_jobs(str(tmp_path), now)] == ['daily']
    state.mark('daily', now)
    assert cron.due_jobs(str(tmp_path), now) == []


def test_owner_registered_routine_is_stored_in_the_staff_directory(tmp_path):
    from src.workflow.staff.registry import StaffRegistry
    from src.workflow.staff.routines import Routines
    registry = StaffRegistry(str(tmp_path))
    role = registry.register_role({'id': 'plain-reader', 'title': '核对', 'brief': '核对任务中的文字',
        'grants': {'operations': ['read'], 'tools': [], 'sees': [], 'web': False, 'project': False},
        'engine': ['vortocode'], 'max_steps': 4, 'timeout_seconds': 60})
    member = registry.register_member({'id': 'staff-reader', 'name': '核对专员', 'role': role['id'], 'engine': 'vortocode'})
    routines = Routines(str(tmp_path))
    item = routines.register({'id': 'routine-report', 'name': '例行核对', 'member': member['id'],
        'prompt': '核对公开数字', 'schedule': 'at 09:00', 'timezone': 'UTC', 'delivery': 'notify'})
    assert routines.get(item['id'])['member'] == member['id']
    assert (registry.directory / 'routines.json').is_file()


def registered(root, **kwargs):
    from tests.unit.test_staff_departments import specialist
    from src.workflow.staff.registry import StaffRegistry
    from src.workflow.staff.routines import Routines
    registry = StaffRegistry(str(root))
    member = specialist(registry, 'reader', **kwargs)
    routines = Routines(str(root), clock=lambda: datetime.fromisoformat('2026-11-11T08:00:00+00:00'))
    data = {'id': 'routine-report', 'name': '核对', 'member': member['id'], 'prompt': '核对资料',
            'schedule': '0 9 * * *', 'timezone': 'UTC', 'delivery': 'notify'}
    return registry, routines, data


def test_routine_proposal_grants_nothing_before_an_explicit_owner_decision(tmp_path):
    from src.workflow.staff.approvals import StaffApprovals
    _, routines, data = registered(tmp_path)
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'routine', data, '每天核对')
    assert routines.list() == [] and proposal['approval_level'] == 'normal'
    with pytest.raises(ValueError, match='主人'):
        approvals.decide(proposal['id'], True, proposal['digest'])
    owner_decide(approvals, proposal['id'], True, proposal['digest'])
    assert routines.get(data['id'])['member'] == data['member']


async def test_routine_dispatch_uses_one_existing_task_per_slot_and_shared_staff_pool(tmp_path):
    from src.workflow.staff.service import StaffService
    from src.workflow.tasks.tasks import TaskRunner
    registry, routines, data = registered(tmp_path)
    routines.register(data)
    pool = TaskRunner(str(tmp_path), None)
    called = []
    async def execute(task, work, role, member, check):
        check(); called.append(task.id); return '完整汇报'
    staff = StaffService(str(tmp_path), pool, execute=execute)
    now = datetime.fromisoformat('2026-11-11T09:03:00+00:00')
    try:
        ids = routines.dispatch(staff, now)
        assert len(ids) == 1 and routines.dispatch(staff, now) == []
        await asyncio.wait_for(pool.join(ids[0]), 2)
        task = pool.ledger.load(ids[0])
        assert task.kind == 'delegation' and task.collaboration['dispatch']['source'] == 'staff'
        assert task.status == 'done' and called == ids
        assert routines.dispatch(staff, now) == []
        later = routines.dispatch(staff, now + timedelta(days=1))
        assert len(later) == 1 and later != ids
        await pool.join(later[0])
    finally:
        await pool.shutdown()


def test_migration_needs_owner_then_preserves_models_prompts_and_separate_permissions(tmp_path, monkeypatch):
    from src.workflow.staff.routine_migration import RoutineMigration, legacy_owner
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.registry import StaffRegistry
    monkeypatch.setenv('VORTOONE_WORKSPACE_SCOPE', 'general')
    write_jobs(tmp_path, 'jobs:\n  - name: personal\n    schedule: at 09:00\n    prompt: 个人回顾\n    model: offline-model\n    announce: silent\n'
                         '  - name: public\n    schedule: every 1h\n    prompt: 查公开新闻\n    allow_web: true\n'
                         '  - name: command\n    schedule: every 1h\n    command: fixture\n')
    source = cron.cron_path(str(tmp_path)).read_bytes()
    migration = RoutineMigration(str(tmp_path))
    plan = migration.plan('UTC')
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'routine_migration', {'timezone': 'UTC', 'config_revision': plan['config_revision']}, '迁移')
    assert StaffRegistry(str(tmp_path)).members() == [] and legacy_owner(str(tmp_path), 'personal') is None
    with pytest.raises(ValueError, match='主人写入路由'):
        approvals.decide(proposal['id'], True, proposal['digest'])
    approved = owner_decide(approvals, proposal['id'], True, proposal['digest'])
    assert approved['status'] == 'approved'
    assert cron.cron_path(str(tmp_path)).read_bytes() == source
    assert [j.name for j in cron.due_jobs(str(tmp_path), datetime(2026, 11, 11, 12))] == ['command']
    roles = {r['id']: r for r in StaffRegistry(str(tmp_path)).roles()}
    assert roles['routine-public']['grants']['sees'] == []
    assert roles['routine-offline']['grants']['web'] is False
    members = StaffRegistry(str(tmp_path)).members()
    assert next(m for m in members if m['role'] == 'routine-offline')['model'] == 'offline-model'
    assert len(migration.routines.list()) == 2 and legacy_owner(str(tmp_path), 'public')['status'] == 'complete'
    assert owner_decide(approvals, proposal['id'], True, proposal['digest']) == approved


def test_project_migration_is_explicitly_deferred(tmp_path, monkeypatch):
    from src.workflow.staff.routine_migration import RoutineMigration
    monkeypatch.setenv('VORTOONE_WORKSPACE_SCOPE', 'project')
    write_jobs(tmp_path, 'jobs:\n  - name: project\n    schedule: every 1h\n    prompt: 核对项目\n')
    plan = RoutineMigration(str(tmp_path)).plan('UTC')
    assert plan['routines'] == [] and plan['deferred'][0]['reason'] == '项目提示类暂缓迁移'


@pytest.mark.parametrize('expression,zone_name,last,now,expected', [
    ('at 09:00', 'Asia/Shanghai', '2026-11-11T00:00:00+00:00', '2026-11-11T01:03:00+00:00', '2026-11-11T01:00:00+00:00'),
    ('0 9 * * 1', 'UTC', '2026-11-01T00:00:00+00:00', '2026-11-11T10:00:00+00:00', '2026-11-09T09:00:00+00:00'),
    ('once 2026-11-12T09:00:00+08:00', 'Asia/Shanghai', '2026-11-11T00:00:00+00:00', '2026-11-12T01:01:00+00:00', '2026-11-12T01:00:00+00:00'),
    ('at 02:30', 'America/New_York', '2026-03-08T05:00:00+00:00', '2026-03-08T08:00:00+00:00', None),
    ('at 01:30', 'America/New_York', '2026-11-01T05:30:00+00:00', '2026-11-01T06:31:00+00:00', None),
])
def test_zoned_schedule_catches_up_once_and_does_not_invent_dst_slots(expression, zone_name, last, now, expected):
    from src.scheduling.routine_schedule import RoutineSchedule
    due = RoutineSchedule(expression, zone_name).due(now, last)
    assert (due.isoformat() if due else None) == expected
    if due:
        assert RoutineSchedule(expression, zone_name).due(now, due) is None


@pytest.mark.parametrize('field', ['grants', 'role', 'model', 'engine', 'allow_web', 'owner'])
def test_routine_input_cannot_override_registered_authority(tmp_path, field):
    _, routines, data = registered(tmp_path)
    with pytest.raises(ValueError, match='权限|模型|未知'):
        routines.register({**data, field: True})


def test_changed_routine_member_cannot_be_silently_rebound(tmp_path):
    registry, routines, data = registered(tmp_path)
    saved = routines.register(data)
    member = registry.member(data['member'])
    registry.set_member_status(member['id'], 'paused', member['revision'])
    with pytest.raises(ValueError, match='版本|岗位'):
        routines.control(saved['id'], saved['revision'], enabled=True)
    assert routines.get(saved['id'])['revision'] == saved['revision']


@pytest.mark.parametrize('approve', [True, False])
async def test_routine_goal_action_waits_for_owner_without_holding_shared_capacity(tmp_path, monkeypatch, approve):
    from tests.unit.test_staff_actions import live_staff, next_card, decide
    from src.workflow.staff.routines import Routines
    from src.workflow.workbench.personal_goals import PersonalGoals
    service, member, waiting, _, _, _, starts = live_staff(tmp_path, monkeypatch, real_goal=True)
    routines = Routines(str(tmp_path), clock=lambda: datetime.fromisoformat('2026-11-11T08:00:00+00:00'))
    routines.register({'id': 'routine-goal', 'name': '整理', 'member': member['id'], 'prompt': '整理学习计划',
                       'schedule': 'at 09:00', 'timezone': 'UTC', 'delivery': 'notify'})
    try:
        ids = routines.dispatch(service, datetime.fromisoformat('2026-11-11T09:01:00+00:00'))
        task, card = await next_card(waiting)
        assert task.id == ids[0] and not PersonalGoals(str(tmp_path)).list()
        other = service.assign('owner', {'member': member['id'], 'prompt': '独立只读任务'})
        await asyncio.wait_for(service.runner.join(other.id), 2)
        assert service.runner.ledger.load(other.id).status == 'done'
        decide(service, task, card, approve, reason='' if approve else '本次不保存')
        await asyncio.wait_for(service.runner.join(task.id), 2)
        assert len(PersonalGoals(str(tmp_path)).list()) == int(approve)
        assert starts.count((task.id, 1)) == 1
    finally:
        await service.runner.shutdown()


def test_interrupted_migration_blocks_both_lanes_until_the_same_signed_decision_finishes(tmp_path, monkeypatch):
    from src.workflow.staff.routine_migration import RoutineMigration, legacy_owner
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.registry import StaffRegistry
    monkeypatch.setenv('VORTOONE_WORKSPACE_SCOPE', 'general')
    write_jobs(tmp_path, 'jobs:\n  - name: old\n    schedule: every 1h\n    prompt: old\n')
    migration = RoutineMigration(str(tmp_path))
    approvals = StaffApprovals(str(tmp_path))
    plan = migration.plan('UTC')
    item = approvals.propose('owner', 'routine_migration', {'timezone': 'UTC', 'config_revision': plan['config_revision']}, '迁移')
    original, fail = StaffRegistry._write, [True]
    def interrupted(self, name, data):
        if name == 'members.json' and fail[0]:
            fail[0] = False
            raise OSError('fixture write interruption')
        return original(self, name, data)
    monkeypatch.setattr(StaffRegistry, '_write', interrupted)
    with pytest.raises(OSError):
        owner_decide(approvals, item['id'], True, item['digest'])
    assert approvals.get(item['id'])['status'] == 'applying'
    assert legacy_owner(str(tmp_path), 'old')['status'] == 'applying'
    assert cron.due_jobs(str(tmp_path), datetime(2026, 11, 11, 10)) == []
    assert migration.routines.dispatch(None, datetime.fromisoformat('2026-11-11T10:00:00+00:00')) == []
    owner_decide(approvals, item['id'], True, item['digest'])
    assert len(StaffRegistry(str(tmp_path)).members()) == 1
    assert legacy_owner(str(tmp_path), 'old')['status'] == 'complete'


def test_routine_management_api_requires_owner_and_proposals_do_not_apply_themselves(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.server import app
    from src.gateway.main_conversation import claim_main_chat
    registry, routines, data = registered(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VORTOONE_API_TOKEN', 'fixture-admin')
    monkeypatch.setenv('VORTOONE_WORKSPACE_SCOPE', 'general')
    client, headers = TestClient(app), {'Authorization': 'Bearer fixture-admin'}
    assert client.get('/api/staff/routines').status_code == 401
    assert client.put('/api/staff/routines', json={'action': 'timezone', 'data': {'timezone': 'UTC', 'revision': 1}}).status_code == 401
    assert client.put('/api/staff/routines', headers=headers, json={'action': 'timezone', 'data': {'timezone': 'UTC', 'revision': True}}).status_code == 409
    claim_main_chat(str(tmp_path), 'sid-owner')
    proposed = client.put('/api/staff/routines', headers=headers, json={'action': 'propose', 'data': {'definition': data, 'reason': '每天核对'}})
    assert proposed.status_code == 200 and routines.list() == []
    item = proposed.json()
    assert client.post(f"/api/staff/proposals/{item['id']}/decision", headers=headers,
        json=proposal_body(item)).status_code == 200
    current = routines.get(data['id'])
    assert current['owner'] == 'sid-owner'
    assert client.put('/api/staff/routines', headers=headers,
        json={'action': 'delete', 'data': {'id': current['id'], 'revision': current['revision']}}).status_code == 200
    assert routines.get(current['id'])['retired'] is True


def test_default_timezone_is_explicit_and_changes_only_new_routines(tmp_path):
    _, routines, data = registered(tmp_path)
    with pytest.raises(ValueError, match='时区'):
        routines.register({k: v for k, v in data.items() if k != 'timezone'})
    routines.configure_timezone('UTC', 1)
    first = routines.register({k: v for k, v in data.items() if k != 'timezone'})
    routines.configure_timezone('Asia/Shanghai', 2)
    assert routines.get(first['id'])['timezone'] == 'UTC'
    assert routines.register({**{k: v for k, v in data.items() if k != 'timezone'}, 'id': 'routine-second'})['timezone'] == 'Asia/Shanghai'


async def test_prompt_total_timeout_records_failure_instead_of_only_timing_out_the_caller(tmp_path):
    from src.workflow.workbench.runs import RunLedger
    job = cron.CronJob('limited', cron.parse_schedule('every 1h'), prompt='hang', timeout=0.01)
    async def hang(*args, **kwargs): await asyncio.Event().wait()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(cron.run_job(str(tmp_path), job, run_session=hang), 2)
    records = RunLedger(str(tmp_path)).list()
    assert len(records) == 1 and records[0].status == 'failed' and 'TimeoutError' in records[0].error


def test_two_preexisting_cron_state_instances_do_not_erase_each_others_claims(tmp_path):
    first, second = cron.CronState(str(tmp_path)), cron.CronState(str(tmp_path))
    now = datetime(2026, 11, 11, 9)
    first.mark('one', now); second.mark('two', now)
    assert cron.CronState(str(tmp_path)).last_run('one') == now
    assert cron.CronState(str(tmp_path)).last_run('two') == now


def test_restart_parks_the_original_queued_task_without_replaying_it(tmp_path):
    from src.workflow.staff.service import StaffService
    from src.workflow.tasks.tasks import TaskLedger
    _, routines, data = registered(tmp_path)
    routines.register(data)
    class Pool:
        def enqueue_existing(self, *args, **kwargs): return True
        def notify_existing(self, *args): pass
    staff = StaffService(str(tmp_path), Pool(), execute=None)
    ids = routines.dispatch(staff, datetime.fromisoformat('2026-11-11T09:01:00+00:00'))
    assert TaskLedger(str(tmp_path)).load(ids[0]).status == 'queued'
    routines.recover()
    assert TaskLedger(str(tmp_path)).load(ids[0]).status == 'interrupted'
    assert routines.dispatch(staff, datetime.fromisoformat('2026-11-12T09:01:00+00:00')) == []
    assert '重启' in routines.get(data['id'])['state']['error']


async def test_a_slow_cron_tick_does_not_block_heartbeat(tmp_path, monkeypatch):
    from src.scheduling import scheduler, heartbeat
    monkeypatch.setenv('VORTOONE_CRON', '1'); monkeypatch.setenv('VORTOONE_HEARTBEAT', '1')
    stop, called = asyncio.Event(), []
    async def slow(*args, **kwargs):
        called.append('cron'); await asyncio.Event().wait()
    async def beat(*args, **kwargs): called.append('heartbeat'); stop.set()
    monkeypatch.setattr(cron, 'run_due', slow)
    monkeypatch.setattr(heartbeat, 'run_heartbeat', beat)
    monkeypatch.setattr(heartbeat, 'heartbeat_every_seconds', lambda: 0)
    await asyncio.wait_for(scheduler.scheduler_loop(stop, cwd=str(tmp_path), runner=None, notify=None), 2)
    assert called == ['cron', 'heartbeat']


async def test_old_cron_work_uses_the_existing_global_capacity(tmp_path):
    from src.workflow.tasks.tasks import TaskRunner
    write_jobs(tmp_path, 'jobs:\n  - name: old\n    schedule: every 1h\n    prompt: old\n')
    pool, called = TaskRunner(str(tmp_path), None, max_concurrent=1), []
    async def execute(*args, **kwargs): called.append(True); return 'done'
    try:
        async with pool.background_capacity():
            job = asyncio.create_task(cron.run_due(str(tmp_path), datetime(2026, 11, 11, 9), run_session=execute, capacity=pool.background_capacity))
            for _ in range(4): await asyncio.sleep(0)
            assert called == []
        assert await asyncio.wait_for(job, 2) == ['old'] and called == [True]
    finally:
        await pool.shutdown()


@pytest.mark.parametrize('private', [True, False])
def test_compatibility_role_has_exact_old_tools_and_never_combines_private_context_with_web(tmp_path, monkeypatch, private):
    from src.agents.staff import build_staff_member
    from src.workflow.staff.roles import routine_roles
    from src.workflow.staff.registry import StaffRegistry
    import src.memory.assistant_profile as profile
    monkeypatch.setattr(profile, 'assistant_system_parts', lambda root: ['PRIVATE_FIXTURE_MEMORY'])
    monkeypatch.setattr(profile, 'load_profile', lambda root: {'name': '公开名字', 'soul': '公开口吻', 'about_you': 'PRIVATE_FIXTURE_MEMORY'})
    role = next(r for r in routine_roles() if r['id'] == ('routine-offline' if private else 'routine-public'))
    StaffRegistry(str(tmp_path)).validate_role(role, builtin=True)
    captured = {}
    class Agent:
        def __init__(self, tools, **kwargs): captured.update(tools=tools, **kwargs)
    build_staff_member(str(tmp_path), role, {'model': ''}, check=lambda: None, llm=object(), agent_factory=Agent)
    assert {tool.name for tool in captured['tools']} == set(role['grants']['tools'])
    assert ('PRIVATE_FIXTURE_MEMORY' in captured['extra_system']) is private
    assert all(tool.operation_kind == 'read' for tool in captured['tools'])
    assert not {'routine_propose', 'staff_assign', 'cron_add'} & {tool.name for tool in captured['tools']}


@pytest.mark.parametrize('level,tainted,can_ask,answer', [
    ('ask', False, True, True), ('reads', False, True, True), ('full', False, True, True),
    ('full', False, True, False), ('full', True, True, True), ('full', False, False, True),
])
async def test_routine_proposal_obeys_owner_confirmation_before_registration(tmp_path, level, tainted, can_ask, answer):
    from src.agents.gate import make_confirm_gate
    from src.agents.staff_proposals import build_routine_proposal_tool
    from src.agents.taint import mark_tainted
    _, routines, data = registered(tmp_path)
    asked = []
    async def human(message, kind):
        assert routines.list() == []
        asked.append(kind)
        return answer
    tool = build_routine_proposal_tool(str(tmp_path), owner='owner', confirm=make_confirm_gate(
        human, trust_level=level, auto_approve=not can_ask, can_ask_human=can_ask))
    if tainted:
        mark_tainted()
    await tool.handler({'data': data, 'reason': '每天核对资料'})
    assert asked == (['execute'] if can_ask and not tainted else [])
    assert routines.list() == []
    from src.workflow.staff.approvals import StaffApprovals
    assert len(StaffApprovals(str(tmp_path)).list()) == int(can_ask and not tainted and answer)


def test_approved_migration_cannot_apply_a_changed_source(tmp_path, monkeypatch):
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.routine_migration import RoutineMigration, legacy_owner
    monkeypatch.setenv('VORTOONE_WORKSPACE_SCOPE', 'general')
    write_jobs(tmp_path, 'jobs:\n  - name: old\n    schedule: every 1h\n    prompt: original\n')
    migration, approvals = RoutineMigration(str(tmp_path)), StaffApprovals(str(tmp_path))
    plan = migration.plan('UTC')
    proposal = approvals.propose('owner', 'routine_migration', {'timezone': 'UTC', 'config_revision': plan['config_revision']}, '迁移')
    write_jobs(tmp_path, 'jobs:\n  - name: old\n    schedule: every 1h\n    prompt: changed\n')
    with pytest.raises(ValueError, match='变化'):
        owner_decide(approvals, proposal['id'], True, proposal['digest'])
    assert migration.routines.list() == [] and approvals.registry.members() == []
    assert legacy_owner(str(tmp_path), 'old') is None


async def test_migrated_silent_work_keeps_receipts_and_never_revives_after_deletion(tmp_path, monkeypatch):
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.routine_migration import RoutineMigration
    from src.workflow.staff.service import StaffService
    from src.workflow.tasks.tasks import TaskRunner
    from src.workflow.workbench.runs import RunLedger
    from src.scheduling.task_notices import message
    monkeypatch.setenv('VORTOONE_WORKSPACE_SCOPE', 'general')
    write_jobs(tmp_path, 'jobs:\n  - name: old\n    schedule: every 1h\n    prompt: original\n    announce: silent\n')
    migration, approvals = RoutineMigration(str(tmp_path)), StaffApprovals(str(tmp_path))
    plan = migration.plan('UTC')
    proposal = approvals.propose('owner', 'routine_migration', {'timezone': 'UTC', 'config_revision': plan['config_revision']}, '迁移')
    owner_decide(approvals, proposal['id'], True, proposal['digest'])
    routines, pool = migration.routines, TaskRunner(str(tmp_path), None)
    async def execute(*args): return '已完成合成核对'
    staff = StaffService(str(tmp_path), pool, execute=execute)
    try:
        ids = routines.dispatch(staff, datetime.fromisoformat(routines.list()[0]['state']['cursor']) + timedelta(hours=1))
        await asyncio.wait_for(pool.join(ids[0]), 2)
        task = pool.ledger.load(ids[0])
        assert task.status == 'done' and message(task, root=str(tmp_path)) is None
        routines.settle(task); routines.settle(task)
        assert len(RunLedger(str(tmp_path)).list()) == 1
        assert cron.CronState(str(tmp_path)).failures('old') == 0
        item = routines.list()[0]
        routines.control(item['id'], item['revision'], retire=True)
        assert cron.due_jobs(str(tmp_path), datetime(2026, 11, 12, 12)) == []
        with pytest.raises(cron.CronEditError, match='删除'):
            await cron.run_job_by_name(str(tmp_path), 'old')
    finally:
        await pool.shutdown()


async def test_a_task_id_conflict_parks_only_its_routine_without_replaying(tmp_path):
    from src.workflow.staff.service import StaffService
    from src.workflow.tasks.tasks import TaskRunner
    _, routines, data = registered(tmp_path)
    routines.register(data)
    pool, calls = TaskRunner(str(tmp_path), None), []
    async def execute(task, *args): calls.append(task.id); return '完成'
    staff = StaffService(str(tmp_path), pool, execute=execute)
    now = datetime.fromisoformat('2026-11-11T09:01:00+00:00')
    try:
        first = routines.dispatch(staff, now, manual=True)
        await asyncio.wait_for(pool.join(first[0]), 2)
        routines.register({**data, 'id': 'routine-second'})
        second = routines.dispatch(staff, now, manual=True)
        assert len(second) == 1 and second != first
        await asyncio.wait_for(pool.join(second[0]), 2)
        assert calls == first + second
        assert '已存在' in routines.get(data['id'])['state']['error']
    finally:
        await pool.shutdown()
