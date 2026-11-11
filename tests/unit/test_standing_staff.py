"""S8: standing suggestions use the existing owner approvals, tasks and routines."""
import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta

import pytest


def request(specialist='calendar', **extra):
    return {'specialist': specialist, 'timezone': 'Asia/Shanghai',
            'times': ['08:00'] if specialist != 'mail' else ['09:15', '17:45'],
            **({'topic': '公开资料中的新进展', 'weekday': 1} if specialist == 'research' else {}), **extra}


def test_three_plans_declare_exact_separate_authority_without_registering_anything(tmp_path):
    from src.workflow.staff.standing import standing_catalog, standing_plan
    from src.workflow.staff.registry import StaffRegistry
    assert {item['id'] for item in standing_catalog()} == {'calendar', 'mail', 'research'}
    expected = {'calendar': (['calendar_events'], ['plugin:calendar'], False),
                'mail': (['mail_list', 'mail_read', 'mail_draft'], ['plugin:mail'], False),
                'research': (['web_search', 'web_fetch'], [], True)}
    registry = StaffRegistry(str(tmp_path))
    for specialist, (tools, sees, web) in expected.items():
        plan = standing_plan(str(tmp_path), request(specialist))
        role = plan['steps'][0]['definition']
        assert role['builtin'] is True and plan['steps'][0]['data'] == {'id': role['id'], 'install': True}
        grants = registry.validate_role(role, builtin=True)['grants']
        assert grants == {'operations': ['read', 'draft'] if specialist == 'mail' else ['read'],
                          'tools': tools, 'sees': sees, 'web': web, 'project': False}
        assert role['engine'] == ['vortocode']
        assert [s['state'] for s in plan['steps']] == ['ready'] + ['blocked'] * (len(plan['steps']) - 1)
        routines = [s['data'] for s in plan['steps'] if s['kind'] == 'routine']
        assert all(r['timezone'] == 'Asia/Shanghai' and r['delivery'] == 'notify' for r in routines)
        assert [r['schedule'] for r in routines] == (['at 09:15', 'at 17:45'] if specialist == 'mail' else
                                                     ['0 8 * * 1'] if specialist == 'research' else ['at 08:00'])
        assert not (tmp_path / '.vortoone' / 'staff').exists()


@pytest.mark.parametrize('change', [{'grants': {}}, {'model': 'override'}, {'engine': 'opencode'},
    {'member': 'other'}, {'role': 'other'}, {'timezone': ''}, {'times': []}, {'times': ['25:00']},
    {'times': ['08:00', '08:00']}, {'topic': 'private'}, {'weekday': True}])
def test_plans_refuse_authority_overrides_and_incomplete_owner_choices(tmp_path, change):
    from src.workflow.staff.standing import standing_plan
    with pytest.raises(ValueError):
        standing_plan(str(tmp_path), request(**change))


def approve(approvals, step, sign):
    proposal = approvals.propose('owner', step['kind'], step['data'], '按主人核对的方案登记')
    assert proposal['requires_desktop_key'] is True
    with pytest.raises(ValueError, match='批准钥匙'):
        approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True)
    return approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True,
                            signature=sign(approvals.approval_message(proposal)))


@pytest.mark.parametrize('specialist', ['calendar', 'mail', 'research'])
def test_each_step_becomes_available_only_after_the_previous_owner_approval(tmp_path, specialist):
    from src.workflow.staff.standing import standing_plan
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.registry import StaffRegistry
    from src.workflow.staff.routines import Routines
    from tests.unit.test_staff_contract import staff_key
    approvals = StaffApprovals(str(tmp_path))
    _, sign = staff_key(approvals)
    args = request(specialist)
    original = standing_plan(str(tmp_path), args)
    count = len(original['steps'])
    for index in range(count):
        plan = standing_plan(str(tmp_path), args)
        assert [step['state'] for step in plan['steps'][:index]] == ['existing'] * index
        step = plan['steps'][index]
        assert step['state'] == 'ready'
        proposal = approvals.propose('owner', step['kind'], step['data'], '按主人核对的方案登记')
        assert standing_plan(str(tmp_path), args)['steps'][index]['state'] == 'pending'
        assert len(Routines(str(tmp_path)).list()) == max(0, index - 3)
        approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True,
                         signature=sign(approvals.approval_message(proposal)))
    assert all(step['state'] == 'existing' for step in standing_plan(str(tmp_path), args)['steps'])
    member = StaffRegistry(str(tmp_path)).members()[0]
    assert member['department'] and StaffRegistry(str(tmp_path)).department(member['department'])['members'] == [member['id']]
    assert len(Routines(str(tmp_path)).list()) == (2 if specialist == 'mail' else 1)


