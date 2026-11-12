"""L0 is a read-only projection, never an approval authority."""
import json
from datetime import datetime, timezone

import pytest

from src.gateway.audit import append_audit, record_decision_audit

NOW = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)


def row(root, **changes):
    value = {'category': 'decision', 'ts': NOW.isoformat(), 'operation_kind': 'own_data',
             'decision_source': 'user', 'approval_scope': 'once', 'approval_outcome': 'approved',
             'decision': 'allowed', 'tainted': False, 'wait_seconds': 8,
             'approval_source': 'main', 'operation': 'PRIVATE BODY token=never-copy'}
    value.update(changes)
    assert append_audit(str(root), value)


def stats(root, **kwargs):
    from src.workflow.staff.approval_stats import approval_statistics
    return approval_statistics(str(root), now=NOW, **kwargs)


def test_daily_statistics_match_audit_decisions_without_copying_action_content(tmp_path):
    for outcome, delay in [('approved', 2), ('approved', 10), ('denied', 5), ('expired', None)]:
        row(tmp_path, approval_outcome=outcome, decision='allowed' if outcome == 'approved' else 'denied', wait_seconds=delay)
    row(tmp_path, approval_source='staff', source_id='staff-reader', tainted=True)
    row(tmp_path, approval_source='routine', source_id='routine-morning')
    row(tmp_path, ts='2026-10-01T00:00:00+00:00')
    result = stats(tmp_path)
    main = next(x for x in result['groups'] if x['source'] == 'main')
    assert (main['count'], main['approved'], main['denied'], main['expired']) == (4, 2, 1, 1)
    assert main['median_wait_seconds'] == 5 and main['timed'] == 3
    assert {x['source_id'] for x in result['groups']} == {'', 'staff-reader', 'routine-morning'}
    assert result['days'] == 7 and result['totals']['count'] == 6
    assert 'PRIVATE' not in json.dumps(result) and 'operation' not in result
    assert sum(x['count'] for x in result['groups']) == result['totals']['count']


def test_auto_allows_and_system_refusals_are_not_counted_as_owner_cards(tmp_path):
    bases = ['existing_trust', 'turn', 'task', 'standing', 'task_workspace', 'protective']
    for basis in bases:
        row(tmp_path, approval_scope=basis, decision_source='preauthorized')
    row(tmp_path, decision_source='system', decision='denied', approval_outcome='denied')
    result = stats(tmp_path)
    assert result['totals']['count'] == 0
    assert result['automatic'] == dict.fromkeys(bases, 1)
    assert result['system_denied'] == 1


def test_legacy_unknowns_and_truncated_history_are_explicit(tmp_path, monkeypatch):
    import src.gateway.audit as audit
    row(tmp_path, operation_kind='invented', approval_source='secret body', source_id='https://private',
        tainted=None, wait_seconds=None)
    result = stats(tmp_path)
    group = result['groups'][0]
    assert group['kind'] == 'unknown' and group['source'] == 'other' and group['source_id'] == ''
    assert group['tainted'] == 'unknown' and group['median_wait_seconds'] is None
    monkeypatch.setattr(audit, '_MAX_READ_BYTES', 1)
    assert stats(tmp_path)['coverage']['audit'] == 'partial'


def test_statistics_does_not_silently_cap_at_the_timeline_500_rows(tmp_path):
    for _ in range(505):
        row(tmp_path)
    assert stats(tmp_path)['totals']['count'] == 505


async def test_live_staff_card_is_counted_once_before_and_after_owner_decides(tmp_path, monkeypatch):
    from tests.unit.test_staff_actions import live_staff, next_card, decide
    from src.workflow.tasks.tasks import TaskLedger
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch)
    clock = [NOW.timestamp() - 60]
    service.actions.clock = lambda: clock[0]
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': 'PRIVATE TASK'})
        _, card = await next_card(waiting)
        assert stats(tmp_path)['totals']['pending'] == 1 and not invoked
        clock[0] += 15
        decide(service, task, card)
        await service.runner.join(task.id)
        result = stats(tmp_path)
        assert result['totals']['count'] == 1 and result['totals']['approved'] == 1
        assert result['totals']['median_wait_seconds'] == 15
        assert result['groups'][0]['source_id'] == member['id']
        assert len(invoked) == 1 and TaskLedger(tmp_path).load(task.id).status == 'done'
    finally:
        await service.runner.shutdown()


