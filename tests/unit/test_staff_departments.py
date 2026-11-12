"""S5: owner registration, source boundaries, notes and independent department execution."""
import asyncio
import json

import pytest

from tests.unit.staff_owner_helpers import owner_decide

from src.workflow.staff.approvals import StaffApprovals
from src.workflow.staff.notes import StaffNotes
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskLedger, TaskRunner


def specialist(registry, name, *, sees=(), web=False, operations=('read',), tools=()):
    role = registry.register_role({'id': name + '-role', 'title': name, 'brief': '整理交给它的材料',
        'grants': {'operations': list(operations), 'tools': list(tools), 'sees': list(sees), 'web': web, 'project': False},
        'engine': ['vortocode'], 'max_steps': 8, 'timeout_seconds': 600})
    return registry.register_member({'id': 'staff-' + name, 'name': name, 'role': role['id'], 'engine': 'vortocode'})


def department(registry, name, members=(), **kwargs):
    return registry.register_department({'id': 'dept-' + name, 'name': name, 'duty': '整理材料',
        'members': [x['id'] for x in members], 'lead': '', 'limits': {'parallel': 1, 'daily_tokens': 0}, **kwargs})


def test_department_membership_has_one_authority_and_cannot_join_two_groups(tmp_path):
    registry = StaffRegistry(str(tmp_path))
    member = specialist(registry, 'reader')
    original = registry.member(member['id'])
    dept = department(registry, 'reading', [member])
    assert registry.member(member['id'])['department'] == dept['id']
    with pytest.raises(ValueError, match='其它部门'):
        department(registry, 'other', [member])
    registry.register_department({**dept, 'members': []})
    assert registry.member(member['id']) == original


def test_department_and_member_limits_reject_invalid_counts(tmp_path):
    registry = StaffRegistry(str(tmp_path))
    members = [specialist(registry, 'reader-' + str(i)) for i in range(7)]
    with pytest.raises(ValueError, match='6 位'):
        department(registry, 'too-many', members)
    with pytest.raises(ValueError, match='并发'):
        department(registry, 'bad', limits={'parallel': True, 'daily_tokens': 0})
    for n in range(8):
        department(registry, str(n))
    with pytest.raises(ValueError, match='8 个'):
        department(registry, 'ninth')


@pytest.mark.parametrize('other', ['web', 'empty'])
def test_private_department_cannot_share_notes_with_incompatible_roles(tmp_path, other):
    registry = StaffRegistry(str(tmp_path))
    private = specialist(registry, 'mail', sees=['plugin:mail'])
    public = specialist(registry, 'public', web=other == 'web')
    with pytest.raises(ValueError, match='资料边界'):
        department(registry, 'mixed', [private, public])


def test_department_proposal_grants_nothing_until_owner_confirms_exact_members(tmp_path):
    registry = StaffRegistry(str(tmp_path)); member = specialist(registry, 'reader')
    approvals = StaffApprovals(str(tmp_path))
    data = {'id': 'dept-reading', 'name': '资料', 'duty': '整理公开材料', 'members': [member['id']]}
    proposal = approvals.propose('owner', 'department', data, '长期整理')
    assert proposal['approval_level'] == 'normal' and registry.departments() == []
    with pytest.raises(ValueError, match='主人'):
        approvals.decide(proposal['id'], True, proposal['digest'])
    owner_decide(approvals, proposal['id'], True, proposal['digest'])
    assert registry.member(member['id'])['department'] == 'dept-reading'
    assert owner_decide(approvals, proposal['id'], True, proposal['digest'])['status'] == 'approved'


def test_department_proposal_is_invalidated_by_member_or_role_change(tmp_path):
    registry = StaffRegistry(str(tmp_path)); member = specialist(registry, 'reader')
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'department', {'id': 'dept-reading', 'name': '资料', 'duty': '整理', 'members': [member['id']]}, '长期整理')
    registry.set_member_status(member['id'], 'paused', member['revision'])
    with pytest.raises(ValueError, match='版本已变化'):
        owner_decide(approvals, proposal['id'], True, proposal['digest'])
    assert registry.departments() == []


def test_private_department_requires_owner_route_confirmation(tmp_path):
    registry = StaffRegistry(str(tmp_path)); member = specialist(registry, 'mail', sees=['plugin:mail'])
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'department', {'id': 'dept-mail', 'name': '邮件', 'duty': '只读', 'members': [member['id']]}, '长期整理')
    assert proposal['approval_level'] == 'desktop'
    with pytest.raises(ValueError, match='主人写入路由'):
        approvals.decide(proposal['id'], True, proposal['digest'])
    assert registry.departments() == []


