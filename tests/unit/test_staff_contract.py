"""Staff design section 10: role grants (1/2); dispatch contracts added in S2."""
from copy import deepcopy
import json

import pytest


def role(**grants):
    return {'id': 'reader', 'title': '阅读员', 'brief': '整理交给它的材料。',
            'grants': {'operations': ['read'], 'tools': [], 'sees': [], 'web': False, 'project': False, **grants},
            'engine': ['vortocode'], 'max_steps': 12, 'timeout_seconds': 600}


def test_builtin_roles_are_persisted_and_obey_the_operation_contract(tmp_path):
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(tmp_path))
    entries = registry.roles()
    assert {item['id'] for item in entries} == {'web-researcher', 'reviewer', 'developer'}
    catalog = registry.tool_catalog()
    for item in entries:
        assert item['builtin'] and item['revision'] == 1
        registry.validate_role(item, builtin=True)
        for name in item['grants']['tools']:
            tool = catalog[name]
            assert {tool.operation_kind, *tool.possible_operations} <= set(item['grants']['operations'])
    path = tmp_path / '.vortoone' / 'staff' / 'roles.json'
    assert json.loads(path.read_text())['roles'] == entries


@pytest.mark.parametrize('grants,rule', [
    ({'operations': ['read', 'execute'], 'sees': ['plugin:mail']}, '5.2.1'),
    ({'web': True, 'sees': ['memory']}, '5.2.2'),
    ({'operations': ['read', 'write'], 'sees': ['plugin:notion']}, '5.2.3'),
])
def test_bad_combinations_cannot_be_registered(tmp_path, grants, rule):
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(tmp_path))
    original = registry.roles()
    with pytest.raises(ValueError, match=rule):
        registry.register_role(role(**grants))
    assert registry.roles() == original


@pytest.mark.parametrize('operations', [['deliver'], ['irreversible'], ['payment'], ['transfer'], ['delete'], ['security']])
def test_owner_only_actions_cannot_be_granted(tmp_path, operations):
    from src.workflow.staff.registry import StaffRegistry
    with pytest.raises(ValueError):
        StaffRegistry(str(tmp_path)).register_role(role(operations=operations))


@pytest.mark.parametrize('name', ['staff_assign', 'staff_propose', 'routine_propose', 'cron_add',
                                  'task', 'send_file', 'open_pr', 'delete_artifact', 'run_command', 'browser_click'])
def test_roles_cannot_acquire_dispatch_or_owner_only_tools(tmp_path, name):
    from src.workflow.staff.registry import StaffRegistry
    with pytest.raises(ValueError):
        StaffRegistry(str(tmp_path)).register_role(role(tools=[name]))


def test_tool_categories_and_private_sources_are_checked(tmp_path):
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(tmp_path))
    with pytest.raises(ValueError, match='类别'):
        registry.register_role(role(tools=['mail_draft'], sees=['plugin:mail']))
    with pytest.raises(ValueError, match='来源'):
        registry.register_role(role(tools=['mail_read']))
    accepted = registry.register_role(role(operations=['read', 'draft'], tools=['mail_read', 'mail_draft'], sees=['plugin:mail']))
    assert accepted['grants']['operations'] == ['read', 'draft']


def test_builtin_definitions_are_immutable_and_custom_updates_are_revisioned(tmp_path):
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(tmp_path))
    builtin = next(item for item in registry.roles() if item['id'] == 'reviewer')
    changed = deepcopy(builtin)
    changed['grants']['web'] = True
    with pytest.raises(ValueError, match='内置'):
        registry.register_role(changed)
    item = registry.register_role(role())
    item['brief'] = '逐条列出证据。'
    assert registry.register_role(item)['revision'] == 2
    assert registry.set_role_enabled('reviewer', False)['enabled'] is False
    assert 'reviewer' not in registry.team_roles()


def test_failed_atomic_save_and_corrupt_file_do_not_restore_default_permissions(tmp_path, monkeypatch):
    from src.workflow.staff.registry import StaffRegistry
    from pathlib import Path
    registry = StaffRegistry(str(tmp_path))
    registry.roles()
    path = tmp_path / '.vortoone' / 'staff' / 'roles.json'
    before = path.read_bytes()
    original = Path.replace
    def fail(source, target):
        if Path(target) == path:
            raise OSError('synthetic failure')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'replace', fail)
        with pytest.raises(OSError):
            registry.register_role(role())
    assert path.read_bytes() == before
    path.write_text('{broken')
    with pytest.raises(ValueError):
        registry.roles()


