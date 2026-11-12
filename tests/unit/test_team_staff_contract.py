"""S6: registered team members and complete, scoped delivery files."""
import hashlib
import asyncio

import pytest

from tests.unit.staff_owner_helpers import owner_decide

from src.workflow.tasks.collaboration import CollaborationService
from src.workflow.tasks.errors import CollaborationConflict
from src.workflow.tasks.team import TeamService
from src.workflow.tasks.tasks import TaskLedger
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.approvals import StaffApprovals


def specialist(tmp_path, name='reader', *, web=False, sees=()):
    registry = StaffRegistry(str(tmp_path))
    role = registry.register_role({'id': name + '-role', 'title': name, 'brief': '整理已交给本任务的资料',
        'grants': {'operations': ['read'], 'tools': ['read_delivery'], 'sees': list(sees), 'web': web, 'project': False},
        'engine': ['vortocode'], 'max_steps': 8, 'timeout_seconds': 600})
    member = registry.register_member({'id': 'staff-' + name, 'name': name, 'role': role['id'], 'engine': 'vortocode'})
    return registry, member


def team(tmp_path):
    service = TeamService(str(tmp_path))
    root = service.create('owner', objective='比较公开资料', acceptance=['交回完整报告'], members=[
        {'name': '研究', 'role': 'web-researcher'}, {'name': '审查', 'role': 'reviewer'}])
    return service, root


