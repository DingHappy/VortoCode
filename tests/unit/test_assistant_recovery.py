from copy import deepcopy

import pytest

from src.workflow.tasks.assistant_recovery import recovery_observation
from src.workflow.tasks.task_recovery import recovery_task_id


def pair():
    source = {'id': 'task-original', 'kind': 'dev', 'status': 'failed', 'prompt': 'repair',
              'owner_session': 'sid-owner', 'goal_id': '', 'plan_id': 'p1', 'branch': 'vorto/fix',
              'resumed_task_id': recovery_task_id('task-original'), 'recovery_source_revision': 'a' * 64,
              'execution_scope': {'repo_root': '/srv/demo'}}
    child = {**source, 'id': source['resumed_task_id'], 'kind': 'dev-resume', 'status': 'running',
             'parent_task_id': source['id'], 'resumed_task_id': '', 'result': '', 'error': '',
             'recovery': {'source_task_id': source['id'], 'source_revision': 'a' * 64, 'plan_revision': 'b' * 64}}
    return source, child


def test_observation_keeps_origin_and_latest_outcomes_distinct():
    source, child = pair()
    original = deepcopy(source)
    result = recovery_observation(source, child, repo_root='/srv/demo')
    assert result['status'] == 'running' and source == original
    child['status'], child['result'] = 'done', 'tests passed'
    assert recovery_observation(source, child, repo_root='/srv/demo')['result'] == 'tests passed'


@pytest.mark.parametrize('field,value', [('id', 'other'), ('parent_task_id', 'other'), ('kind', 'dev'),
    ('plan_id', 'other'), ('owner_session', 'sid-other'), ('prompt', 'other'), ('goal_id', 'other'),
    ('status', 'invented'), ('branch', 'vorto/other'), ('execution_scope', {'repo_root': '/other'}),
    ('recovery', {'source_task_id': 'task-original', 'source_revision': 'c' * 64, 'plan_revision': 'b' * 64})])
def test_unrelated_or_changed_child_cannot_be_observed(field, value):
    source, child = pair()
    child[field] = value
    with pytest.raises(ValueError, match='不匹配'):
        recovery_observation(source, child, repo_root='/srv/demo')


@pytest.mark.asyncio
async def test_explicit_refresh_reads_recovery_without_post_or_replacing_original(tmp_path, monkeypatch):
    import json
    from src.workflow.tasks import assistant_dispatch as dispatch
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'projects': [{'name': 'demo', 'repo_root': '/srv/demo', 'port': 8082}]}))
    monkeypatch.setenv('VORTOCODE_RESIDENT_PROJECTS', str(registry))
    entry = dispatch.propose(str(tmp_path), project='demo', title='fix', goal='repair')
    dispatch._update(str(tmp_path), entry['id'], lambda item: item.update(status='failed', remote_task_id='task-original'))
    source, child = pair()
    source['branch'] = child['branch'] = ''
    requests = []
    async def http(method, url, payload=None):
        requests.append((method, url))
        return child if url.endswith(child['id']) else source
    saved = (await dispatch.refresh(str(tmp_path), dispatch_id=entry['id'], http=http))[0]
    assert saved['remote_task_id'] == 'task-original' and saved['status'] == 'failed'
    assert saved['recovery_task']['id'] == child['id'] and saved['recovery_task']['status'] == 'running'
    assert len(requests) == 2 and all(method == 'GET' for method, _ in requests)
    assert saved['recovery_error'] == ''
    child['owner_session'] = 'sid-other'
    saved = (await dispatch.refresh(str(tmp_path), dispatch_id=entry['id'], http=http))[0]
    assert saved['recovery_task'] is None and saved['recovery_error']
    assert saved['status'] == 'failed' and saved['remote_task_id'] == 'task-original'


@pytest.mark.asyncio
async def test_wrong_recovery_hint_is_rejected_before_reading_other_task(tmp_path, monkeypatch):
    import json
    from src.workflow.tasks import assistant_dispatch as dispatch
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'projects': [{'name': 'demo', 'repo_root': '/srv/demo', 'port': 8082}]}))
    monkeypatch.setenv('VORTOCODE_RESIDENT_PROJECTS', str(registry))
    entry = dispatch.propose(str(tmp_path), project='demo', title='fix', goal='repair')
    dispatch._update(str(tmp_path), entry['id'], lambda item: item.update(status='failed', remote_task_id='task-original'))
    source, _ = pair()
    source['branch'] = ''
    source['resumed_task_id'] = '../../other-task'
    calls = []
    async def http(method, url, payload=None):
        calls.append(url)
        return source
    saved = (await dispatch.refresh(str(tmp_path), dispatch_id=entry['id'], http=http))[0]
    assert len(calls) == 1 and saved['recovery_task'] is None
    assert '未读取其它任务' in saved['recovery_error']