def test_calendar_reading_is_available_but_combined_writing_waits_for_later_design(tmp_path):
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(tmp_path))
    calendar = role(tools=['calendar_events'], sees=['plugin:calendar'])
    assert registry.register_role(calendar)['grants']['operations'] == ['read']
    for operations in [['read', 'write'], ['read', 'write', 'interact']]:
        with pytest.raises(ValueError, match='5.2'):
            registry.register_role({**role(operations=operations, tools=['calendar_events', 'calendar_add'], sees=['plugin:calendar']), 'id': 'calendar-writer'})


def test_team_compatibility_view_keeps_existing_faces_and_execution_choices(tmp_path):
    from src.workflow.staff.registry import StaffRegistry
    from src.workflow.tasks.team import ROLES, TeamService
    expected = {
        'web-researcher': {'title': '研究员', 'tools': 'web_read', 'web': True, 'max_steps': 12, 'timeout_seconds': 600},
        'reviewer': {'title': '审查员', 'tools': 'none', 'web': False, 'max_steps': 2, 'timeout_seconds': 600},
        'developer': {'title': '开发', 'tools': 'code_write', 'web': False, 'max_steps': 40, 'timeout_seconds': 1800, 'engines': ('opencode',)},
    }
    assert ROLES == StaffRegistry(str(tmp_path)).team_roles() == expected
    service = TeamService(str(tmp_path))
    team = service.create('owner', objective='研究', acceptance=['来源'], members=[{'name': '研究员', 'role': 'web-researcher', 'engine': 'opencode'}])
    assert team.team['members'][0]['engine'] == 'opencode'
    StaffRegistry(str(tmp_path)).set_role_enabled('reviewer', False)
    from src.workflow.tasks.errors import CollaborationConflict
    with pytest.raises(CollaborationConflict):
        service.create('owner', objective='审查', acceptance=['证据'], members=[{'name': '审查', 'role': 'reviewer'}])


def staff_setup(tmp_path, *, tools=(), **grants):
    from src.workflow.staff.registry import StaffRegistry
    from src.workflow.staff.service import StaffService
    registry = StaffRegistry(str(tmp_path))
    registry.register_role(role(tools=list(tools), **grants))
    member = registry.register_member({'id': 'staff-reader', 'name': '阅读专员', 'role': 'reader', 'engine': 'vortocode'})
    class Pool:
        jobs = {}
        def enqueue_existing(self, tid, execute, **callbacks):
            self.jobs[tid] = (execute, callbacks)
            return True
    pool = Pool()
    async def execute(task, work, assigned_role, assigned_member, check):
        check()
        return '逐条交回结论'
    return registry, member, pool, StaffService(str(tmp_path), pool, execute=execute)


@pytest.mark.parametrize('field', ['grants', 'operations', 'tools', 'sees', 'web', 'project', 'permissions', 'role', 'engine', 'model', 'max_steps'])
def test_assignment_cannot_carry_any_authority_fields(tmp_path, field):
    registry, member, pool, service = staff_setup(tmp_path)
    with pytest.raises(ValueError, match='字段'):
        service.assignment({'member': member['id'], 'prompt': '整理材料', field: []})
    assert not pool.jobs


async def test_one_staff_completes_existing_collaboration_task_without_a_coordinator(tmp_path):
    from src.workflow.tasks.collaboration import CollaborationService
    registry, member, pool, service = staff_setup(tmp_path)
    task = service.assign('owner', {'member': member['id'], 'prompt': '整理材料', 'acceptance': ['逐条结论']})
    assert task.kind == 'delegation' and not task.team
    assert task.collaboration['dispatch']['source'] == 'staff'
    assert task.collaboration['dispatch']['delivery'] == 'review'
    await pool.jobs[task.id][0]()
    current = CollaborationService(str(tmp_path), 'owner').get(task.id)
    assert current.status == 'done' and current.result == '逐条交回结论'
    assert current.collaboration['review'] == 'pending'
    assert current.collaboration['tainted'] is True
    from src.workflow.tasks.errors import CollaborationConflict
    with pytest.raises(CollaborationConflict, match='主人'):
        CollaborationService(str(tmp_path), 'owner').review(task.id, 1, 'accept', '已核对')


async def test_role_revision_changed_while_queued_never_executes(tmp_path):
    from src.workflow.tasks.collaboration import CollaborationService
    registry, member, pool, service = staff_setup(tmp_path)
    task = service.assign('owner', {'member': member['id'], 'prompt': '整理材料'})
    current = registry.role('reader')
    current['grants']['web'] = True
    registry.register_role(current)
    with pytest.raises(ValueError, match='变化'):
        await pool.jobs[task.id][0]()
    assert CollaborationService(str(tmp_path), 'owner').get(task.id).status == 'queued'


