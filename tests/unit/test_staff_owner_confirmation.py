"""Staff authority comes only from an explicit authenticated owner write."""
import json

import pytest

from src.workflow.staff.approvals import StaffApprovals
from tests.unit.test_staff_contract import role
from tests.unit.staff_owner_helpers import action_body, proposal_body


@pytest.fixture
def owner_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.server import app
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VORTOONE_API_TOKEN', 'offline-staff-owner')
    client = TestClient(app)
    client.headers['Authorization'] = 'Bearer offline-staff-owner'
    return client


async def test_tool_confirmation_creates_pending_proposal_without_registering_even_readonly(tmp_path):
    from src.agents.gate import make_confirm_gate
    from src.agents.staff_proposals import build_staff_proposal_tool
    from src.agents.taint import reset_taint
    reset_taint()
    async def confirm(message, kind):
        return True
    tool = build_staff_proposal_tool(str(tmp_path), owner='owner',
        confirm=make_confirm_gate(confirm, can_ask_human=True))
    result = json.loads(await tool.handler({'kind': 'role', 'data': role(), 'reason': '独立只读核对'}))
    assert result['proposal']['status'] == 'pending'
    assert not any(x['id'] == 'reader' for x in StaffApprovals(str(tmp_path)).registry.roles())


def test_owner_route_rejects_cookie_only_approval(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.auth import SESSION_COOKIE
    from src.web.server import app
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VORTOONE_API_TOKEN', 'offline-staff-owner')
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'role', role(), '独立核对')
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, 'offline-staff-owner')
    response = client.post(f"/api/staff/proposals/{proposal['id']}/decision",
        json={'approve': True, 'digest': proposal['digest']})
    assert response.status_code == 403
    assert approvals.get(proposal['id'])['status'] == 'pending'


@pytest.mark.parametrize('data', [role(), role(web=True, tools=['web_search']),
    role(sees=['plugin:calendar'], tools=['calendar_events'])])
def test_owner_route_registers_exact_proposal_without_key_or_enrollment(tmp_path, owner_client, data):
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'role', data, '独立核对')
    assert not any(r['id'] == 'reader' for r in approvals.registry.roles())
    visible = owner_client.get('/api/staff').json()
    assert 'approval_key' not in visible and 'requires_desktop_key' not in visible['proposals'][0]
    endpoint = f"/api/staff/proposals/{proposal['id']}/decision"
    result = owner_client.post(endpoint, json=proposal_body(proposal))
    assert result.status_code == 200 and result.json()['status'] == 'approved'
    assert result.json()['decision']['source'] == 'owner_route'
    assert approvals.registry.role('reader')['revision'] == 1
    assert owner_client.post(endpoint, json=proposal_body(proposal)).json() == result.json()
    assert approvals.registry.role('reader')['revision'] == 1
    assert not (approvals.registry.directory / 'approval-key.json').exists()


@pytest.mark.parametrize('change', [{'digest': '0' * 64}, {'nonce': '0' * 32}, {'round': 99}, {'instance': '0' * 32}])
def test_owner_route_rejects_changed_proposal_binding(tmp_path, owner_client, change):
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'role', role(web=True), '公开核对')
    result = owner_client.post(f"/api/staff/proposals/{proposal['id']}/decision", json=proposal_body(proposal, **change))
    assert result.status_code == 409
    assert approvals.get(proposal['id'])['status'] == 'pending'
    assert not any(r['id'] == 'reader' for r in approvals.registry.roles())


def test_restart_rejects_old_proposal_card_but_fresh_owner_review_is_possible(tmp_path, owner_client, monkeypatch):
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'role', role(), '只读核对')
    monkeypatch.setattr('src.workflow.staff.owner_confirmation._PROCESS_INSTANCE', 'restarted-process')
    endpoint = f"/api/staff/proposals/{proposal['id']}/decision"
    assert owner_client.post(endpoint, json=proposal_body(proposal)).status_code == 409
    assert approvals.get(proposal['id'])['status'] == 'pending'
    fresh = owner_client.get('/api/staff').json()['proposals'][0]
    assert fresh['instance'] != proposal['instance'] and fresh['digest'] == proposal['digest']
    assert owner_client.post(endpoint, json=proposal_body(fresh)).json()['status'] == 'approved'


