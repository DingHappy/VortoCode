"""Schedule management never changes job contents and rejects stale configuration."""
import hashlib
import json

from fastapi.testclient import TestClient

from src.scheduling.cron import record_outcome
from src.web.server import app


def setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / '.vortocode' / 'cron.yaml'
    path.parent.mkdir()
    path.write_text('jobs:\n  - name: brief\n    schedule: "every 1h"\n    prompt: "original"\n    enabled: true\n  - name: other\n    schedule: "every 2h"\n    prompt: "other"\n')
    return TestClient(app), path


def test_pause_resume_preserves_contents_and_stale_cards_conflict(tmp_path, monkeypatch):
    client, path = setup(tmp_path, monkeypatch)
    item = client.get('/api/assistant/schedule').json()['items'][-2]
    assert item['revision'] == hashlib.sha256(path.read_bytes()).hexdigest()
    before = path.read_text()
    response = client.put('/api/cron/brief/enabled', json={'enabled': False, 'revision': item['revision']})
    assert response.status_code == 200
    assert path.read_text() == before.replace('enabled: true', 'enabled: false')
    assert client.put('/api/cron/brief/enabled', json={'enabled': True, 'revision': item['revision']}).status_code == 409
    revision = response.json()['revision']
    assert client.put('/api/cron/brief/enabled', json={'enabled': True, 'revision': revision}).status_code == 200
    assert path.read_text() == before


def test_rejects_changed_content_missing_jobs_and_non_boolean(tmp_path, monkeypatch):
    client, path = setup(tmp_path, monkeypatch)
    revision = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text(path.read_text().replace('original', 'changed'))
    before = path.read_bytes()
    assert client.put('/api/cron/brief/enabled', json={'enabled': True, 'revision': revision}).status_code == 409
    assert client.put('/api/cron/missing/enabled', json={'enabled': False, 'revision': revision}).status_code == 404
    for value in ['false', 1, None]:
        assert client.put('/api/cron/brief/enabled', json={'enabled': value, 'revision': revision}).status_code == 400
    assert path.read_bytes() == before


def test_history_is_bounded_and_uses_job_attribution(tmp_path, monkeypatch):
    client, _ = setup(tmp_path, monkeypatch)
    for i in range(12):
        record_outcome(str(tmp_path), 'brief', ok=i % 2 == 0, summary=f'result {i}')
    record_outcome(str(tmp_path), 'other', ok=True, summary='private other result')
    payload = client.get('/api/cron/brief/history').json()
    assert payload['retained_only'] is True
    assert len(payload['runs']) == 10
    assert {run['status'] for run in payload['runs']} == {'done', 'failed'}
    assert 'private other result' not in json.dumps(payload)
    assert client.get('/api/cron/missing/history').status_code == 404


def test_scheduler_disabled_is_explicit(tmp_path, monkeypatch):
    client, _ = setup(tmp_path, monkeypatch)
    monkeypatch.delenv('VORTOCODE_CRON', raising=False)
    items = client.get('/api/assistant/schedule').json()['items']
    assert all(item['scheduler_enabled'] is False for item in items if item['kind'] == 'cron')


def test_all_config_editors_obey_shared_lock(tmp_path, monkeypatch):
    import pytest
    from src.scheduling.cron import CronEditError, add_job, set_job_enabled, set_job_prompt, set_job_web
    from src.workflow.pipeline.pipeline_storage import pipeline_lock

    client, path = setup(tmp_path, monkeypatch)
    before = path.read_bytes()
    revision = hashlib.sha256(before).hexdigest()
    with pipeline_lock(str(tmp_path), 'cron-config', kind='edit'):
        assert client.put('/api/cron/brief/enabled', json={'enabled': False, 'revision': revision}).status_code == 409
        for operation in [lambda: set_job_enabled(str(tmp_path), 'brief', False),
                          lambda: set_job_prompt(str(tmp_path), 'brief', 'changed'),
                          lambda: set_job_web(str(tmp_path), 'brief', True),
                          lambda: add_job(str(tmp_path), name='new', schedule='every 1h', prompt='new')]:
            with pytest.raises(CronEditError):
                operation()
    assert path.read_bytes() == before