def test_task_receipts_add_expired_routine_cards_but_never_duplicate_decision_audit(tmp_path):
    from src.workflow.tasks.tasks import BackgroundTask, TaskLedger
    task = BackgroundTask.new('delegation', 'PRIVATE TASK')
    created = '2026-11-12T10:00:00+00:00'
    expires = datetime(2026, 11, 12, 11, tzinfo=timezone.utc).timestamp()
    task.collaboration = {'dispatch': {'source': 'staff', 'member': 'staff-reader',
                                     'routine': {'id': 'routine-morning'}},
                          'approval': {'status': 'pending', 'payload': {'id': 'staff-action-old',
                            'created': created, 'expires_at': expires, 'kind': 'draft', 'tainted': True}},
                          'approval_history': [{'id': 'staff-action-done', 'status': 'approved', 'kind': 'draft',
                                                'decided_at': created, 'tainted': False}]}
    TaskLedger(tmp_path).save(task)
    row(tmp_path, staff_action='staff-action-done', staff_task=task.id, approval_source='other')
    result = stats(tmp_path)
    assert result['totals']['count'] == 2 and result['totals']['expired'] == 1
    assert result['totals']['approved'] == 1 and result['coverage']['task_cards'] == 1
    expired = next(x for x in result['groups'] if x['expired'])
    assert expired['source'] == 'routine' and expired['source_id'] == 'routine-morning'
    assert expired['median_wait_seconds'] == 3600
    assert TaskLedger(tmp_path).load(task.id).collaboration['approval']['status'] == 'pending'
    assert 'PRIVATE' not in json.dumps(result)


def test_separate_scope_grant_audit_does_not_inflate_the_card_count(tmp_path):
    row(tmp_path, approval_event='scope_grant')
    row(tmp_path, approval_scope='turn')
    result = stats(tmp_path)
    assert result['totals']['count'] == 1 and result['coverage']['scope_grants'] == 1


def test_read_failures_and_invalid_timestamps_cannot_be_reported_as_complete(tmp_path, monkeypatch):
    from src.workflow.tasks.tasks import TaskLedger, LedgerScan
    row(tmp_path, ts='invalid')
    monkeypatch.setattr(TaskLedger, 'scan', lambda _: LedgerScan((), 0, 0, False, False))
    result = stats(tmp_path)
    assert result['coverage']['tasks'] == 'unavailable'
    assert result['coverage']['invalid_timestamps'] == 1
    assert result['totals']['count'] == 0


def test_statistics_route_requires_owner_auth_and_is_read_only(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.server import app
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VORTOONE_API_TOKEN', 'offline-statistics-owner')
    row(tmp_path, ts=datetime.now(timezone.utc).isoformat())
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    client = TestClient(app)
    assert client.get('/api/staff/approval-stats').status_code in (401, 403)
    response = client.get('/api/staff/approval-stats', headers={'Authorization': 'Bearer offline-statistics-owner'})
    assert response.status_code == 200 and response.json()['totals']['count'] == 1
    assert client.post('/api/staff/approval-stats', headers={'Authorization': 'Bearer offline-statistics-owner'}).status_code == 405
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


async def test_confirmation_timing_and_existing_scope_metadata_survive_auditing(tmp_path, monkeypatch):
    from src.agents import confirmation_context as context
    clock = [100.0]
    monkeypatch.setattr(context.time, 'monotonic', lambda: clock[0])
    with context.presenting_approval('own_data', source='user', scope='once'):
        clock[0] += 12
        context.record_approval_outcome('approved')
        assert record_decision_audit(str(tmp_path), session='fixture', mode='build', operation='PRIVATE',
                                     decision=True, tainted=False)
    for basis in ('task_workspace', 'protective'):
        assert record_decision_audit(str(tmp_path), session='fixture', mode='build', operation='PRIVATE', decision=True,
                                    tainted=False, approval={'approval_scope': basis, 'decision_source': 'preauthorized'})
    records = [json.loads(x) for x in (tmp_path / '.vortoone/audit.log').read_text().splitlines()]
    assert records[0]['wait_seconds'] == 12
    assert [x['approval_scope'] for x in records[1:]] == ['task_workspace', 'protective']


@pytest.mark.parametrize('days', [0, 32, True])
def test_window_is_bounded(tmp_path, days):
    with pytest.raises(ValueError):
        stats(tmp_path, days=days)