def test_s6_team_result_preserves_the_complete_utf8_delivery_in_a_file(tmp_path):
    service, root = team(tmp_path)
    task = service.assign(root.id, '研究', '交回长报告', '完整正文')
    work = service.collaboration(root)
    work.start(task.id, 1)
    text = '公开材料与证据🙂\n' * 1500 + '最后一条事实必须保留'
    work.finish(task.id, 1, text, tainted=True)
    saved = TaskLedger(str(tmp_path)).load(task.id)
    output = saved.collaboration.get('output')
    assert output is not None, '完整交付必须落文件，不能只剩截断稿'
    path = tmp_path / '.vortoone' / output['path']
    assert path.read_text() == text
    assert output['sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert output['characters'] == len(text)
    assert output['tainted'] is True
    assert saved.collaboration['messages'][-1]['output'] == output


def complete(tmp_path, text='原交付', **kwargs):
    service, root = team(tmp_path)
    task = service.assign(root.id, '研究', '交回报告', '完整正文')
    work = service.collaboration(root)
    work.start(task.id, 1)
    return service, root, work, work.finish(task.id, 1, text, **kwargs)


def test_delivery_pages_reconstruct_the_full_unicode_text_after_reload(tmp_path):
    text = '证据🙂\n' * 5000 + '最后一条'
    _, root, _, task = complete(tmp_path, text, tainted=True)
    work = TeamService(str(tmp_path)).collaboration(root)
    parts, offset = [], 0
    while True:
        page = work.read_output(task.id, 1, offset=offset, limit=7000)
        assert len(page['text']) <= 7000 and page['external_content']
        parts.append(page['text'])
        if page['next_offset'] is None:
            assert page['complete']
            break
        assert not page['complete'] and page['next_offset'] > offset
        offset = page['next_offset']
    assert ''.join(parts) == text
    path = tmp_path / '.vortoone' / task.collaboration['output']['path']
    assert path.stat().st_mode & 0o777 == 0o600


def test_rework_keeps_each_original_delivery_without_reusing_a_failed_round(tmp_path):
    service, root, work, task = complete(tmp_path, '第一轮尾部' * 2000)
    service.review(root.id, task.id, 'rework', '补充证据')
    work.start(task.id, 2)
    work.finish(task.id, 2, '第二轮正文')
    service.review(root.id, task.id, 'rework', '核对来源')
    work.start(task.id, 3)
    work.finish(task.id, 3, '', error='来源暂不可读取')
    assert work.read_output(task.id, 1, offset=8000)['text'].endswith('第一轮尾部')
    assert work.read_output(task.id, 2)['text'] == '第二轮正文'
    with pytest.raises(CollaborationConflict, match='没有完整交付'):
        work.read_output(task.id, 3)


@pytest.mark.parametrize('mutation', ['path', 'sha256', 'text', 'missing', 'symlink'])
def test_changed_or_missing_output_never_reads_unverified_or_outside_content(tmp_path, mutation):
    _, _, work, task = complete(tmp_path)
    reference = task.collaboration['output']
    path = tmp_path / '.vortoone' / reference['path']
    if mutation == 'path':
        reference['path'] = '../outside.txt'
    elif mutation == 'sha256':
        reference['sha256'] = 'f' * 64
    elif mutation == 'text':
        path.write_text('被更换的正文')
    else:
        path.unlink()
        if mutation == 'symlink':
            outside = tmp_path / 'outside.txt'
            outside.write_text('不应读取')
            path.symlink_to(outside)
    assert TaskLedger(str(tmp_path)).save(task)
    with pytest.raises(CollaborationConflict):
        work.read_output(task.id, 1)


@pytest.mark.parametrize('offset,limit', [(-1, 100), (True, 100), (0, 0), (0, 8001), (0, True), (99999, 100)])
def test_delivery_read_rejects_invalid_ranges(tmp_path, offset, limit):
    _, _, work, task = complete(tmp_path)
    with pytest.raises(CollaborationConflict):
        work.read_output(task.id, 1, offset=offset, limit=limit)


def test_another_owner_cannot_read_a_delivery_even_with_its_task_id(tmp_path):
    _, _, _, task = complete(tmp_path)
    with pytest.raises(CollaborationConflict, match='不属于'):
        CollaborationService(str(tmp_path), 'other').read_output(task.id, 1)


def test_output_write_failure_does_not_claim_the_task_completed(tmp_path, monkeypatch):
    service, root = team(tmp_path)
    task = service.assign(root.id, '研究', '报告', '正文')
    work = service.collaboration(root)
    work.start(task.id, 1)
    import src.workflow.tasks.task_outputs as outputs
    with monkeypatch.context() as patch:
        patch.setattr(outputs.tempfile, 'NamedTemporaryFile', lambda **kwargs: (_ for _ in ()).throw(OSError('fixture storage error')))
        with pytest.raises(OSError):
            work.finish(task.id, 1, '完整正文')
    saved = work.get(task.id)
    assert saved.status == 'running' and 'output' not in saved.collaboration
    task = work.finish(task.id, 1, '完整正文')
    assert task.status == 'done' and work.read_output(task.id, 1)['text'] == '完整正文'


def test_oversized_delivery_fails_explicitly_without_a_truncated_success(tmp_path):
    service, root = team(tmp_path)
    task = service.assign(root.id, '研究', '报告', '正文')
    work = service.collaboration(root)
    work.start(task.id, 1)
    from src.workflow.tasks.task_outputs import MAX_OUTPUT_BYTES
    with pytest.raises(CollaborationConflict, match='未截断保存'):
        work.finish(task.id, 1, 'x' * (MAX_OUTPUT_BYTES + 1))
    assert work.get(task.id).status == 'running'


def test_a_failed_task_receipt_does_not_claim_a_delivery_and_retry_reuses_one_file(tmp_path, monkeypatch):
    service, root = team(tmp_path)
    child = service.assign(root.id, '研究', '报告', '正文')
    work = service.collaboration(root)
    work.start(child.id, 1)
    save = TaskLedger.save
    with monkeypatch.context() as patch:
        patch.setattr(TaskLedger, 'save', lambda ledger, task: False if task.id == child.id and task.status == 'done' else save(ledger, task))
        with pytest.raises(OSError): work.finish(child.id, 1, '完整交付')
    assert work.get(child.id).status == 'running' and 'output' not in work.get(child.id).collaboration
    work.finish(child.id, 1, '完整交付')
    assert len(list((tmp_path / '.vortoone' / 'tasks' / 'outputs' / child.id).glob('*.txt'))) == 1


def test_external_developer_rework_receives_its_complete_own_attachment_without_changing_the_request(tmp_path):
    from src.agents.team_members import external_developer_prompt
    service = TeamService(str(tmp_path))
    root = service.create('owner', objective='改代码并审查', acceptance=['交回分支'], mode='private', reason='independent_review',
        project={'name': 'fixture', 'repo_root': str(tmp_path / 'repo')},
        members=[{'name': '开发', 'role': 'developer', 'engine': 'opencode'}, {'name': '审查', 'role': 'reviewer'}])
    child = service.assign(root.id, '开发', '修复小问题', '完整说明')
    work = service.collaboration(root); work.start(child.id, 1)
    report = '自己的交付说明🙂' * 2000 + '最后的验证缺口'
    work.finish(child.id, 1, report)
    service.review(root.id, child.id, 'rework', '补齐验证')
    current = work.get(child.id)
    assert report in external_developer_prompt(current, work) and report not in current.prompt


def test_an_output_directory_link_is_rejected_before_writing_outside_the_task_area(tmp_path):
    service, root = team(tmp_path)
    child = service.assign(root.id, '研究', '报告', '正文')
    work = service.collaboration(root); work.start(child.id, 1)
    outside = tmp_path / 'outside'; outside.mkdir()
    (tmp_path / '.vortoone' / 'tasks' / 'outputs').symlink_to(outside, target_is_directory=True)
    with pytest.raises(CollaborationConflict, match='符号链接'): work.finish(child.id, 1, '正文')
    assert not list(outside.iterdir()) and work.get(child.id).status == 'running'


def test_team_can_reference_a_registered_specialist_without_overriding_its_authority(tmp_path):
    registry, member = specialist(tmp_path)
    root = TeamService(str(tmp_path)).create('owner', objective='独立复核公开资料', acceptance=['交回证据'],
        members=[{'member': member['id']}, {'name': '审查', 'role': 'reviewer'}], reason='independent_review')
    entry = root.team['members'][0]
    assert entry['member'] == member['id'] and entry['role_snapshot'] == registry.role(member['role'])
    child = TeamService(str(tmp_path)).assign(root.id, member['name'], '核对公开事实', '来源')
    assert child.collaboration['dispatch']['member'] == member['id']
    assert child.collaboration['dispatch']['member_revision'] == member['revision']
    with pytest.raises((ValueError, CollaborationConflict), match='权限|字段|覆盖'):
        TeamService(str(tmp_path)).create('owner', objective='x', acceptance=['y'],
            members=[{'member': member['id'], 'grants': {'web': True}}], reason='parallel')


def test_old_builtin_delivery_permissions_require_an_explicit_versioned_approval(tmp_path):
    from src.workflow.staff.roles import builtin_roles
    registry = StaffRegistry(str(tmp_path))
    registry._write('roles.json', {'version': 1, 'roles': builtin_roles(legacy=True)})
    old = registry.role('reviewer')
    assert 'read_delivery' not in old['grants']['tools']
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'role', {'id': 'reviewer', 'revision': old['revision'], 'upgrade': True}, '允许读取本任务交付物')
    assert registry.role('reviewer') == old
    owner_decide(approvals, proposal['id'], True, proposal['digest'])
    updated = registry.role('reviewer')
    assert updated['revision'] == old['revision'] + 1 and 'read_delivery' in updated['grants']['tools']