def test_write_roles_register_but_cannot_dispatch_before_background_approval_cards(tmp_path):
    registry, member, pool, service = staff_setup(tmp_path, operations=['read', 'draft'], tools=['mail_draft'], sees=['plugin:mail'])
    with pytest.raises(ValueError, match='后台审批'):
        service.assign('owner', {'member': member['id'], 'prompt': '起草邮件'})
    assert not pool.jobs


@pytest.fixture(autouse=True)
def staff_clean_taint():
    from src.agents.taint import reset_taint
    reset_taint()
    yield
    reset_taint()


@pytest.mark.parametrize('level,tainted,ask', [('ask', False, True), ('reads', False, False),
    ('full', False, False), ('reads', True, True), ('full', True, True)])
@pytest.mark.parametrize('answer', [True, False])
async def test_assignment_uses_role_category_and_tainted_tasks_show_full_prompt(tmp_path, level, tainted, ask, answer):
    from src.agents.staff import build_staff_tools
    from src.agents.gate import make_confirm_gate
    from src.agents.taint import mark_tainted
    registry, member, pool, service = staff_setup(tmp_path, web=True, tools=['web_search'])
    prompt = '逐字核对任务：' + '完整原文' * 500
    asked = []
    async def human(message, kind):
        assert not pool.jobs
        assert member['id'] in message and prompt in message
        asked.append(kind)
        return answer
    tools = {t.name: t for t in build_staff_tools(str(tmp_path), confirm=make_confirm_gate(human,
        can_ask_human=True, trust_level=level, capability_profile='local'), owner='owner', service=service)}
    if tainted:
        mark_tainted()
    await tools['staff_assign'].handler({'member': member['id'], 'prompt': prompt})
    assert asked == (['read'] if ask else [])
    assert bool(pool.jobs) == (answer if ask else True)


async def test_assignment_has_no_unattended_or_confirmation_race_bypass(tmp_path):
    from src.agents.staff import build_staff_tools
    from src.agents.gate import make_confirm_gate
    registry, member, pool, service = staff_setup(tmp_path)
    args = {'member': member['id'], 'prompt': '核对材料'}
    tools = {t.name: t for t in build_staff_tools(str(tmp_path), confirm=make_confirm_gate(
        auto_approve=True, can_ask_human=False, capability_profile='unattended'), owner='owner', service=service)}
    # auto_approve is an explicit old CLI grant; unattended hosts must never set it.
    tools['staff_assign'].confirm = make_confirm_gate(trust_level='full', capability_profile='unattended')
    await tools['staff_assign'].handler(args)
    assert not pool.jobs
    async def changed(message, kind):
        current = registry.role('reader')
        current['brief'] = '新的工作说明'
        registry.register_role(current)
        return True
    tools['staff_assign'].confirm = make_confirm_gate(changed, can_ask_human=True)
    with pytest.raises(ValueError, match='变化'):
        await tools['staff_assign'].handler(args)
    assert not pool.jobs


def test_specialist_tool_surface_and_fresh_context_are_exactly_the_registered_role(tmp_path):
    from src.agents.staff import build_staff_member
    registry, member, pool, service = staff_setup(tmp_path, tools=['calendar_events'], sees=['plugin:calendar'])
    assigned = registry.role('reader')
    captured = {}
    class Fresh:
        def __init__(self, tools, **kwargs):
            captured.update(kwargs)
            self.tools = {t.name: t for t in tools}
    agent = build_staff_member(str(tmp_path), assigned, member, check=lambda: None, llm=object(), agent_factory=Fresh)
    assert set(agent.tools) == {'calendar_events'}
    assert not set(agent.tools) & {'task', 'staff_assign', 'staff_propose', 'routine_propose', 'cron_add'}
    assert captured['auto_recall'] is False and captured['env_context'] is False
    assert assigned['brief'] in captured['extra_system']
    assert agent.max_steps == agent.build_max_steps == assigned['max_steps']
    assert agent.build_auto_continues == 0


@pytest.mark.parametrize('level', ['ask', 'reads', 'full'])
@pytest.mark.parametrize('answer', [False, True])
async def test_staff_acceptance_is_pending_until_owner_confirmed(tmp_path, level, answer):
    from src.agents.delegation import build_research_tools
    from src.agents.gate import make_confirm_gate
    from src.workflow.tasks.collaboration import CollaborationService
    registry, member, pool, service = staff_setup(tmp_path)
    task = service.assign('owner', {'member': member['id'], 'prompt': '整理材料'})
    await pool.jobs[task.id][0]()
    work = CollaborationService(str(tmp_path), 'owner')
    asked = []
    async def human(message, kind):
        assert work.get(task.id).collaboration['review'] == 'pending'
        asked.append(kind)
        return answer
    tools = {t.name: t for t in build_research_tools(str(tmp_path), collaboration=work,
        confirm=make_confirm_gate(human, can_ask_human=True, trust_level=level))}
    await tools['task_review'].handler({'task_id': task.id, 'round': 1, 'verdict': 'accept', 'note': '已核对证据'})
    assert asked == ['irreversible']
    assert work.get(task.id).collaboration['review'] == ('accepted' if answer else 'pending')


