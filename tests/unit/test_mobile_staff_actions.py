"""L6: signed owner-device decisions on the existing live staff continuation."""
import asyncio
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_relay_envelope import phone, envelope
from tests.unit.test_staff_actions import live_staff, next_card
from tests.unit.test_mobile_owner import owner, setup, bind, signed  # noqa: F401 - imported fixture dependencies

READ_PATH = '/api/assistant/app-relay/staff-actions'
WRITE_PATH = '/api/assistant/app-relay/staff-action'
PHONE_PATH = '/api/mobile/native/staff/action'


def resident(root, service, monkeypatch):
    from src.web.server import app
    monkeypatch.chdir(root)
    monkeypatch.setenv('VORTOONE_WORKSPACE_SCOPE', 'general')
    monkeypatch.setenv('VORTOONE_APP_RELAY_TOKEN', 'fixture-relay')
    monkeypatch.setattr('src.scheduling.staff_runner.get_staff_service', lambda _: service)
    client = TestClient(app, client=('127.0.0.1', 50000))
    return client, {'Authorization': 'Bearer fixture-relay'}


def decision(task, card, approve=True, **changes):
    return {'task_id': task.id, 'action_id': card['payload']['id'], 'digest': card['digest'],
            **{key: card['payload'][key] for key in ('nonce', 'round', 'instance', 'runtime')},
            'approve': approve, 'reason': '' if approve else '本次先不执行', **changes}


@pytest.mark.parametrize('kind', ['read', 'own_data', 'draft'])
@pytest.mark.parametrize('approve', [True, False])
async def test_signed_ordinary_card_resumes_the_same_task_once(tmp_path, monkeypatch, kind, approve):
    service, member, waiting, _, invoked, _, starts = live_staff(tmp_path, monkeypatch, kind=kind)
    device_id, key, _ = phone(tmp_path)
    client, headers = resident(tmp_path, service, monkeypatch)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '核对后执行'})
        _, card = await next_card(waiting)
        read = client.post(READ_PATH, headers=headers, json={})
        assert read.status_code == 200
        row, = read.json()['cards']
        assert row['can_decide'] and row['args_text'] and row['tainted'] is False
        assert not invoked
        body = decision(task, card, approve)
        first = client.post(WRITE_PATH, headers=headers, json={'envelope': envelope(device_id, key, PHONE_PATH, body)})
        assert first.status_code == 200 and first.json()['replayed'] is False
        second = client.post(WRITE_PATH, headers=headers, json={'envelope': envelope(device_id, key, PHONE_PATH, body)})
        assert second.status_code == 200 and second.json()['replayed'] is True
        await asyncio.wait_for(service.runner.join(task.id), 2)
        assert len(invoked) == int(approve) and starts == [(task.id, 1)]
        assert service.actions.view(task.id)['payload']['id'] == card['payload']['id']
        assert 'scoped_approval' not in service.runner.ledger.load(task.id).collaboration
    finally:
        await service.runner.shutdown()


@pytest.mark.parametrize('kind', ['write', 'execute', 'interact'])
@pytest.mark.parametrize('approve', [True, False])
async def test_high_risk_cards_are_readonly_even_with_a_genuine_phone_signature(tmp_path, monkeypatch, kind, approve):
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch, kind=kind)
    device_id, key, approval = phone(tmp_path)
    client, headers = resident(tmp_path, service, monkeypatch)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '核对后执行'})
        _, card = await next_card(waiting)
        row, = client.post(READ_PATH, headers=headers, json={}).json()['cards']
        assert not row['can_decide'] and row['approval_level'] == 'desktop'
        for signing_key, key_kind in ((key, 'device'), (approval, 'approval')):
            response = client.post(WRITE_PATH, headers=headers, json={'envelope': envelope(
                device_id, signing_key, PHONE_PATH, decision(task, card, approve), key_kind=key_kind)})
            assert response.status_code == 409
        assert not invoked and service.actions.view(task.id)['status'] == 'pending'
    finally:
        await service.runner.shutdown()