@pytest.mark.parametrize('obsolete_file', ['{"version":1,"instance":"old","key":null}', 'obsolete legacy bytes'])
def test_legacy_pending_proposal_and_retired_key_file_are_readable_without_rewriting(tmp_path, owner_client, obsolete_file):
    from src.workflow.staff.approvals import digest
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'role', role(web=True), '旧待批提议')
    legacy = {key: proposal[key] for key in ('id', 'snapshot', 'owner', 'reason', 'created_at', 'nonce', 'status')}
    legacy['requires_desktop_key'] = True
    legacy['digest'] = digest(legacy)
    approvals.registry._write('proposals.json', {'version': 1, 'proposals': [legacy]})
    key_file = approvals.registry.directory / 'approval-key.json'
    key_file.write_text(obsolete_file)
    before = key_file.read_bytes()
    fresh = owner_client.get('/api/staff').json()['proposals'][0]
    assert fresh['digest'] == legacy['digest'] and 'requires_desktop_key' not in fresh
    assert owner_client.post(f"/api/staff/proposals/{fresh['id']}/decision", json=proposal_body(fresh)).status_code == 200
    assert approvals.registry.role('reader')['grants']['web']
    assert key_file.read_bytes() == before


def test_proposal_audit_is_durable_before_registration_and_failure_keeps_it_pending(tmp_path, owner_client, monkeypatch):
    from src.gateway.audit import append_audit
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'role', role(), '审计后登记')
    endpoint = f"/api/staff/proposals/{proposal['id']}/decision"
    monkeypatch.setattr('src.gateway.audit.append_audit', lambda *args, **kwargs: False)
    assert owner_client.post(endpoint, json=proposal_body(proposal)).status_code == 409
    assert approvals.get(proposal['id'])['status'] == 'pending'
    calls = []
    def audit(root, entry, *, durable):
        assert durable and not any(r['id'] == 'reader' for r in approvals.registry.roles())
        assert entry['action_digest'] == proposal['digest'] and entry['nonce'] == proposal['nonce']
        calls.append(entry)
        return append_audit(root, entry, durable=durable)
    monkeypatch.setattr('src.gateway.audit.append_audit', audit)
    assert owner_client.post(endpoint, json=proposal_body(proposal)).status_code == 200
    assert len(calls) == 1 and approvals.registry.role('reader')['revision'] == 1


@pytest.mark.parametrize('extra', [{'owner_confirmation': True}, {'signature': 'untrusted'},
    {'source': 'desktop'}, {'auto_approve': True}])
def test_request_body_cannot_supply_owner_authority(tmp_path, owner_client, extra):
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'role', role(), '独立核对')
    assert owner_client.post(f"/api/staff/proposals/{proposal['id']}/decision",
        json=proposal_body(proposal, **extra)).status_code == 422
    assert approvals.get(proposal['id'])['status'] == 'pending'


def test_proposal_workflow_without_owner_route_cannot_approve_even_with_exact_binding(tmp_path):
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'role', role(), '独立核对')
    with pytest.raises(ValueError, match='主人写入路由'):
        approvals.decide(proposal['id'], True, proposal['digest'],
            **{key: proposal[key] for key in ('nonce', 'round', 'instance')})
    assert approvals.get(proposal['id'])['status'] == 'pending'


def test_removed_key_endpoint_is_unavailable(tmp_path, owner_client):
    assert owner_client.post('/api/staff/approval-key', json={}).status_code == 404


def test_proposal_approval_requires_configured_owner_credential(tmp_path, owner_client, monkeypatch):
    approvals = StaffApprovals(str(tmp_path))
    proposal = approvals.propose('owner', 'role', role(), '独立核对')
    monkeypatch.setenv('VORTOONE_API_TOKEN', '')
    assert owner_client.post(f"/api/staff/proposals/{proposal['id']}/decision",
        json=proposal_body(proposal)).status_code == 403
    assert approvals.get(proposal['id'])['status'] == 'pending'


async def test_real_im_tool_confirmation_cannot_approve_registration(tmp_path):
    from src.gateway.agent_session import build_session
    from src.agents.taint import reset_taint
    reset_taint()
    async def confirm(message, kind):
        return True
    agent = build_session(str(tmp_path), kind='im', workspace_scope='general',
        task_owner='owner', confirm=confirm, can_ask_human=True, trust_level='full')
    await agent._run_tool('staff_propose', {'kind': 'role', 'data': role(), 'reason': '独立核对'}, 'plan', lambda _: None)
    assert 'reader' not in {r['id'] for r in StaffApprovals(str(tmp_path)).registry.roles()}
    assert all(p['status'] == 'pending' for p in StaffApprovals(str(tmp_path)).list())