async def test_paused_members_and_disabled_roles_never_start_queued_work(tmp_path):
    registry, member, pool, service = staff_setup(tmp_path)
    task = service.assign('owner', {'member': member['id'], 'prompt': '整理材料'})
    registry.register_member({**member, 'status': 'paused'})
    with pytest.raises(ValueError, match='变化'):
        await pool.jobs[task.id][0]()
    with pytest.raises(ValueError, match='停用'):
        service.assign('owner', {'member': member['id'], 'prompt': '整理材料'})


async def test_notify_delivery_keeps_existing_parent_review_without_owner_acceptance(tmp_path):
    from src.workflow.tasks.collaboration import CollaborationService
    registry, member, pool, service = staff_setup(tmp_path)
    task = service.assign('owner', {'member': member['id'], 'prompt': '汇报资料', 'delivery': 'notify'})
    await pool.jobs[task.id][0]()
    work = CollaborationService(str(tmp_path), 'owner')
    assert work.review(task.id, 1, 'accept', '已核对汇报').collaboration['review'] == 'accepted'


@pytest.mark.parametrize('profile', ['local', 'external', 'owner_device'])
async def test_dispatch_results_match_across_desktop_mobile_and_im_capabilities(tmp_path, profile):
    from src.agents.staff import build_staff_tools
    from src.agents.gate import make_confirm_gate
    from src.agents.capabilities import SessionCapabilities
    from src.agents.tool_runtime import ToolRuntime, ToolExecutionContext
    registry, member, pool, service = staff_setup(tmp_path)
    async def human(message, kind):
        return True
    confirm = make_confirm_gate(human, can_ask_human=True, trust_level='reads', capability_profile=profile)
    tools = {t.name: t for t in build_staff_tools(str(tmp_path), confirm=confirm, owner='owner', service=service)}
    runtime = ToolRuntime(lambda: tools, ToolExecutionContext(confirm=confirm,
        capabilities=SessionCapabilities.for_profile(profile, str(tmp_path))))
    outcome = await runtime.execute('staff_assign', {'member': member['id'], 'prompt': '只读核对'}, 'plan')
    assert outcome.status == 'succeeded' and len(pool.jobs) == 1


async def test_real_pool_executes_staff_and_rework_with_original_contract(tmp_path):
    import asyncio
    from src.workflow.tasks.tasks import TaskRunner
    from src.workflow.staff.service import StaffService
    from src.workflow.tasks.collaboration import CollaborationService
    registry, member, _, _ = staff_setup(tmp_path)
    pool = TaskRunner(str(tmp_path), None)
    completed = asyncio.Event()
    def update(task):
        if task.status == 'done':
            completed.set()
    seen = []
    async def execute(task, work, assigned, person, check):
        check()
        seen.append((task.id, task.collaboration['round'], assigned['grants'], person['id']))
        return '核对后交回'
    service = StaffService(str(tmp_path), pool, execute=execute, on_update=update)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '核对资料'})
        await asyncio.wait_for(completed.wait(), 2)
        work = CollaborationService(str(tmp_path), 'owner')
        work.review(task.id, 1, 'rework', '补充来源')
        completed.clear()
        # Yield to the existing pool's completion bookkeeping, without a timed sleep.
        await asyncio.sleep(0)
        service.followup('owner', task.id, 1, '补充来源')
        await asyncio.wait_for(completed.wait(), 2)
        assert [(x[0], x[1]) for x in seen] == [(task.id, 1), (task.id, 2)]
        assert all(x[2] == registry.role('reader')['grants'] for x in seen)
    finally:
        await pool.shutdown()


async def test_actual_member_assembly_runs_without_inheriting_owner_memory(tmp_path, monkeypatch):
    from src.agents import staff
    from src.workflow.tasks.collaboration import CollaborationService
    registry, member, pool, service = staff_setup(tmp_path)
    task = service.assign('owner', {'member': member['id'], 'prompt': '只整理交给你的材料'})
    captured = []
    class Agent:
        _llm = None
        async def run_turn(self, prompt, mode):
            captured.append((prompt, mode))
            return '独立结论'
    def factory(root, assigned, person, *, check):
        check()
        assert assigned == registry.role('reader') and person == member
        return Agent()
    monkeypatch.setattr(staff, 'build_staff_member', factory)
    service.execute = lambda current, work, assigned, person, check: staff.execute_staff(
        str(tmp_path), current, work, assigned, person, check)
    await pool.jobs[task.id][0]()
    assert captured == [('只整理交给你的材料', 'plan')]
    assert CollaborationService(str(tmp_path), 'owner').get(task.id).result == '独立结论'


