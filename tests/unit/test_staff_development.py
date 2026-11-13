"""S9: approved project bindings and single-specialist isolated delivery."""
from copy import deepcopy
from functools import partial
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from src.agents.staff import execute_staff
from src.workflow.staff.approvals import StaffApprovals
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.service import StaffService
from tests.unit.staff_owner_helpers import owner_decide, proposal_body


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def development(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir()
    (project / 'app.txt').write_text('original\n')
    (project / '.gitignore').write_text('.env\n.vortoone/\n')
    git(project, 'init', '-q', '-b', 'main')
    git(project, 'add', '.')
    git(project, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
    (project / '.env').write_text('fixture private data')
    config = tmp_path / 'projects.json'
    config.write_text(json.dumps({'projects': [{'name': 'Example', 'repo_root': str(project), 'port': 8099}]}))
    monkeypatch.setenv('VORTOONE_RESIDENT_PROJECTS', str(config))
    for key, value in {'GIT_AUTHOR_NAME': 'fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                       'GIT_COMMITTER_NAME': 'fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}.items():
        monkeypatch.setenv(key, value)
    root = tmp_path / 'assistant'
    registry = StaffRegistry(str(root))
    data = deepcopy(registry.role('developer'))
    for field in ('builtin', 'revision', 'enabled'):
        data.pop(field)
    data.update(id='example-developer', project='Example')
    return root, project, config, data


class Pool:
    def __init__(self):
        self.jobs = {}

    def enqueue_existing(self, tid, execute, **callbacks):
        self.jobs[tid] = execute
        return True


def registered(development):
    root, project, config, data = development
    approvals = StaffApprovals(root)
    snapshot = approvals.snapshot('role', data)
    registry = approvals.registry
    role = registry.register_role(snapshot['data'])  # Trusted owner receipt adapter.
    member = registry.register_member({'id': 'example-dev', 'name': '开发专员', 'role': role['id'], 'engine': 'opencode'})
    pool = Pool()
    service = StaffService(root, pool, execute=None)
    service.execute = partial(execute_staff, root, approval_service=service)
    return registry, role, member, pool, service


def test_project_role_proposal_freezes_registered_target_and_requires_owner(development):
    root, project, config, data = development
    approvals = StaffApprovals(root)
    proposal = approvals.propose('owner', 'role', data, '在登记项目开发')
    assert proposal['approval_level'] == 'desktop'
    binding = proposal['snapshot']['data']['project_binding']
    assert binding == {'name': 'Example', 'repo_root': str(project), 'port': 8099}
    assert not any(role['id'] == data['id'] for role in approvals.registry.roles())
    with pytest.raises(ValueError, match='主人写入路由'):
        approvals.decide(proposal['id'], True, proposal['digest'])
    config.write_text(json.dumps({'projects': [{'name': 'Example', 'repo_root': str(project), 'port': 8100}]}))
    with pytest.raises(ValueError, match='变化'):
        owner_decide(approvals, proposal['id'], True, proposal['digest'])


async def test_single_developer_delivers_branch_and_reworks_without_touching_primary(development, monkeypatch):
    root, project, config, data = development
    registry, role, member, pool, service = registered(development)
    from src.agents import team_engines
    copies = []
    async def external(engine, kind, model, prompt, **kwargs):
        path = Path(kwargs['workdir'])
        copies.append(path)
        assert path != project and not (path / '.env').exists()
        assert kind == 'developer' and engine == 'opencode'
        before = (path / 'app.txt').read_text()
        assert before == ('original\n' if len(copies) == 1 else 'original\nround 1\n')
        (path / 'app.txt').write_text(before + f'round {len(copies)}\n')
        return SimpleNamespace(text='已完成本轮修改', networked=False, error='')
    monkeypatch.setattr(team_engines, 'run_external', external)
    task = service.assign('owner', {'member': member['id'], 'prompt': '补充项目说明', 'acceptance': ['有改动']})
    assert task.kind == 'delegation' and not task.team
    await pool.jobs[task.id]()
    done = service.collaboration('owner').get(task.id)
    assert done.status == 'done' and done.collaboration['review'] == 'pending'
    delivery = done.collaboration['delivery']
    assert delivery['branch'] and delivery['verified'] is None
    assert git(project, 'show', delivery['branch'] + ':app.txt') == 'original\nround 1'
    assert (project / 'app.txt').read_text() == 'original\n'
    assert git(project, 'branch', '--show-current') == 'main'
    assert done.collaboration['usage']['complete'] is False
    service.followup('owner', task.id, 1, '再补一条')
    await pool.jobs[task.id]()
    final = service.collaboration('owner').get(task.id)
    assert final.status == 'done' and final.collaboration['round'] == 2
    assert git(project, 'show', final.collaboration['delivery']['branch'] + ':app.txt') == 'original\nround 1\nround 2'
    assert (project / 'app.txt').read_text() == 'original\n'
    assert all(not path.exists() for path in copies)


async def test_acp_observation_is_bound_to_original_developer_task_not_delivery_acceptance(development, monkeypatch):
    import sys
    from src.agents import team_engines
    from src.workflow.staff.executions import execution_projection
    from tests.unit.test_acp_backend import FAKE
    root, project, config, data = development
    registry, role, member, pool, service = registered(development)
    binary = root.parent / 'fake-opencode'
    binary.write_text('#!' + sys.executable + '\n' + FAKE)
    binary.chmod(0o700)
    monkeypatch.setattr(team_engines, 'resolve_binary', lambda _: str(binary))
    for key, value in {'OPENAI_API_BASE': 'https://relay.example.invalid/v1', 'OPENAI_API_KEY': 'fixture-key',
                       'FAKE_SCENARIO': 'plain', 'FAKE_LOG': str(root.parent / 'acp-log')}.items():
        monkeypatch.setenv(key, value)
    task = service.assign('owner', {'member': member['id'], 'prompt': '检查项目'})
    await pool.jobs[task.id]()
    done = service.collaboration('owner').get(task.id)
    report = execution_projection(done)['snapshot']
    assert report['ref']['task_id'] == task.id and report['ref']['backend_id'] == 'opencode'
    assert report['ref']['session_id'] == 's1' and report['state'] == 'finished'
    assert done.collaboration['review'] != 'accepted'
    assert (project / 'app.txt').read_text() == 'original\n'
    assert git(project, 'branch', '--show-current') == 'main'


async def test_developer_uses_common_entry_once_with_original_task_and_isolated_copy(development, monkeypatch):
    from src.agents import team_engines
    from src.agents.backends import entry
    root, project, config, data = development
    registry, role, member, pool, service = registered(development)
    observed = []
    original_entry = entry.execute_once
    async def traced(binding, call, **kwargs):
        observed.append((binding.backend_id, call))
        return await original_entry(binding, call, **kwargs)
    copies = []
    async def external(engine, kind, model, prompt, **kwargs):
        path = Path(kwargs['workdir'])
        copies.append(path)
        assert path != project and kind == 'developer' and engine == 'opencode'
        assert not (path / '.env').exists()
        (path / 'app.txt').write_text('changed in isolated copy\n')
        return SimpleNamespace(text='已修改', error='', networked=False)
    monkeypatch.setattr(entry, 'execute_once', traced)
    monkeypatch.setattr(team_engines, 'run_external', external)
    task = service.assign('owner', {'member': member['id'], 'prompt': '只改项目副本'})
    await pool.jobs[task.id]()
    saved = service.collaboration('owner').get(task.id)
    assert saved.status == 'done' and saved.collaboration['review'] == 'pending'
    assert len(observed) == len(copies) == 1 and observed[0][0] == 'opencode'
    assert observed[0][1].ref.task_id == task.id
    assert git(project, 'show', saved.collaboration['delivery']['branch'] + ':app.txt') == 'changed in isolated copy'
    assert (project / 'app.txt').read_text() == 'original\n' and not copies[0].exists()


async def test_changed_project_never_starts_queued_development(development, monkeypatch):
    root, project, config, data = development
    registry, role, member, pool, service = registered(development)
    task = service.assign('owner', {'member': member['id'], 'prompt': '补充项目说明'})
    config.write_text('{"projects": []}')
    with pytest.raises(ValueError, match='项目'):
        await pool.jobs[task.id]()
    assert git(project, 'branch', '--show-current') == 'main'
    assert not list((project / '.vortoone' / 'worktrees').glob('*'))


async def test_new_dispatch_waits_for_owner_then_uses_shared_staff_task_once(development, monkeypatch):
    root, project, config, data = development
    registry, role, member, pool, service = registered(development)
    from src.scheduling import staff_runner
    from src.workflow.tasks import assistant_dispatch as dispatch
    monkeypatch.setattr(staff_runner, 'get_staff_service', lambda _root: service)
    entry = dispatch.propose_staff(str(root), project='Example', title='开发事项', goal='补充说明', session='owner')
    assert entry['status'] == 'proposed' and not pool.jobs
    assert entry['staff_binding']['role'] == role
    with pytest.raises(dispatch.DispatchError, match='主人'):
        await dispatch.approve(str(root), entry['id'], decision_source='automatic_review')
    async def forbidden_http(*args, **kwargs):
        raise AssertionError('new work must never use the old project HTTP executor')
    sent = await dispatch.approve(str(root), entry['id'], decision_source='user', http=forbidden_http)
    assert sent['staff_task_id'] in pool.jobs and len(pool.jobs) == 1
    task = service.collaboration('owner').get(sent['staff_task_id'])
    assert task.kind == 'delegation' and not task.team and task.prompt == '补充说明'
    service.collaboration('owner').start(task.id, 1)
    service.collaboration('owner').finish(task.id, 1, '保存的开发结果')
    current = (await dispatch.refresh(str(root), dispatch_id=entry['id'], http=forbidden_http))[0]
    assert current['status'] == 'done' and current['result'] == '保存的开发结果'
    assert len(pool.jobs) == 1


def test_new_dispatch_needs_an_unambiguous_approved_project_specialist(development):
    root, project, config, data = development
    from src.workflow.tasks import assistant_dispatch as dispatch
    with pytest.raises(dispatch.DispatchError, match='专员'):
        dispatch.propose_staff(str(root), project='Example', title='开发事项', goal='补充说明')
    registry, role, member, pool, service = registered(development)
    registry.register_member({'id': 'another-dev', 'name': '另一专员', 'role': role['id'], 'engine': 'opencode'})
    with pytest.raises(dispatch.DispatchError, match='staff_assign'):
        dispatch.propose_staff(str(root), project='Example', title='开发事项', goal='补充说明')


def test_desktop_can_only_propose_fixed_development_authority(development, monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.server import app
    from src.workflow.tasks.tasks import TaskLedger
    root, project, config, data = development
    root.mkdir(exist_ok=True)
    monkeypatch.chdir(root)
    monkeypatch.setenv('VORTOONE_WORKSPACE_SCOPE', 'general')
    monkeypatch.setenv('VORTOONE_API_TOKEN', 'fixture-test-token')
    monkeypatch.setattr('src.gateway.main_conversation.main_chat_key', lambda root: 'owner')
    client = TestClient(app)
    headers = {'Authorization': 'Bearer fixture-test-token'}
    payload = {'kind': 'role', 'id': 'example-developer', 'name': '项目开发', 'project': 'Example'}
    assert client.post('/api/staff/development-proposals', json=payload).status_code == 401
    result = client.post('/api/staff/development-proposals', json=payload, headers=headers)
    assert result.status_code == 200, result.text
    proposal = result.json()
    assert proposal['status'] == 'pending' and proposal['approval_level'] == 'desktop'
    assert proposal['snapshot']['data']['project_binding']['repo_root'] == str(project)
    assert StaffRegistry(root).members() == [] and TaskLedger(str(root)).list() == []
    for field, value in [('grants', {}), ('repo_root', str(project)), ('engine', 'vortocode')]:
        assert client.post('/api/staff/development-proposals', json={**payload, field: value}, headers=headers).status_code == 422
    registry = StaffRegistry(root)
    decision_path = f"/api/staff/proposals/{proposal['id']}/decision"
    assert client.post(decision_path, json=proposal_body(proposal), headers=headers).json()['status'] == 'approved'
    assert registry.role('example-developer')['project_binding']['repo_root'] == str(project)
    member = client.post('/api/staff/development-proposals',
        json={'kind': 'member', 'id': 'example-dev', 'name': '开发专员', 'role': 'example-developer'}, headers=headers)
    assert member.status_code == 200 and member.json()['approval_level'] == 'desktop'
    assert registry.members() == []
    pending = member.json()
    endpoint = f"/api/staff/proposals/{pending['id']}/decision"
    assert client.post(endpoint, json=proposal_body(pending), headers={'Authorization': 'Bearer device-fixture'}).status_code == 401
    assert client.post(endpoint, json=proposal_body(pending), headers=headers).json()['status'] == 'approved'
    assert registry.member('example-dev')['role'] == 'example-developer'
    assert TaskLedger(str(root)).list() == []
    assert not (registry.directory / 'approval-key.json').exists()


def test_project_change_needs_a_fresh_owner_confirmation_and_revokes_queued_authority(development):
    root, project, config, data = development
    approvals = StaffApprovals(root)
    first = approvals.propose('owner', 'role', data, '固定项目开发')
    owner_decide(approvals, first['id'], True, first['digest'])
    registry = approvals.registry
    original_role = registry.role(data['id'])
    member = registry.register_member({'id': 'example-dev', 'name': '开发专员', 'role': data['id'], 'engine': 'opencode'})
    pool = Pool()
    service = StaffService(root, pool, execute=None)
    task = service.assign('owner', {'member': member['id'], 'prompt': '原项目任务'})
    config.write_text(json.dumps({'projects': [
        {'name': 'Example', 'repo_root': str(project), 'port': 8099},
        {'name': 'Other', 'repo_root': str(project), 'port': 8100}]}))
    changed = {**data, 'project': 'Other', 'revision': 1}
    proposal = approvals.propose('owner', 'role', changed, '主人要求换项目')
    assert registry.role(data['id'])['project'] == 'Example'
    with pytest.raises(ValueError, match='变化'):
        owner_decide(approvals, proposal['id'], True, first['digest'],
            **{key: first[key] for key in ('nonce', 'round', 'instance')})
    owner_decide(approvals, proposal['id'], True, proposal['digest'])
    with pytest.raises(ValueError, match='变化'):
        service.check(member, original_role, task=task)
    assert service.collaboration('owner').get(task.id).status == 'queued'


@pytest.mark.parametrize('change', [{'web': True}, {'sees': ['memory']}, {'tools': ['read_file']}])
def test_bound_developer_cannot_misdeclare_external_framework_permissions(development, change):
    root, project, config, data = development
    data['grants'].update(change)
    with pytest.raises(ValueError):
        StaffApprovals(root).propose('owner', 'role', data, '不能扩大权限')


@pytest.mark.parametrize('answer', [True, False])
async def test_tainted_development_waits_for_actual_owner_before_queueing(development, answer):
    from src.agents.gate import make_confirm_gate
    from src.agents.staff import build_staff_tools
    from src.agents.taint import mark_tainted, reset_taint
    registry, role, member, pool, service = registered(development)
    asked = []
    async def owner(message, kind):
        assert not pool.jobs and role['project'] in message
        asked.append(kind)
        return answer
    gate = make_confirm_gate(owner, can_ask_human=True, trust_level='full', capability_profile='local')
    tool = next(item for item in build_staff_tools(service.root, confirm=gate, owner='owner', service=service)
                if item.name == 'staff_assign')
    try:
        mark_tainted()
        await tool.handler({'member': member['id'], 'prompt': '逐字核对开发要求'})
        assert asked == ['write'] and bool(pool.jobs) is answer
    finally:
        reset_taint()


async def test_lost_dispatch_receipt_never_replays_the_existing_staff_job(development, monkeypatch):
    root, project, config, data = development
    registry, role, member, pool, service = registered(development)
    from src.scheduling import staff_runner
    from src.workflow.tasks import assistant_dispatch as dispatch
    monkeypatch.setattr(staff_runner, 'get_staff_service', lambda root: service)
    entry = dispatch.propose_staff(str(root), project='Example', title='开发', goal='改说明', session='owner')
    original = dispatch._update
    failed = False
    def update(root, id, change):
        nonlocal failed
        probe = deepcopy(next(row for row in dispatch.list_dispatches(root) if row['id'] == id))
        change(probe)
        if probe.get('staff_task_id') and not failed:
            failed = True
            raise OSError('synthetic lost receipt')
        return original(root, id, change)
    monkeypatch.setattr(dispatch, '_update', update)
    with pytest.raises(dispatch.DispatchError):
        await dispatch.approve(str(root), entry['id'], decision_source='user')
    assert len(pool.jobs) == 1
    sent = await dispatch.approve(str(root), entry['id'], decision_source='user')
    assert sent['staff_task_id'] in pool.jobs and len(pool.jobs) == 1


@pytest.mark.parametrize('changed', [False, True])
async def test_staff_branch_is_only_published_by_separate_owner_action_and_current_evidence(development, monkeypatch, changed):
    root, project, config, data = development
    registry, role, member, pool, service = registered(development)
    from src.scheduling import staff_runner
    from src.workflow.tasks import assistant_dispatch as dispatch
    from src.agents import vcs
    monkeypatch.setattr(staff_runner, 'get_staff_service', lambda root: service)
    calls = []
    def publish(*args, **kwargs):
        calls.append((args, kwargs))
        return {'ok': True, 'url': 'https://example.invalid/review/1'}
    monkeypatch.setattr(vcs, 'push_and_open_pr', publish)
    entry = dispatch.propose_staff(str(root), project='Example', title='开发', goal='改说明', session='owner')
    sent = await dispatch.approve(str(root), entry['id'], decision_source='user')
    task = service.collaboration('owner').get(sent['staff_task_id'])
    git(project, 'checkout', '-qb', 'vorto/fixture')
    (project / 'app.txt').write_text('changed\n')
    git(project, 'add', 'app.txt')
    git(project, 'commit', '-qm', 'fixture branch')
    head = git(project, 'rev-parse', 'HEAD')
    git(project, 'checkout', '-q', 'main')
    work = service.collaboration('owner')
    work.start(task.id, 1)
    work.update_staff_action(task.id, 1, lambda row: row.collaboration.update(delivery={
        'branch': 'vorto/fixture', 'head': head, 'verified': True}))
    work.finish(task.id, 1, '交回本地分支')
    await dispatch.refresh(str(root), dispatch_id=entry['id'])
    assert not calls
    if changed:
        git(project, 'branch', '-f', 'vorto/fixture', 'main')
        with pytest.raises(dispatch.DispatchError, match='分支已变化'):
            await dispatch.open_pr(str(root), entry['id'])
        assert not calls
    else:
        opened = await dispatch.open_pr(str(root), entry['id'])
        assert opened['pr_url'] == 'https://example.invalid/review/1'
        assert len(calls) == 1 and calls[0][0][:2] == (str(project), 'vorto/fixture') and calls[0][1]['draft'] is True


@pytest.mark.parametrize('explicit', [False, True])
def test_team_cannot_override_a_registered_developers_fixed_project(development, explicit):
    root, project, config, data = development
    registry, role, member, pool, service = registered(development)
    reviewer = registry.register_member({'id': 'review-staff', 'name': '审查', 'role': 'reviewer', 'engine': 'vortocode'})
    from src.workflow.tasks.team import TeamService
    from src.workflow.tasks.errors import CollaborationConflict
    teams = TeamService(str(root))
    args = {'objective': '开发并审查', 'acceptance': ['有分支'], 'members': [member['id'], reviewer['id']],
            'mode': 'private', 'reason': 'independent_review'}
    with pytest.raises(CollaborationConflict, match='固定项目'):
        teams.create('owner', **args, project={'name': 'Other', 'repo_root': str(project)})
    team = teams.create('owner', **args, **({'project': role['project_binding']} if explicit else {}))
    assert team.team['project'] == {'name': 'Example', 'repo_root': str(project)}
    child = teams.assign(team.id, member['name'], '改说明', '交回分支')
    identity, assigned = teams.execution_identity(child.id)
    service.check(identity, assigned, task=child)
    assert assigned['project_binding'] == role['project_binding']


def test_unbound_legacy_project_proposal_keeps_its_original_confirmation_class(development):
    from tests.unit.test_staff_contract import role as read_role
    from src.workflow.staff.approvals import digest
    root, project, config, data = development
    approvals = StaffApprovals(root)
    proposal = approvals.propose('owner', 'role', read_role(project=True, tools=['read_file']), '旧的未接入项目岗位')
    # Existing S3 records classified an unbound read-only role as low risk; it still cannot execute.
    legacy = {key: proposal[key] for key in ('id', 'snapshot', 'owner', 'reason', 'created_at', 'nonce', 'status')}
    legacy['requires_desktop_key'] = False
    legacy['digest'] = digest(legacy)
    approvals.registry._write('proposals.json', {'version': 1, 'proposals': [legacy]})
    loaded = approvals.get(proposal['id'])
    assert loaded['status'] == 'pending' and loaded['approval_level'] == 'normal'
    with pytest.raises(ValueError, match='尚未接入'):
        StaffService.executable(proposal['snapshot']['data'])