@pytest.mark.parametrize('failure', ['unsigned', 'foreign_path', 'tampered', 'nonce_replay', 'revoked', 'audit'])
async def test_resident_rechecks_the_signed_source_before_allowing_work(tmp_path, monkeypatch, failure):
    from src.gateway.mobile_devices import MobileDevices
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch)
    device_id, key, _ = phone(tmp_path)
    client, headers = resident(tmp_path, service, monkeypatch)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '核对后执行'})
        _, card = await next_card(waiting)
        body = decision(task, card)
        signed_request = envelope(device_id, key, PHONE_PATH, body)
        if failure == 'unsigned': signed_request = None
        elif failure == 'foreign_path': signed_request = envelope(device_id, key, '/api/mobile/native/capture', body)
        elif failure == 'tampered': signed_request['signature'] = 'changed'
        elif failure == 'nonce_replay': MobileDevices(str(tmp_path)).consume_nonce(device_id, 'relay:' + signed_request['nonce'])
        elif failure == 'revoked': MobileDevices(str(tmp_path)).revoke(device_id)
        else: monkeypatch.setattr('src.gateway.audit.append_audit', lambda *args, **kwargs: False)
        response = client.post(WRITE_PATH, headers=headers, json={'envelope': signed_request})
        assert response.status_code in {401, 409}
        assert not invoked and service.actions.view(task.id)['status'] == 'pending'
    finally:
        await service.runner.shutdown()


@pytest.mark.parametrize('change', ['expiry', 'runtime', 'instance', 'member', 'role'])
async def test_signed_decision_cannot_revive_an_expired_or_changed_wait(tmp_path, monkeypatch, change):
    from src.workflow.staff.actions import StaffActions
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch)
    device_id, key, _ = phone(tmp_path)
    client, headers = resident(tmp_path, service, monkeypatch)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '核对后执行'})
        _, card = await next_card(waiting)
        if change == 'expiry': service.actions.clock = lambda: card['payload']['expires_at']
        elif change == 'runtime': service.actions = StaffActions(service)
        elif change == 'instance': monkeypatch.setattr('src.workflow.staff.owner_confirmation._PROCESS_INSTANCE', 'restart')
        elif change == 'member': service.registry.set_member_status(member['id'], 'paused', member['revision'])
        else:
            role = service.registry.role('assistant'); role['brief'] += '新职责'; service.registry.register_role(role)
        response = client.post(WRITE_PATH, headers=headers, json={'envelope': envelope(device_id, key, PHONE_PATH, decision(task, card))})
        assert response.status_code == 409 and not invoked
    finally:
        await service.runner.shutdown()


async def test_a_mobile_signature_scope_never_becomes_a_registration_owner_route(tmp_path, monkeypatch):
    from src.workflow.staff.owner_confirmation import signed_staff_action
    from src.workflow.staff.approvals import StaffApprovals
    from tests.unit.test_staff_contract import role
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '核对后执行'})
        _, card = await next_card(waiting)
        body = decision(task, card)
        with pytest.raises(ValueError, match='验签'):
            service.actions.decide_mobile(body)
        approvals = StaffApprovals(str(tmp_path)); proposal = approvals.propose('owner', 'role', role(), '核对')
        with signed_staff_action(tmp_path, body), pytest.raises(ValueError, match='主人写入路由'):
            approvals.decide(proposal['id'], True, proposal['digest'])
        assert not invoked and approvals.get(proposal['id'])['status'] == 'pending'
    finally:
        await service.runner.shutdown()