async def test_reading_staff_results_keeps_untrusted_history_mark(tmp_path):
    from src.agents.delegation import build_research_tools
    from src.agents.gate import make_confirm_gate
    from src.agents.tool_runtime import ToolRuntime, ToolExecutionContext
    from src.agents.taint import is_tainted
    from src.workflow.tasks.collaboration import CollaborationService
    registry, member, pool, service = staff_setup(tmp_path)
    task = service.assign('owner', {'member': member['id'], 'prompt': '核对资料'})
    await pool.jobs[task.id][0]()
    work = CollaborationService(str(tmp_path), 'owner')
    tools = {t.name: t for t in build_research_tools(str(tmp_path), collaboration=work, confirm=make_confirm_gate())}
    ctx = ToolExecutionContext()
    outcome = await ToolRuntime(lambda: tools, ctx).execute('task_status', {'task_id': task.id}, 'plan')
    assert outcome.status == 'succeeded' and is_tainted() and ctx.state.external_ingested


async def test_unattended_staff_acceptance_and_missing_scope_fail_closed(tmp_path):
    from src.agents.delegation import build_research_tools
    from src.agents.gate import make_confirm_gate
    from src.agents.tool_runtime import ToolRuntime, ToolExecutionContext
    from src.workflow.tasks.collaboration import CollaborationService
    registry, member, pool, service = staff_setup(tmp_path)
    task = service.assign('owner', {'member': member['id'], 'prompt': '核对资料'})
    await pool.jobs[task.id][0]()
    work = CollaborationService(str(tmp_path), 'owner')
    tools = {t.name: t for t in build_research_tools(str(tmp_path), collaboration=work,
        confirm=make_confirm_gate(auto_approve=True, can_ask_human=False))}
    args = {'task_id': task.id, 'round': 1, 'verdict': 'accept', 'note': '证据齐全'}
    await tools['task_review'].handler(args)
    assert work.get(task.id).collaboration['review'] == 'pending'
    ctx = ToolExecutionContext(confirm=make_confirm_gate(auto_approve=True))
    outcome = await ToolRuntime(lambda: tools, ctx).execute('task_review', args, 'build')
    assert outcome.status == 'blocked' and work.get(task.id).collaboration['review'] == 'pending'


@pytest.mark.parametrize('kind,profile', [('web', 'external'), ('web', 'owner_device'), ('im', 'external'), ('cli', 'local')])
async def test_real_general_session_assembles_staff_tools_and_shared_execution(kind, profile, tmp_path, monkeypatch):
    from src.gateway.agent_session import build_session
    from src.scheduling.staff_runner import set_staff_service
    from src.workflow.tasks.tasks import TaskRunner
    registry, member, pool, service = staff_setup(tmp_path)
    runner = TaskRunner(str(tmp_path), None)
    active = set_staff_service(str(tmp_path), runner)
    active.execute = service.execute
    async def human(message, category):
        return True
    try:
        agent = build_session(str(tmp_path), kind=kind, workspace_scope='general', task_owner='owner',
            confirm=human, can_ask_human=True, trust_level='reads', capability_profile=profile)
        assert {'staff_list', 'staff_assign', 'task_status', 'task_review', 'task_followup'} <= set(agent.tools)
        assert 'read_file' not in agent.tools and 'run_command' not in agent.tools
        assert member['id'] in agent.extra_system
        text = await agent._run_tool('staff_assign', {'member': member['id'], 'prompt': '核对材料'}, 'plan', lambda _: None)
        assert json.loads(text)['status'] == 'queued'
    finally:
        await runner.shutdown()
        set_staff_service(str(tmp_path))


async def test_notify_delivery_notification_does_not_request_owner_acceptance(tmp_path):
    from src.scheduling.task_notices import message
    from src.workflow.tasks.collaboration import CollaborationService
    registry, member, pool, service = staff_setup(tmp_path)
    task = service.assign('owner', {'member': member['id'], 'prompt': '汇报材料', 'delivery': 'notify'})
    await pool.jobs[task.id][0]()
    note = message(CollaborationService(str(tmp_path), 'owner').get(task.id))
    assert '汇报' in note and '验收' not in note