def test_existing_registration_is_never_silently_overwritten_or_reenabled(tmp_path):
    from src.workflow.staff.standing import standing_plan
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(tmp_path))
    role = deepcopy(standing_plan(str(tmp_path), request())['steps'][0]['definition'])
    role['builtin'] = False
    role['brief'] = '主人自己的原说明'
    saved = registry.register_role(role)
    plan = standing_plan(str(tmp_path), request())
    assert plan['steps'][0]['state'] == 'conflict' and plan['steps'][0]['data'] is None
    assert all(step['state'] == 'blocked' for step in plan['steps'][1:])
    assert registry.role(saved['id']) == saved
    registry.set_role_enabled(saved['id'], False)
    assert standing_plan(str(tmp_path), request())['steps'][0]['state'] == 'conflict'


async def test_main_agent_can_read_a_plan_but_no_specialist_can_acquire_the_planner(tmp_path):
    from src.agents.staff import build_staff_tools
    from src.agents.gate import make_confirm_gate
    from src.workflow.staff.registry import StaffRegistry
    tools = {tool.name: tool for tool in build_staff_tools(str(tmp_path), owner='owner', confirm=make_confirm_gate())}
    result = json.loads(await tools['staff_plan'].handler(request('mail')))
    assert result['steps'][0]['kind'] == 'role' and tools['staff_plan'].operation_kind == 'read'
    assert StaffRegistry(str(tmp_path)).members() == []
    with pytest.raises(ValueError):
        StaffRegistry(str(tmp_path)).register_role({'id': 'bad-planner', 'title': '不应存在', 'brief': '试图替主人开专员',
            'grants': {'operations': ['read'], 'tools': ['staff_plan'], 'sees': [], 'web': False, 'project': False},
            'engine': ['vortocode'], 'max_steps': 12, 'timeout_seconds': 600})


async def test_calendar_routine_queries_the_owner_day_in_its_explicit_timezone(monkeypatch):
    from src.plugins import calendar_ics as cal
    seen = []
    async def load(local): return object()
    def occurrences(calendar, first, last):
        seen.append((first, last)); return [], False
    monkeypatch.setattr(cal, 'load_calendar', load)
    monkeypatch.setattr(cal, 'occurrences', occurrences)
    text = await cal.events_text({'date': 'today', 'timezone': 'Asia/Shanghai'},
                                now=datetime.fromisoformat('2026-11-10T20:00:00+00:00'))
    assert seen[0][0].isoformat() == '2026-11-11T00:00:00+08:00'
    assert seen[0][1] - seen[0][0] == timedelta(days=1)
    assert 'Asia/Shanghai' in text