@pytest.mark.asyncio
async def test_unavailable_recovery_review_keeps_checked_progress_without_pass(tmp_path, monkeypatch):
    import json
    from src.workflow.tasks import assistant_dispatch as dispatch
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'projects': [{'name': 'demo', 'repo_root': '/srv/demo', 'port': 8082}]}))
    monkeypatch.setenv('VORTOCODE_RESIDENT_PROJECTS', str(registry))
    entry = dispatch.propose(str(tmp_path), project='demo', title='fix', goal='repair')
    dispatch._update(str(tmp_path), entry['id'], lambda item: item.update(status='failed', remote_task_id='task-original'))
    source, child = pair()
    child['status'] = 'done'
    async def http(method, url, payload=None):
        assert method == 'GET'
        if url.endswith('/review'):
            raise OSError('review unavailable')
        return child if url.endswith(child['id']) else source
    saved = (await dispatch.refresh(str(tmp_path), dispatch_id=entry['id'], http=http))[0]
    assert saved['recovery_task']['status'] == 'done'
    assert 'delivery_evidence' not in saved['recovery_task']
    assert saved['recovery_error'] and saved['status'] == 'failed'


@pytest.mark.parametrize('mutation', ['revision', 'missing', 'plan', 'kind', 'status'])
def test_cached_observation_does_not_survive_changed_source(mutation):
    from src.workflow.tasks.assistant_recovery import observation_matches_source
    source, child = pair()
    observed = recovery_observation(source, child, repo_root='/srv/demo')
    assert observation_matches_source(observed, source)
    changes = {'revision': {'recovery_source_revision': 'c' * 64}, 'missing': {'resumed_task_id': ''},
               'plan': {'plan_id': 'p2'}, 'kind': {'kind': 'research'}, 'status': {'status': 'done'}}
    source.update(changes[mutation])
    assert not observation_matches_source(observed, source)


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', [False, True])
@pytest.mark.parametrize('child_status', ['running', 'done'])
async def test_routine_poll_checks_recovery_association_without_reading_child(tmp_path, monkeypatch, changed, child_status):
    import json
    from src.workflow.tasks import assistant_dispatch as dispatch
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'projects': [{'name': 'demo', 'repo_root': '/srv/demo', 'port': 8082}]}))
    monkeypatch.setenv('VORTOCODE_RESIDENT_PROJECTS', str(registry))
    source, child = pair()
    child['status'] = child_status
    source['branch'] = child['branch'] = ''
    observed = recovery_observation(source, child, repo_root='/srv/demo')
    observed['checked_at'] = '2026-10-13T00:00:00Z'
    entry = dispatch.propose(str(tmp_path), project='demo', title='fix', goal='repair')
    dispatch._update(str(tmp_path), entry['id'], lambda item: item.update(status='failed', remote_task_id=source['id'], recovery_task=observed))
    if changed:
        source['recovery_source_revision'] = 'c' * 64
    calls = []
    async def http(method, url, payload=None):
        calls.append((method, url))
        return source
    saved = (await dispatch.refresh(str(tmp_path), http=http))[0]
    assert len(calls) == 1 and calls[0][0] == 'GET'
    if changed:
        assert saved['recovery_task'] is None and saved['recovery_error']
    else:
        assert saved['recovery_task'] == observed  # The child's checked_at is never silently advanced.
    assert saved['remote_task_id'] == source['id'] and saved['status'] == 'failed'


@pytest.mark.asyncio
async def test_offline_recovery_keeps_historical_observation_marked_unreachable(tmp_path, monkeypatch):
    import json
    from src.workflow.tasks import assistant_dispatch as dispatch
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'projects': [{'name': 'demo', 'repo_root': '/srv/demo', 'port': 8082}]}))
    monkeypatch.setenv('VORTOCODE_RESIDENT_PROJECTS', str(registry))
    source, child = pair()
    observed = recovery_observation(source, child, repo_root='/srv/demo')
    entry = dispatch.propose(str(tmp_path), project='demo', title='fix', goal='repair')
    dispatch._update(str(tmp_path), entry['id'], lambda item: item.update(status='failed', remote_task_id=source['id'], recovery_task=observed))
    async def offline(*args, **kwargs):
        raise OSError('offline')
    saved = (await dispatch.refresh(str(tmp_path), http=offline))[0]
    assert saved['recovery_task'] == observed and saved['sync_state'] == 'unreachable'