def test_suggestions_never_write_notes_until_selected_and_public_content_is_explicit(tmp_path):
    registry = StaffRegistry(str(tmp_path)); member = specialist(registry, 'reader')
    dept = department(registry, 'public', [member]); notes = StaffNotes(str(tmp_path))
    notes.propose(dept['id'], ['先写结论', '附上来源'], task_id='task-0123456789', round=1, member=member['id'], sources=[])
    assert notes.snapshot(dept['id'])['text'] == ''
    assert not (registry.directory / 'notes' / (dept['id'] + '.md')).exists()
    pending = notes.pending(dept['id'])
    choices = {x['id']: {'digest': x['digest'], 'approve': i == 0} for i, x in enumerate(pending)}
    with pytest.raises(ValueError, match='公开'):
        notes.decide(dept['id'], choices, 0)
    saved = notes.decide(dept['id'], choices, 0, public=True)
    assert saved['revision'] == 1 and '先写结论' in saved['text'] and '附上来源' not in saved['text']
    assert saved['tainted'] and notes.pending(dept['id']) == []
    assert notes.decide(dept['id'], choices, 0, public=True) == saved
    assert (registry.directory / 'notes' / (dept['id'] + '.md')).read_text() == saved['text']


def test_notes_cannot_expand_private_sources_or_reclassify_external_material(tmp_path):
    registry = StaffRegistry(str(tmp_path))
    mail = specialist(registry, 'mail', sees=['plugin:mail'])
    calendar = specialist(registry, 'calendar', sees=['plugin:calendar'])
    dept = department(registry, 'daily', [mail, calendar]); notes = StaffNotes(str(tmp_path))
    notes.propose(dept['id'], ['邮件里来的建议'], task_id='task-0123456789', round=1, member=mail['id'], sources=['plugin:mail'])
    item = notes.pending(dept['id'])[0]
    with pytest.raises(ValueError, match='来源超出'):
        notes.decide(dept['id'], {item['id']: {'approve': True, 'digest': item['digest']}}, 0)
    assert notes.snapshot(dept['id'])['revision'] == 0


def test_owner_preferences_save_without_model_or_suggestion_and_remain_versioned(tmp_path):
    registry = StaffRegistry(str(tmp_path)); member = specialist(registry, 'mail', sees=['plugin:mail'])
    dept = department(registry, 'daily', [member]); notes = StaffNotes(str(tmp_path))
    current = notes.decide(dept['id'], {}, 0, owner_text='摘要先写待回的信')
    assert current['revision'] == 1 and not current['tainted']
    with pytest.raises(ValueError, match='版本'):
        notes.decide(dept['id'], {}, 0, owner_text='新偏好')


@pytest.mark.parametrize('failure', ['markdown', 'receipt'])
def test_interrupted_note_save_requires_same_owner_decision_and_recovers_once(tmp_path, monkeypatch, failure):
    registry = StaffRegistry(str(tmp_path)); member = specialist(registry, 'reader')
    dept = department(registry, 'public', [member]); notes = StaffNotes(str(tmp_path))
    with monkeypatch.context() as patch:
        if failure == 'markdown':
            patch.setattr(notes, '_markdown', lambda *args: (_ for _ in ()).throw(OSError('fixture storage error')))
        else:
            write = notes.registry._write
            def fail_final(name, state):
                if name.startswith('notes-') and state.get('current', {}).get('revision') == 1 and not state.get('applying'):
                    raise OSError('fixture receipt error')
                return write(name, state)
            patch.setattr(notes.registry, '_write', fail_final)
        with pytest.raises(OSError):
            notes.decide(dept['id'], {}, 0, owner_text='公开偏好', public=True)
    with pytest.raises(ValueError, match='保存中断'):
        notes.snapshot(dept['id'])
    with pytest.raises(ValueError, match='原决定'):
        notes.decide(dept['id'], {}, 0, owner_text='另一条', public=True)
    saved = notes.decide(dept['id'], {}, 0, owner_text='公开偏好', public=True)
    assert saved['revision'] == 1 and saved['text'].count('公开偏好') == 1


async def test_task_freezes_note_version_and_agent_only_proposes_changes(tmp_path):
    registry = StaffRegistry(str(tmp_path)); member = specialist(registry, 'reader')
    dept = department(registry, 'public', [member]); notes = StaffNotes(str(tmp_path))
    saved = notes.decide(dept['id'], {}, 0, owner_text='先写结论', public=True)
    pool = TaskRunner(str(tmp_path), None)
    captured = []
    async def execute(task, *args):
        captured.append(task.collaboration['dispatch']['notes'])
        return json.dumps({'result': '交付结论', 'note_suggestions': ['建议附上出处']}, ensure_ascii=False)
    service = StaffService(str(tmp_path), pool, execute=execute)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '整理公开材料'})
        notes.decide(dept['id'], {}, 1, owner_text='每条注明日期', public=True)
        await pool.join(task.id)
        assert captured == [saved]
        assert TaskLedger(tmp_path).load(task.id).result == '交付结论'
        assert notes.snapshot(dept['id'])['revision'] == 2
        assert len(notes.pending(dept['id'])) == 1
    finally:
        await pool.shutdown()