@pytest.mark.parametrize('specialist', ['calendar', 'mail', 'research'])
async def test_approved_standing_routine_reads_its_source_in_the_shared_pool_then_main_reports(tmp_path, monkeypatch, specialist):
    from src.agents import staff
    from src.agents.agent_loop import MainAgent
    from src.agents.delegation import build_research_tools
    from src.agents.gate import make_confirm_gate
    from src.agents.taint import is_tainted
    from src.llm.client import add_usage
    from src.plugins import calendar_ics, mail
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.routines import Routines
    from src.workflow.staff.service import StaffService
    from src.workflow.tasks.tasks import TaskRunner
    from tests.unit.test_staff_contract import staff_key
    approvals = StaffApprovals(str(tmp_path)); _, sign = staff_key(approvals)
    from src.workflow.staff.standing import standing_plan
    for _ in range(5):
        ready = next((s for s in standing_plan(str(tmp_path), request(specialist))['steps'] if s['state'] == 'ready'), None)
        if ready is None: break
        approve(approvals, ready, sign)
    read = []
    async def calendar(args): read.append(('calendar_events', args)); return '合成日程：10:00 核对资料'
    def messages(*args):
        read.append(('mail_list', args))
        return {'items': [{'uid': '123', 'unread': True, 'from': '合成来源', 'subject': '待核对', 'date': None}], 'total': 1, 'scanned': 1}
    def message(uid): read.append(('mail_read', uid)); return {'body': '合成资料，请主人核对后处理'}
    def search(query): read.append(('web_search', query)); return '合成来源 https://example.org/source'
    monkeypatch.setattr(calendar_ics, 'events_text', calendar)
    monkeypatch.setattr(mail, 'list_messages', messages); monkeypatch.setattr(mail, 'read_message', message)
    monkeypatch.setattr('src.agents.web_search.web_search', search)
    calls = {'calendar': [('calendar_events', {'date': 'today', 'timezone': 'Asia/Shanghai'})],
             'mail': [('mail_list', {'unread_only': 'true', 'days': 1}), ('mail_read', {'uid': '123'})],
             'research': [('web_search', {'query': '公开资料中的新进展'})]}[specialist]
    report = '合成例行汇报：' + specialist
    observed = []
    class FakeLLM:
        def __init__(self, sequence): self.sequence, self.index = sequence, 0
        async def chat(self, messages, **kwargs):
            observed.append((self.index, is_tainted(), str(messages)))
            add_usage(10, 2, model='offline-fixture')
            if self.index < len(self.sequence):
                name, args = self.sequence[self.index]
                text = json.dumps({'tool': name, 'args': args})
            else: text = report
            self.index += 1
            return {'content': text, 'tool_calls': []}
        async def close(self): pass
    original = staff.build_staff_member
    def factory(root, role, member, **kwargs):
        agent = original(root, role, member, **kwargs, llm=FakeLLM(calls))
        assert set(agent.tools) == set(role['grants']['tools'])
        return agent
    monkeypatch.setattr(staff, 'build_staff_member', factory)
    pool = TaskRunner(str(tmp_path), None)
    service = StaffService(str(tmp_path), pool, execute=None)
    service.execute = lambda task, work, role, member, check: staff.execute_staff(str(tmp_path), task, work, role, member, check, approval_service=service)
    try:
        routines = Routines(str(tmp_path))
        item = routines.list()[0]
        now = datetime.fromisoformat(item['state']['cursor']) + timedelta(days=8)
        ids = routines.dispatch(service, now, only=item['id'])
        assert len(ids) == 1
        await asyncio.wait_for(pool.join(ids[0]), 3)
        task = pool.ledger.load(ids[0])
        assert task.status == 'done', task.error
        assert task.result == report and task.kind == 'delegation'
        assert [name for name, _ in read] == [name for name, _ in calls]
        assert task.collaboration['dispatch']['department'] == 'dept-' + specialist
        assert task.collaboration['usage']['complete'] and task.collaboration['usage']['tokens'] == 12 * (len(calls) + 1)
        assert any(index > 0 and tainted for index, tainted, _ in observed)
        tools = build_research_tools(str(tmp_path), confirm=make_confirm_gate(), collaboration=service.collaboration('owner'))
        parent = MainAgent([t for t in tools if t.name == 'task_status'],
            llm=FakeLLM([('task_status', {'task_id': task.id})]), native=False, auto_recall=False, env_context=False)
        assert await parent.run_turn('核对专员的交付，向我汇报。', mode='plan') == report
        assert report in str(parent.history) and is_tainted()
    finally:
        await pool.shutdown()


@pytest.mark.parametrize('answer', [True, False])
async def test_standing_mail_can_save_a_draft_only_after_the_current_owner_card(tmp_path, monkeypatch, answer):
    from src.agents.staff import build_staff_member
    from src.plugins import mail
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.service import StaffService
    from src.workflow.staff.standing import standing_plan
    from src.workflow.tasks.tasks import TaskRunner
    from tests.unit.test_staff_contract import staff_key
    approvals = StaffApprovals(str(tmp_path)); _, sign = staff_key(approvals)
    for _ in range(5):
        ready = next((s for s in standing_plan(str(tmp_path), request('mail'))['steps'] if s['state'] == 'ready'), None)
        if ready is None: break
        approve(approvals, ready, sign)
    saved, results, waiting = [], [], asyncio.Queue()
    def save(draft): saved.append(deepcopy(draft)); return '合成草稿箱'
    monkeypatch.setattr(mail, 'save_draft', save)
    seen = set()
    def update(task):
        card = task.collaboration.get('approval') or {}
        if card.get('status') == 'pending' and card['payload']['id'] not in seen:
            seen.add(card['payload']['id']); waiting.put_nowait((task, deepcopy(card)))
    async def execute(task, work, role, member, check):
        async def confirm(call): return await service.confirm_action(task, role, member, call)
        agent = build_staff_member(str(tmp_path), role, member, check=check, llm=object(), confirm_action=confirm)
        results.append(await agent._run_tool('mail_draft', {'to': 'reader@example.org', 'subject': '合成主题', 'body': '合成草稿正文'}, 'build', lambda _: None))
        return '已核对本次草稿操作的结果'
    pool = TaskRunner(str(tmp_path), None)
    service = StaffService(str(tmp_path), pool, execute=execute, on_update=update)
    try:
        task = service.assign('owner', {'member': 'staff-mail', 'prompt': '起草合成回复，但不发送。'})
        current, card = await asyncio.wait_for(waiting.get(), 2)
        assert current.id == task.id and current.status == 'blocked' and saved == []
        assert card['payload']['args']['body'] == '合成草稿正文'
        service.actions.decide(task.id, card['payload']['id'], approve=answer,
                               expected_digest=card['digest'], reason='' if answer else '这次不保存')
        await asyncio.wait_for(pool.join(task.id), 2)
        assert len(saved) == int(answer) and pool.ledger.load(task.id).status == 'done'
        assert ('没有发送' in results[0]) if answer else ('没存' in results[0] or '拒绝' in results[0])
    finally:
        await pool.shutdown()