async def test_active_staff_member_rechecks_registration_before_each_tool(tmp_path):
    from src.agents.staff import build_staff_member
    registry, member, pool, service = staff_setup(tmp_path, tools=['web_search'], web=True)
    assigned = registry.role('reader')
    agent = build_staff_member(str(tmp_path), assigned, member,
        check=lambda: service.check(member, assigned), llm=object())
    async def offline(args):
        return '离线资料'
    agent.tools['web_search']._implementation = offline
    assert '离线资料' in await agent._run_tool('web_search', {'query': '资料'}, 'plan', lambda _: None)
    registry.set_role_enabled('reader', False)
    with pytest.raises(ValueError, match='变化'):
        await agent._run_tool('web_search', {'query': '资料'}, 'plan', lambda _: None)


async def test_real_pool_persists_failure_when_queued_role_changes(tmp_path):
    import asyncio
    from src.workflow.tasks.tasks import TaskRunner
    from src.workflow.staff.service import StaffService
    from src.workflow.tasks.collaboration import CollaborationService
    registry, member, _, _ = staff_setup(tmp_path)
    pool = TaskRunner(str(tmp_path), None)
    failed = asyncio.Event()
    called = []
    async def execute(*args):
        called.append(True)
        return '不该执行'
    service = StaffService(str(tmp_path), pool, execute=execute,
        on_update=lambda task: failed.set() if task.status == 'failed' else None)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '核对材料'})
        registry.set_role_enabled('reader', False)
        await asyncio.wait_for(failed.wait(), 2)
        assert not called
        assert CollaborationService(str(tmp_path), 'owner').get(task.id).status == 'failed'
    finally:
        await pool.shutdown()


@pytest.mark.parametrize('grants', [{'project': True, 'tools': ['read_file']},
    {'sees': ['files:papers'], 'tools': ['read_file']},
    {'project': True, 'web': True, 'tools': ['read_file', 'web_fetch']}])
def test_unresolved_file_sources_never_fall_back_to_owner_workspace(tmp_path, grants):
    registry, member, pool, service = staff_setup(tmp_path, **grants)
    with pytest.raises(ValueError, match='尚未接入'):
        service.assign('owner', {'member': member['id'], 'prompt': '核对文件'})
    assert not pool.jobs


def test_staff_proposals_do_not_grant_authority_before_owner_decision(tmp_path):
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.registry import StaffRegistry
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'role', role(), '需要独立审查')
    assert proposal['status'] == 'pending'
    with pytest.raises(ValueError):
        StaffRegistry(str(tmp_path)).role('reader')
    applied = approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True)
    assert applied['status'] == 'approved'
    assert StaffRegistry(str(tmp_path)).role('reader')['grants']['tools'] == []


def test_private_or_network_roles_require_registered_desktop_approval_key(tmp_path):
    from src.workflow.staff.approvals import StaffApprovals
    approvals = StaffApprovals(str(tmp_path))
    for data in [role(web=True), role(sees=['plugin:calendar'], tools=['calendar_events'])]:
        proposal = approvals.propose('owner', 'role', data, '需要专员')
        assert proposal['requires_desktop_key']
        with pytest.raises(ValueError, match='批准钥匙'):
            approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True)


def staff_key(approvals):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from cryptography.hazmat.primitives import hashes
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint).hex()
    def sign(message):
        return private.sign(message, ec.ECDSA(hashes.SHA256())).hex()
    approvals.bind_key(public, sign(approvals.enrollment_message(public)))
    return public, sign


def test_desktop_key_binds_exact_proposal_and_replay_is_idempotent(tmp_path):
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.registry import StaffRegistry
    approvals = StaffApprovals(str(tmp_path))
    public, sign = staff_key(approvals)
    proposal = approvals.propose('owner', 'role', role(web=True, tools=['web_search']), '查公开资料')
    signature = sign(approvals.approval_message(proposal))
    with pytest.raises(ValueError, match='变化'):
        approvals.decide(proposal['id'], True, '0' * 64, owner_confirmation=True, signature=signature)
    with pytest.raises(ValueError, match='签名'):
        approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True, signature='00')
    result = approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True, signature=signature)
    assert result['status'] == 'approved' and result['decision']['source'] == 'desktop_key'
    assert approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True) == result
    assert StaffRegistry(str(tmp_path)).role('reader')['revision'] == 1
    other = approvals.propose('owner', 'role', {**role(web=True), 'id': 'other'}, '独立岗位')
    with pytest.raises(ValueError, match='签名'):
        approvals.decide(other['id'], True, other['digest'], owner_confirmation=True, signature=signature)
    with pytest.raises(ValueError, match='已有批准钥匙'):
        staff_key(approvals)