async def test_followup_cannot_cross_departments_after_member_moves(tmp_path):
    registry = StaffRegistry(str(tmp_path)); member = specialist(registry, 'reader')
    original = department(registry, 'first', [member])
    pool = TaskRunner(str(tmp_path), None)
    seen = []
    async def execute(task, *args):
        seen.append(task.collaboration['dispatch']['department'])
        return '已交回'
    service = StaffService(str(tmp_path), pool, execute=execute)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '整理公开材料'})
        await pool.join(task.id)
        registry.register_department({**original, 'members': []})
        department(registry, 'second', [member])
        with pytest.raises(ValueError, match='部门.*变化'):
            service.followup('owner', task.id, 1, '补充来源')
        current = TaskLedger(tmp_path).load(task.id)
        assert current.collaboration['round'] == 1
        assert current.collaboration['dispatch']['department'] == original['id']
        assert seen == [original['id']]
    finally:
        await pool.shutdown()


async def test_department_capacity_does_not_take_global_slots_while_waiting(tmp_path):
    registry = StaffRegistry(str(tmp_path))
    one = specialist(registry, 'one'); two = specialist(registry, 'two'); other = specialist(registry, 'other')
    department(registry, 'first', [one, two]); department(registry, 'second', [other])
    entered, independent, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    pool = TaskRunner(str(tmp_path), None, max_concurrent=2)
    seen = []
    async def execute(task, *args):
        seen.append(task.prompt)
        if task.prompt == '第一项': entered.set(); await release.wait()
        if task.prompt == '独立项': independent.set()
        return '完成'
    service = StaffService(str(tmp_path), pool, execute=execute)
    try:
        first = service.assign('owner', {'member': one['id'], 'prompt': '第一项'})
        await asyncio.wait_for(entered.wait(), 2)
        second = service.assign('owner', {'member': two['id'], 'prompt': '第二项'})
        third = service.assign('owner', {'member': other['id'], 'prompt': '独立项'})
        await asyncio.wait_for(independent.wait(), 2)
        assert '第二项' not in seen
        release.set()
        await asyncio.gather(*(pool.join(task.id) for task in [first, second, third]))
        assert '第二项' in seen
    finally:
        release.set(); await pool.shutdown()


def test_department_notes_and_budget_routes_require_owner_credentials_and_strict_versions(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.server import app
    registry = StaffRegistry(str(tmp_path)); member = specialist(registry, 'reader')
    dept = department(registry, 'public', [member])
    monkeypatch.chdir(tmp_path); monkeypatch.setenv('VORTOONE_API_TOKEN', 'offline-owner-fixture')
    monkeypatch.setenv('VORTOONE_WORKSPACE_SCOPE', 'general')
    client = TestClient(app); headers = {'Authorization': 'Bearer offline-owner-fixture'}
    path = f"/api/staff/departments/{dept['id']}"
    for method, endpoint, body in [('GET', path, None), ('POST', path + '/notes/decision', {'owner_text': '公开偏好', 'public': True}),
        ('PUT', '/api/staff/budgets', {'revision': 1, 'daily_tokens': 100, 'task_tokens': 50})]:
        assert client.request(method, endpoint, json=body).status_code == 401
        assert client.request(method, endpoint, json=body, headers={'Authorization': 'Bearer device-fixture'}).status_code == 401
    detail = client.get(path, headers=headers)
    assert detail.status_code == 200 and detail.json()['notes']['snapshot']['revision'] == 0
    assert detail.json()['tasks_complete']
    result = client.post(path + '/notes/decision', headers=headers, json={'revision': 0, 'owner_text': '每条附出处', 'public': True})
    assert result.status_code == 200 and result.json()['revision'] == 1
    assert client.post(path + '/notes/decision', headers=headers, json={'revision': 0, 'owner_text': '新条目', 'public': True}).status_code == 409
    assert client.post(path + '/notes/decision', headers=headers, json={'revision': 1, 'owner_text': '条目', 'sources': []}).status_code == 422
    settings = client.get('/api/staff', headers=headers).json()['budget_settings']
    assert client.put('/api/staff/budgets', headers=headers,
        json={'revision': settings['revision'], 'daily_tokens': True, 'task_tokens': 0}).status_code == 422
    assert client.put('/api/staff/budgets', headers=headers,
        json={'revision': settings['revision'], 'daily_tokens': -1, 'task_tokens': 0}).status_code == 409
    assert client.put('/api/staff/budgets', headers=headers,
        json={'revision': settings['revision'], 'daily_tokens': 100, 'task_tokens': 50}).status_code == 200
    assert client.put('/api/staff/budgets', headers=headers,
        json={'revision': settings['revision'], 'daily_tokens': 0, 'task_tokens': 0}).status_code == 409


def test_note_digest_tampering_and_fulltext_changes_never_expand_saved_authority(tmp_path):
    registry = StaffRegistry(str(tmp_path)); member = specialist(registry, 'reader')
    dept = department(registry, 'public', [member]); notes = StaffNotes(str(tmp_path))
    notes.propose(dept['id'], ['原条目'], task_id='task-0123456789', round=1, member=member['id'], sources=[])
    item = notes.pending(dept['id'])[0]
    path = registry.directory / ('notes-' + dept['id'] + '.json')
    state = json.loads(path.read_text()); state['pending'][0]['text'] = '替换后的条目'; path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match='摘要'):
        notes.decide(dept['id'], {item['id']: {'approve': True, 'digest': item['digest']}}, 0, public=True)