@pytest.mark.parametrize('kind', ['own_data', 'execute'])
async def test_actions_require_owner_route_for_all_levels_and_run_once(tmp_path, monkeypatch, owner_client, kind):
    from tests.unit.test_staff_actions import live_staff, next_card
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch, kind=kind)
    monkeypatch.setattr('src.scheduling.staff_runner.get_staff_service', lambda _: service)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '等待主人确认'})
        _, card = await next_card(waiting)
        body = action_body(card)
        assert 'required_key' not in card['payload'] and 'key_id' not in card['payload']
        with pytest.raises(ValueError, match='主人写入路由'):
            service.actions.decide(task.id, card['payload']['id'], approve=True, expected_digest=card['digest'],
                **{key: card['payload'][key] for key in ('nonce', 'round', 'instance', 'runtime')})
        endpoint = f"/api/staff/actions/{task.id}/{card['payload']['id']}/decision"
        assert owner_client.post(endpoint, json=body, headers={'Authorization': 'Bearer device-fixture'}).status_code == 401
        assert not invoked
        assert owner_client.post(endpoint, json=body).json()['status'] == 'approved'
        await service.runner.join(task.id)
        assert len(invoked) == 1
        assert owner_client.post(endpoint, json=body).json()['replayed'] is True
        assert len(invoked) == 1
    finally:
        await service.runner.shutdown()


@pytest.mark.parametrize('field', ['digest', 'nonce', 'round', 'instance', 'runtime'])
async def test_owner_action_route_rejects_each_changed_binding(tmp_path, monkeypatch, owner_client, field):
    from tests.unit.test_staff_actions import live_staff, next_card
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch)
    monkeypatch.setattr('src.scheduling.staff_runner.get_staff_service', lambda _: service)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '核对完整动作'})
        _, card = await next_card(waiting)
        body = action_body(card, **{field: 99 if field == 'round' else 'changed'})
        assert owner_client.post(f"/api/staff/actions/{task.id}/{card['payload']['id']}/decision", json=body).status_code == 409
        assert not invoked and service.actions.view(task.id)['status'] == 'pending'
    finally:
        await service.runner.shutdown()


@pytest.mark.parametrize('restart', ['runtime', 'process'])
async def test_owner_route_cannot_resume_card_after_runtime_restart(tmp_path, monkeypatch, owner_client, restart):
    from src.workflow.staff.actions import StaffActions
    from tests.unit.test_staff_actions import live_staff, next_card
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch)
    monkeypatch.setattr('src.scheduling.staff_runner.get_staff_service', lambda _: service)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '等待主人核对'})
        _, card = await next_card(waiting)
        if restart == 'runtime':
            service.actions = StaffActions(service)
            service.actions.recover()
        else:
            monkeypatch.setattr('src.workflow.staff.owner_confirmation._PROCESS_INSTANCE', 'restarted-process')
        assert owner_client.post(f"/api/staff/actions/{task.id}/{card['payload']['id']}/decision",
            json=action_body(card)).status_code == 409
        assert service.actions.view(task.id)['status'] == 'interrupted' and not invoked
    finally:
        await service.runner.shutdown()


async def test_legacy_action_payload_stays_readable_but_cannot_resume(tmp_path, monkeypatch, owner_client):
    from src.workflow.staff.approvals import digest
    from src.workflow.staff.actions import StaffActions
    from src.workflow.tasks.tasks import TaskLedger
    from tests.unit.test_staff_actions import live_staff, next_card
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch)
    monkeypatch.setattr('src.scheduling.staff_runner.get_staff_service', lambda _: service)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '旧待批动作'})
        _, card = await next_card(waiting)
        saved = TaskLedger(str(tmp_path)).load(task.id)
        legacy = saved.collaboration['approval']
        legacy['payload'].pop('approval_level')
        legacy['payload'].update(required_key='approval', key_id='old-key', instance='old-instance')
        legacy['digest'] = digest(legacy['payload'])
        assert TaskLedger(str(tmp_path)).save(saved)
        service.actions = StaffActions(service)
        service.actions.recover()
        assert owner_client.get(f'/api/staff/actions/{task.id}').json()['status'] == 'interrupted'
        assert owner_client.post(f"/api/staff/actions/{task.id}/{card['payload']['id']}/decision",
            json=action_body(legacy)).status_code == 409
        assert not invoked
    finally:
        await service.runner.shutdown()