def registered_team(tmp_path):
    registry, member = specialist(tmp_path)
    service = TeamService(str(tmp_path))
    root = service.create('owner', objective='独立复核', acceptance=['完整证据'],
        members=[{'member': member['id']}, {'name': '审查', 'role': 'reviewer'}], reason='independent_review')
    return registry, member, service, root


def test_private_sources_and_web_specialists_cannot_be_in_the_same_team(tmp_path):
    _, private = specialist(tmp_path, 'mail', sees=['plugin:mail'])
    _, public = specialist(tmp_path, 'public', web=True)
    for mode in ['open', 'private']:
        with pytest.raises(CollaborationConflict):
            TeamService(str(tmp_path)).create('owner', objective='x', acceptance=['y'], mode=mode,
                members=[{'member': private['id']}, {'member': public['id']}], reason='separate_permissions')


async def test_file_handoff_keeps_the_tail_and_reader_cannot_open_another_delivery(tmp_path, monkeypatch):
    from src.agents.team_coordinator import build_tools
    _, member, service, root = registered_team(tmp_path)
    original = service.assign(root.id, member['name'], '核对事实', '完整报告')
    work = service.collaboration(root)
    work.start(original.id, 1)
    text = '正文🙂' * 6000 + '必须读到的尾部'
    work.finish(original.id, 1, text, tainted=True)
    tools = {tool.name: tool for tool in build_tools(str(tmp_path), root.id, kick=lambda _: None)}
    await tools['team_cross_review'].handler({'task_id': original.id, 'reviewer': '审查', 'focus': '尾部事实'})
    reviewer = service.children(service.get(root.id))[-1]
    assert len(reviewer.prompt) < 1000 and '必须读到的尾部' not in reviewer.prompt
    assert reviewer.collaboration['dispatch']['materials'][0]['sha256'] == work.get(original.id).collaboration['output']['sha256']
    parts, offset = [], 0
    while True:
        page = service.read_material(reviewer.id, original.id, 1, offset=offset)
        parts.append(page['text'])
        if page['next_offset'] is None: break
        offset = page['next_offset']
    assert ''.join(parts) == text
    another = service.assign(root.id, member['name'], '其它任务', '结果')
    with pytest.raises(CollaborationConflict, match='未明确'):
        service.read_material(another.id, original.id, 1)
    from src.agents.staff import build_staff_member
    from src.agents.taint import reset_taint, is_tainted
    reset_taint()
    try:
        monkeypatch.setenv('VORTOONE_MAX_TOOL_RESULT', '1800')
        person, role = service.execution_identity(reviewer.id)
        agent = build_staff_member(str(tmp_path), role, person, check=lambda: service.execution_identity(reviewer.id), llm=object(),
            read_delivery=lambda source, number, **kwargs: service.read_material(reviewer.id, source, number, **kwargs))
        assert set(agent.tools) == {'read_delivery'}
        import json
        parts, offset = [], 0
        while True:
            reply = await agent._run_tool('read_delivery', {'task_id': original.id, 'round': 1, 'offset': offset}, 'plan', lambda _: None)
            assert len(reply) <= 1800
            page = json.loads(reply)
            parts.append(page['text'])
            if page['next_offset'] is None: break
            offset = page['next_offset']
        assert ''.join(parts) == text and is_tainted()
    finally:
        reset_taint()