async def test_real_staff_assembly_freezes_notes_and_keeps_external_notes_tainted(tmp_path, monkeypatch):
    from src.agents import staff
    from src.agents.taint import is_tainted, reset_taint
    from src.llm.client import add_usage
    registry = StaffRegistry(str(tmp_path)); member = specialist(registry, 'reader')
    dept = department(registry, 'public', [member]); notes = StaffNotes(str(tmp_path))
    notes.propose(dept['id'], ['外部资料须核对出处'], task_id='task-0123456789', round=1, member=member['id'], sources=[])
    suggestion = notes.pending(dept['id'])[0]
    notes.decide(dept['id'], {suggestion['id']: {'approve': True, 'digest': suggestion['digest']}}, 0, public=True)
    observed = []
    class FakeLLM:
        async def chat(self, messages, **kwargs):
            observed.append((is_tainted(), str(messages)))
            add_usage(10, 2, model='offline-fixture')
            return {'content': '按已确认笔记交回结论', 'tool_calls': []}
        async def close(self): pass
    build = staff.build_staff_member
    def factory(root, role, member, **kwargs):
        return build(root, role, member, **kwargs, llm=FakeLLM())
    monkeypatch.setattr(staff, 'build_staff_member', factory)
    pool = TaskRunner(str(tmp_path), None)
    service = StaffService(str(tmp_path), pool, execute=None)
    service.execute = lambda task, work, role, member, check: staff.execute_staff(str(tmp_path), task, work, role, member, check, approval_service=service)
    try:
        reset_taint()
        task = service.assign('owner', {'member': member['id'], 'prompt': '整理公开材料'})
        await pool.join(task.id)
        current = TaskLedger(tmp_path).load(task.id)
        assert current.status == 'done', current.error
        assert observed and all(tainted and '外部资料须核对出处' in prompt for tainted, prompt in observed)
        assert current.collaboration['dispatch']['notes']['revision'] == 1
    finally:
        reset_taint(); await pool.shutdown()


def test_owner_can_cancel_interrupted_save_after_member_retirement_without_granting_it(tmp_path, monkeypatch):
    registry = StaffRegistry(str(tmp_path)); member = specialist(registry, 'reader')
    dept = department(registry, 'public', [member]); notes = StaffNotes(str(tmp_path))
    with monkeypatch.context() as patch:
        patch.setattr(notes, '_markdown', lambda *args: (_ for _ in ()).throw(OSError('fixture')))
        with pytest.raises(OSError):
            notes.decide(dept['id'], {}, 0, owner_text='原目标内容', public=True)
    request = notes.inspect(dept['id'])['applying']['request']
    registry.set_member_status(member['id'], 'retired', member['revision'])
    with pytest.raises(ValueError, match='撤掉'):
        notes.resume(dept['id'], request)
    result = notes.cancel_save(dept['id'], request)
    assert result['text'] == '' and result['revision'] == 1
    with pytest.raises(ValueError):
        notes.resume(dept['id'], request)


def test_role_change_cannot_turn_private_external_notes_into_execution_permission(tmp_path):
    registry = StaffRegistry(str(tmp_path)); member = specialist(registry, 'memory', sees=['memory'])
    dept = department(registry, 'private', [member]); notes = StaffNotes(str(tmp_path))
    notes.propose(dept['id'], ['待核对资料'], task_id='task-0123456789', round=1, member=member['id'], sources=['memory'])
    item = notes.pending(dept['id'])[0]
    notes.decide(dept['id'], {item['id']: {'approve': True, 'digest': item['digest']}}, 0)
    role = registry.role(member['role']); role['grants']['operations'].append('execute')
    with pytest.raises(ValueError, match='5.2'):
        registry.register_role(role)
    assert registry.role(member['role'])['grants']['operations'] == ['read']