async def test_gateway_verifies_then_forwards_the_original_signature_to_the_resident(owner, monkeypatch):  # noqa: F811
    phone_client, headers, device_id, root = owner
    _, device_key, _ = bind(phone_client, headers)
    service, member, waiting, _, invoked, _, _ = live_staff(root, monkeypatch)
    resident_client, relay_headers = resident(root, service, monkeypatch)
    forwarded = []
    async def relay(path, body):
        forwarded.append(body)
        response = resident_client.post(path, headers=relay_headers, json=body)
        return response.status_code, response.json()
    monkeypatch.setattr('src.gateway.app_relay_client.relay', relay)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '核对后执行'})
        _, card = await next_card(waiting)
        rows = phone_client.get('/api/mobile/native/staff/actions', headers=headers)
        assert rows.status_code == 200 and rows.json()['cards'][0]['id'] == card['payload']['id']
        count = len(forwarded)
        assert phone_client.post(PHONE_PATH, headers=headers, json=decision(task, card)).status_code == 401
        assert len(forwarded) == count and not invoked
        response = signed(phone_client, headers, device_id, PHONE_PATH, decision(task, card), device_key)
        assert response.status_code == 200 and response.json()['status'] == 'approved'
        await service.runner.join(task.id)
        assert len(invoked) == 1 and 'envelope' in forwarded[-1]
    finally:
        await service.runner.shutdown()


async def test_pages_preserve_full_unicode_content_and_report_incomplete_scans(tmp_path, monkeypatch):
    from src.workflow.tasks.tasks import TaskLedger
    text = '完整资料🙂' * 600
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch, text=text)
    try:
        tasks = [service.assign('owner', {'member': member['id'], 'prompt': '核对完整资料'}) for _ in range(9)]
        for _ in tasks: await next_card(waiting)
        first = service.actions.mobile_snapshot()
        assert first['next'] and len(first['cards']) < 9
        assert len(json.dumps(first, ensure_ascii=False).encode()) < 300 * 1024
        second = service.actions.mobile_snapshot(after=first['next'])
        assert second['next'] is None
        cards = first['cards'] + second['cards']
        assert {card['task_id'] for card in cards} == {task.id for task in tasks}
        assert all(json.loads(card['args_text'])['text'] == text for card in cards)
        assert first['source_complete'] and second['source_complete'] and not invoked
        scan = TaskLedger(str(tmp_path)).scan()
        monkeypatch.setattr(TaskLedger, 'scan', lambda self: replace(scan, truncated=True))
        assert service.actions.mobile_snapshot()['source_complete'] is False
    finally:
        await service.runner.shutdown()


def test_relay_routes_require_the_dedicated_token_and_loopback_even_for_reads(tmp_path, monkeypatch):
    from src.web.server import app
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VORTOONE_WORKSPACE_SCOPE', 'general')
    monkeypatch.setenv('VORTOONE_API_TOKEN', 'fixture-admin')
    monkeypatch.setenv('VORTOONE_APP_RELAY_TOKEN', 'fixture-relay')
    for client in (TestClient(app, client=('127.0.0.1', 50000)), TestClient(app, client=('198.51.100.1', 50000))):
        for path in (READ_PATH, WRITE_PATH):
            assert client.post(path, json={}).status_code == 404
            assert client.post(path, json={}, headers={'Authorization': 'Bearer fixture-admin'}).status_code == 404
    remote = TestClient(app, client=('198.51.100.1', 50000))
    assert remote.post(READ_PATH, json={}, headers={'Authorization': 'Bearer fixture-relay'}).status_code == 404


@pytest.mark.parametrize('field,value', [('digest', 'changed'), ('nonce', 'changed'), ('round', 2),
    ('instance', 'previous-instance'), ('runtime', 'previous-runtime'), ('action_id', 'staff-action-' + '0' * 32),
    ('scope', 'task'), ('standing', ['own_data'])])
async def test_signed_stale_or_expanded_decision_cannot_execute(tmp_path, monkeypatch, field, value):
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch)
    device_id, key, _ = phone(tmp_path)
    client, headers = resident(tmp_path, service, monkeypatch)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '核对后执行'})
        _, card = await next_card(waiting)
        body = decision(task, card, **{field: value})
        response = client.post(WRITE_PATH, headers=headers, json={'envelope': envelope(device_id, key, PHONE_PATH, body)})
        assert response.status_code in {400, 409}
        assert not invoked and service.actions.view(task.id)['status'] == 'pending'
    finally:
        await service.runner.shutdown()