@pytest.mark.parametrize('change', ['lead', 'department'])
def test_lead_assembly_has_only_team_coordination_tools_and_loses_scope_after_revision_change(tmp_path, change):
    from src.agents.team_coordinator import build_coordinator
    registry, one = specialist(tmp_path, 'one')
    _, two = specialist(tmp_path, 'two')
    _, lead = specialist(tmp_path, 'lead', web=True)
    department = registry.register_department({'id': 'dept-public', 'name': '公开资料', 'duty': '核对',
        'members': [one['id'], two['id'], lead['id']], 'lead': lead['id']})
    service = TeamService(str(tmp_path))
    root = service.create('owner', objective='x', acceptance=['y'], department=department['id'], reason='parallel',
        members=[{'member': one['id']}, {'member': two['id']}])
    captured = {}
    class Agent:
        def __init__(self, tools, **kwargs): captured.update(tools=tools, **kwargs)
    build_coordinator(str(tmp_path), root, kick=lambda _: None, llm=object(), agent_factory=Agent)
    assert {tool.name for tool in captured['tools']} == {'team_assign', 'team_review', 'team_cross_review',
        'team_ask_owner', 'team_submit', 'team_status', 'team_result'}
    assert root.team['coordinator']['member'] == lead['id']
    if change == 'lead':
        registry.set_member_status(lead['id'], 'paused', lead['revision'])
    else:
        registry.register_department({**department, 'status': 'paused'})
    with pytest.raises(CollaborationConflict, match='协调人|部门'):
        captured['execution_check']()


async def test_failed_coordinator_is_durably_parked_without_replaying_its_actions(tmp_path):
    from src.scheduling.staff_runner import set_staff_service
    from src.scheduling.team_runner import TeamRunner
    from src.workflow.tasks.tasks import TaskRunner
    _, _, service, root = registered_team(tmp_path)
    pool = TaskRunner(str(tmp_path), None)
    set_staff_service(str(tmp_path), pool)
    called = []
    async def coordinator(*args, **kwargs):
        called.append(True)
        raise RuntimeError('offline fixture')
    runner = TeamRunner(str(tmp_path), pool, coordinator=coordinator)
    try:
        runner.wake(root.id, '开始')
        await runner._driving[root.id]
        assert service.get(root.id).status == 'waiting'
        assert '中断' in service.get(root.id).team['waiting_reason']
        runner.wake(root.id, '重复事件')
        await runner._driving[root.id]
        assert called == [True]
    finally:
        await runner.close(); await pool.shutdown(); set_staff_service(str(tmp_path), None)