def test_pending_member_approval_checks_current_role_and_retains_owner_history(tmp_path):
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(tmp_path))
    registry.register_role(role())
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'member', {'id': 'staff-new', 'name': '独立专员', 'role': 'reader',
        'engine': 'vortocode'}, '独立整理')
    assert registry.members() == []
    approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True)
    assert registry.member('staff-new')['created_by'] == 'owner'
    assert approvals.list()[0]['decision']['approve'] is True
    pending = approvals.propose('owner', 'member', {'id': 'staff-other', 'name': '另一专员', 'role': 'reader',
        'engine': 'vortocode'}, '独立核对')
    registry.register_role({**registry.role('reader'), 'brief': '已调整职责'})
    with pytest.raises(ValueError, match='变化'):
        approvals.decide(pending['id'], True, pending['digest'], owner_confirmation=True)
    assert len(registry.members()) == 1


def test_grant_failure_has_durable_approval_and_can_resume_without_duplicate_registration(tmp_path, monkeypatch):
    from src.workflow.staff.approvals import StaffApprovals
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'role', role(), '只读审查')
    original = approvals.registry.register_role
    def fail(*args):
        raise OSError('synthetic storage failure')
    monkeypatch.setattr(approvals.registry, 'register_role', fail)
    with pytest.raises(OSError):
        approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True)
    saved = approvals.get(proposal['id'])
    assert saved['status'] == 'applying' and saved['decision']['approve'] is True
    assert saved['decision']['at']
    monkeypatch.setattr(approvals.registry, 'register_role', original)
    assert approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True)['status'] == 'approved'
    assert approvals.registry.role('reader')['revision'] == 1


@pytest.mark.parametrize('tainted,answer', [(True, True), (False, False), (False, True)])
async def test_propose_requires_owner_even_full_and_tainted_turns_cannot_propose(tmp_path, tainted, answer):
    from src.agents.staff_proposals import build_staff_proposal_tool
    from src.agents.taint import mark_tainted
    from src.agents.gate import make_confirm_gate
    from src.workflow.staff.registry import StaffRegistry
    asked = []
    async def human(message, kind):
        asked.append(kind)
        assert not StaffRegistry(str(tmp_path)).members()
        return answer
    tool = build_staff_proposal_tool(str(tmp_path), owner='owner', confirm=make_confirm_gate(human,
        auto_approve=True, can_ask_human=True))
    if tainted:
        mark_tainted()
    await tool.handler({'kind': 'role', 'data': role(), 'reason': '只读整理'})
    assert asked == ([] if tainted else ['execute'])
    roles = StaffRegistry(str(tmp_path)).roles()
    assert ('reader' in {x['id'] for x in roles}) == (not tainted and answer)


async def test_high_privilege_proposal_remains_pending_after_normal_confirmation(tmp_path):
    from src.agents.staff_proposals import build_staff_proposal_tool
    from src.agents.gate import make_confirm_gate
    from src.workflow.staff.approvals import StaffApprovals
    async def human(message, kind): return True
    tool = build_staff_proposal_tool(str(tmp_path), owner='owner', confirm=make_confirm_gate(human, can_ask_human=True))
    await tool.handler({'kind': 'role', 'data': role(web=True, tools=['web_fetch']), 'reason': '公开资料'})
    approvals = StaffApprovals(str(tmp_path))
    assert approvals.list()[0]['status'] == 'pending'
    with pytest.raises(ValueError):
        approvals.registry.role('reader')