@pytest.mark.parametrize('change', [{'topic': ''}, {'weekday': None}, {'weekday': True}, {'weekday': 8}, {'times': ['08:00', '09:00']}])
def test_research_plan_requires_owner_topic_and_one_explicit_weekly_time(tmp_path, change):
    from src.workflow.staff.standing import standing_plan
    with pytest.raises(ValueError):
        standing_plan(str(tmp_path), request('research', **change))


@pytest.mark.parametrize('data', [{'id': 'not-shipped', 'install': True}, {'id': 'mail-reader', 'install': False},
    {'id': 'mail-reader', 'install': True, 'grants': {'web': True}}])
def test_builtin_install_does_not_accept_a_model_supplied_definition(tmp_path, data):
    from src.workflow.staff.approvals import StaffApprovals
    approvals = StaffApprovals(str(tmp_path))
    with pytest.raises(ValueError):
        approvals.propose('owner', 'role', data, '不应登记')
    assert {r['id'] for r in approvals.registry.roles()} == {'web-researcher', 'reviewer', 'developer'}


def test_same_named_custom_role_is_preserved_even_when_its_tools_match_the_suggestion(tmp_path):
    from src.workflow.staff.standing import standing_plan
    from src.workflow.staff.registry import StaffRegistry
    data = standing_plan(str(tmp_path), request('mail'))['steps'][0]['definition']
    data['builtin'] = False
    registry = StaffRegistry(str(tmp_path))
    saved = registry.register_role(data)
    assert registry.role(saved['id']) == saved and saved['builtin'] is False
    assert standing_plan(str(tmp_path), request('mail'))['steps'][0]['state'] == 'conflict'
    assert registry.role(saved['id']) == saved


def test_interrupted_builtin_install_resumes_only_the_original_signed_receipt_and_is_immutable(tmp_path, monkeypatch):
    from src.workflow.staff.standing import standing_plan
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.registry import StaffRegistry
    from tests.unit.test_staff_contract import staff_key
    approvals = StaffApprovals(str(tmp_path)); _, sign = staff_key(approvals)
    step = standing_plan(str(tmp_path), request())['steps'][0]
    proposal = approvals.propose('owner', 'role', step['data'], '登记日程岗位')
    signature = sign(approvals.approval_message(proposal))
    original = StaffRegistry._write
    def fail(self, name, data):
        if name == 'roles.json': raise OSError('合成保存失败')
        return original(self, name, data)
    with monkeypatch.context() as patch:
        patch.setattr(StaffRegistry, '_write', fail)
        with pytest.raises(OSError):
            approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True, signature=signature)
    assert approvals.get(proposal['id'])['status'] == 'applying'
    assert 'calendar-reader' not in {r['id'] for r in approvals.registry.roles()}
    result = approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True)
    assert result['status'] == 'approved'
    assert approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True) == result
    role = approvals.registry.role('calendar-reader')
    with pytest.raises(ValueError, match='内置'):
        approvals.registry.register_role({**role, 'brief': '试图改写'})
    assert len([r for r in approvals.registry.roles() if r['id'] == role['id']]) == 1
    paused = approvals.registry.set_role_enabled(role['id'], False)
    assert paused['enabled'] is False