async def test_restarted_team_quota_wait_requires_an_explicit_owner_reply(tmp_path):
    from src.scheduling.staff_runner import set_staff_service
    from src.scheduling.team_runner import TeamRunner
    from src.workflow.staff.team_budget import TeamBudget
    from src.workflow.tasks.tasks import TaskRunner
    _, _, service, root = registered_team(tmp_path)
    pool = TaskRunner(str(tmp_path), None)
    staff = set_staff_service(str(tmp_path), pool)
    called = []
    async def coordinator(*args, **kwargs): called.append(True)
    runner = TeamRunner(str(tmp_path), pool, coordinator=coordinator)
    try:
        TeamBudget(str(tmp_path), staff).stage(root, {'scope': 'global', 'reason': '到额度'})
        await runner.recover()
        current = service.get(root.id)
        assert current.status == 'waiting' and current.team['quota']['status'] == 'interrupted'
        assert not pool.is_active(root.id) and called == []
        assert current.collaboration == {}
    finally:
        await runner.close(); await pool.shutdown(); set_staff_service(str(tmp_path), None)


async def test_registered_team_reuses_the_staff_execution_pool_and_rejects_changed_queued_roles(tmp_path):
    from src.scheduling.staff_runner import set_staff_service
    from src.scheduling.team_runner import TeamRunner
    from src.workflow.tasks.tasks import TaskRunner
    registry, member, service, root = registered_team(tmp_path)
    pool = TaskRunner(str(tmp_path), None)
    staff = set_staff_service(str(tmp_path), pool)
    seen = []
    async def execute(task, work, role, person, check):
        check(); seen.append(person['id']); return '完整交付'
    async def coordinator(*args, **kwargs):
        service.ask_owner(root.id, '已交回，请核对')
    staff.execute = execute
    runner = TeamRunner(str(tmp_path), pool, coordinator=coordinator)
    try:
        child = service.assign(root.id, member['name'], '核对', '交回')
        runner.kick(root.id)
        await asyncio.wait_for(pool.join(child.id), 2)
        assert service.collaboration(root).get(child.id).status == 'done' and seen == [member['id']]
        other_root = service.create('owner', objective='下一项', acceptance=['结果'], reason='parallel',
            members=[{'member': member['id']}, {'name': '审查', 'role': 'reviewer'}])
        other = service.assign(other_root.id, member['name'], '核对', '交回')
        registry.set_member_status(member['id'], 'paused', member['revision'])
        runner.kick(other_root.id)
        assert service.collaboration(other_root).get(other.id).status == 'failed' and seen == [member['id']]
    finally:
        await runner.close(); await pool.shutdown(); set_staff_service(str(tmp_path), None)


async def test_coordinator_quota_counts_real_calls_and_releases_the_shared_pool_while_waiting(tmp_path):
    from src.llm.client import add_usage
    from src.scheduling.staff_runner import set_staff_service
    from src.scheduling.team_runner import TeamRunner
    from src.workflow.staff.team_budget import TeamBudget
    from src.workflow.staff.budgets import StaffBudgetedLLM
    from src.workflow.tasks.tasks import TaskRunner
    _, _, service, root = registered_team(tmp_path)
    pool = TaskRunner(str(tmp_path), None, max_concurrent=1)
    staff = set_staff_service(str(tmp_path), pool)
    staff.configure_budgets({'daily_tokens': 6, 'task_tokens': 0}, staff.budgets.settings()['revision'])
    waiting, called, done = asyncio.Event(), [], asyncio.Event()
    pool.subscribe(lambda task: waiting.set() if task.id == root.id and task.team.get('quota', {}).get('status') == 'waiting' else None)
    class Fake:
        async def chat(self, *args): called.append(1); add_usage(4, 2, model='offline-fixture'); return '结果'
    async def coordinator(*args, **kwargs):
        adapter = TeamBudget(str(tmp_path), staff)
        task, person = adapter.context(root.id)
        proxy = StaffBudgetedLLM(Fake(), adapter, task, {}, person)
        await proxy.chat([]); await proxy.chat([])
        service.ask_owner(root.id, '已核对，等主人决定'); done.set()
    runner = TeamRunner(str(tmp_path), pool, coordinator=coordinator)
    try:
        runner.wake(root.id, '开始')
        await asyncio.wait_for(waiting.wait(), 2)
        assert called == [1]
        independent = pool.ledger.create('fixture', '独立的既有入口')
        progressed = asyncio.Event()
        async def execute(): progressed.set()
        pool.enqueue_existing(independent.id, execute, on_cancel=lambda: None, on_failure=lambda _: None)
        await asyncio.wait_for(progressed.wait(), 2)
        staff.configure_budgets({'daily_tokens': 12, 'task_tokens': 0}, staff.budgets.settings()['revision'])
        await asyncio.wait_for(done.wait(), 2)
        assert called == [1, 1] and service.get(root.id).team['usage']['tokens'] == 12
        assert service.get(root.id).collaboration == {}, '协调用量不能把团队根伪装成缺少消息字段的协作子任务'
    finally:
        await runner.close(); await pool.shutdown(); set_staff_service(str(tmp_path), None)