def test_staff_admin_routes_reject_device_credentials_and_enforce_key_and_revision(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.server import app
    from src.workflow.staff.approvals import StaffApprovals
    registry, member, pool, service = staff_setup(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VORTOONE_API_TOKEN', 'staff-admin-fixture')
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'role', {**role(web=True), 'id': 'network'}, '公开资料')
    client = TestClient(app)
    for method, path, body in [('GET', '/api/staff', None),
        ('POST', f"/api/staff/proposals/{proposal['id']}/decision", {'approve': True, 'digest': proposal['digest']}),
        ('PUT', f"/api/staff/members/{member['id']}/status", {'status': 'paused', 'revision': 1}),
        ('POST', '/api/staff/approval-key', {'public_key': '00', 'signature': '00'})]:
        assert client.request(method, path, json=body, headers={'Authorization': 'Bearer device-fixture'}).status_code == 401
    headers = {'Authorization': 'Bearer staff-admin-fixture'}
    assert client.get('/api/staff', headers=headers).status_code == 200
    assert client.post(f"/api/staff/proposals/{proposal['id']}/decision", headers=headers,
        json={'approve': True, 'digest': proposal['digest']}).status_code == 409
    endpoint = f"/api/staff/members/{member['id']}/status"
    assert client.put(endpoint, headers=headers, json={'status': 'paused', 'revision': 1}).json()['status'] == 'paused'
    assert client.put(endpoint, headers=headers, json={'status': 'active', 'revision': 1}).status_code == 409
    assert client.put(endpoint, headers=headers, json={'status': 'retired', 'revision': 2}).json()['status'] == 'retired'
    assert client.put(endpoint, headers=headers, json={'status': 'active', 'revision': 3}).status_code == 409
    assert client.put(endpoint, headers=headers, json={'status': 'active', 'revision': 3, 'grants': {}}).status_code == 422


@pytest.mark.parametrize('profile', ['local', 'external', 'owner_device'])
async def test_low_risk_registration_uses_the_real_shared_confirmation_on_all_ends(tmp_path, profile):
    from src.gateway.agent_session import build_session
    from src.workflow.staff.registry import StaffRegistry
    asked = []
    async def human(message, kind):
        asked.append(kind)
        return True
    agent = build_session(str(tmp_path), kind='cli' if profile == 'local' else 'web',
        workspace_scope='general', capability_profile=profile, task_owner='owner', confirm=human,
        can_ask_human=True, trust_level='full')
    await agent._run_tool('staff_propose', {'kind': 'role', 'data': role(), 'reason': '独立只读核对'}, 'plan', lambda _: None)
    assert asked == ['execute'] and StaffRegistry(str(tmp_path)).role('reader')['revision'] == 1


def test_staff_api_is_available_in_general_without_a_project():
    from src.gateway.workspace_scope import http_workspace_requirement
    for path in ['/api/staff', '/api/staff/approval-key', '/api/staff/members/reader/status',
                 '/api/staff/proposals/fixture/decision']:
        assert http_workspace_requirement(path, 'general') is None


def test_enrollment_proof_cannot_be_replayed_on_another_instance(tmp_path):
    from src.workflow.staff.approvals import StaffApprovals
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    first, other = StaffApprovals(str(tmp_path / 'first')), StaffApprovals(str(tmp_path / 'other'))
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint).hex()
    proof = key.sign(first.enrollment_message(public), ec.ECDSA(hashes.SHA256())).hex()
    first.bind_key(public, proof)
    with pytest.raises(ValueError, match='签名'):
        other.bind_key(public, proof)
    assert other.key_info()['key'] is None


def test_failed_receipt_never_grants_and_failed_final_save_replays_exact_target(tmp_path, monkeypatch):
    from src.workflow.staff.approvals import StaffApprovals
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'role', role(), '只读核对')
    original = approvals.registry._write
    def fail_receipt(name, value):
        if name == 'proposals.json':
            raise OSError('synthetic receipt failure')
        return original(name, value)
    monkeypatch.setattr(approvals.registry, '_write', fail_receipt)
    with pytest.raises(OSError):
        approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True)
    with pytest.raises(ValueError):
        approvals.registry.role('reader')
    def fail_final(name, value):
        if name == 'proposals.json' and any(x['status'] == 'approved' for x in value['proposals']):
            raise OSError('synthetic final save failure')
        return original(name, value)
    monkeypatch.setattr(approvals.registry, '_write', fail_final)
    with pytest.raises(OSError):
        approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True)
    assert approvals.registry.role('reader')['revision'] == 1
    assert approvals.get(proposal['id'])['status'] == 'applying'
    monkeypatch.setattr(approvals.registry, '_write', original)
    assert approvals.decide(proposal['id'], True, proposal['digest'], owner_confirmation=True)['status'] == 'approved'
    assert approvals.registry.role('reader')['revision'] == 1


async def test_headless_auto_approve_cannot_register_staff(tmp_path):
    from src.agents.staff_proposals import build_staff_proposal_tool
    from src.agents.gate import make_confirm_gate
    from src.workflow.staff.approvals import StaffApprovals
    tool = build_staff_proposal_tool(str(tmp_path), owner='owner',
        confirm=make_confirm_gate(auto_approve=True, can_ask_human=False))
    await tool.handler({'kind': 'role', 'data': role(), 'reason': '只读核对'})
    assert StaffApprovals(str(tmp_path)).list() == []
    with pytest.raises(ValueError):
        StaffApprovals(str(tmp_path)).registry.role('reader')


def test_corrupt_key_registration_fails_closed_without_replacing_identity(tmp_path):
    from src.workflow.staff.approvals import StaffApprovals
    approvals = StaffApprovals(str(tmp_path))
    approvals.key_info()
    path = approvals.registry.directory / 'approval-key.json'
    broken = {'version': 1, 'key': None}
    path.write_text(json.dumps(broken))
    with pytest.raises(ValueError, match='损坏'):
        approvals.key_info()
    assert json.loads(path.read_text()) == broken