def test_output_api_checks_owner_auth_team_membership_digest_and_round(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.server import app
    service, root, _, child = complete(tmp_path, '长报告' * 4000 + '尾部证据', tainted=True)
    monkeypatch.chdir(tmp_path); monkeypatch.setenv('VORTOONE_API_TOKEN', 'offline-owner-fixture')
    monkeypatch.setenv('VORTOONE_WORKSPACE_SCOPE', 'general')
    client = TestClient(app)
    path = f'/api/teams/{root.id}/outputs/{child.id}/1'
    headers = {'Authorization': 'Bearer offline-owner-fixture'}
    assert client.get(path).status_code == 401
    page = client.get(path, headers=headers)
    assert page.status_code == 200 and page.json()['next_offset'] == 8000
    assert client.get(path + '?sha256=' + 'f' * 64, headers=headers).status_code == 409
    tail = client.get(path + '?offset=8000', headers=headers).json()
    assert tail['complete'] and tail['text'].endswith('尾部证据')
    other = service.create('owner', objective='其它小组', acceptance=['结果'], members=[{'name': '审查', 'role': 'reviewer'}])
    assert client.get(f'/api/teams/{other.id}/outputs/{child.id}/1', headers=headers).status_code == 404
    assert client.get(path[:-1] + '2', headers=headers).status_code == 409


@pytest.mark.parametrize('approve', [True, False])
async def test_team_member_actions_wait_for_the_owner_and_are_not_hidden_as_internal_results(tmp_path, monkeypatch, approve):
    from tests.unit.test_staff_actions import live_staff, next_card, decide
    from src.workflow.workbench.personal_goals import PersonalGoals
    from src.scheduling.team_runner import TeamRunner
    from src.scheduling import staff_runner, task_notices
    from src.workflow.tasks.decisions import build_decision_queue
    staff, member, waiting, _, _, results, starts = live_staff(tmp_path, monkeypatch, real_goal=True)
    monkeypatch.setattr(staff_runner, 'get_staff_service', lambda _: staff)
    service = TeamService(str(tmp_path))
    root = service.create('owner', objective='整理个人目标', acceptance=['先给主人核对'], mode='private',
        members=[{'member': member['id']}, {'name': '审查', 'role': 'reviewer'}], reason='independent_review', limits={'parallel': 1})
    async def coordinator(*args, **kwargs): service.ask_owner(root.id, '已交回，请核对')
    runner = TeamRunner(str(tmp_path), staff.runner, coordinator=coordinator)
    try:
        task = service.assign(root.id, member['name'], '整理学习计划', '目标草稿')
        runner.kick(root.id)
        _, card = await next_card(waiting)
        blocked = service.collaboration(root).get(task.id)
        assert blocked.status == 'blocked' and not PersonalGoals(str(tmp_path)).list()
        assert task_notices.message(blocked) is not None
        queue = build_decision_queue(tasks=[blocked])
        assert len(queue) == 1 and queue[0]['target_id'] == task.id
        other = service.assign(root.id, '审查', '独立只读任务', '结果')
        runner.kick(root.id)
        await asyncio.wait_for(staff.runner.join(other.id), 2)
        assert service.collaboration(root).get(other.id).status == 'done'
        decide(staff, task, card, approve, reason='' if approve else '先交回，不保存')
        await asyncio.wait_for(staff.runner.join(task.id), 2)
        assert len(PersonalGoals(str(tmp_path)).list()) == int(approve)
        assert starts.count((task.id, 1)) == 1
    finally:
        await runner.close(); await staff.runner.shutdown()
