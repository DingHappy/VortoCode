"""L3: one owner decision covers a new, indivisible set of registrations."""
from copy import deepcopy
import json

import pytest

from src.workflow.staff.approvals import StaffApprovals, digest
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.routines import Routines
from tests.unit.staff_owner_helpers import owner_decide
from tests.unit.test_staff_contract import role


def bundle():
    return {'role': role(),
        'member': {'id': 'staff-reader', 'name': '阅读专员', 'role': 'reader', 'engine': 'vortocode'},
        'department': {'id': 'dept-reading', 'name': '资料核对', 'duty': '核对资料', 'members': ['staff-reader'], 'lead': ''},
        'routines': [{'id': 'routine-reading', 'name': '每日核对', 'member': 'staff-reader', 'prompt': '核对公开数字',
            'schedule': 'at 09:00', 'timezone': 'UTC', 'delivery': 'notify'}]}


def test_single_owner_decision_registers_the_complete_bundle_with_final_bindings(tmp_path):
    approvals = StaffApprovals(str(tmp_path))
    item = approvals.propose('owner', 'bundle', bundle(), '每天核对')
    registry = StaffRegistry(str(tmp_path))
    assert not registry.members() and not registry.departments() and not Routines(str(tmp_path)).list()
    with pytest.raises(ValueError, match='主人'):
        approvals.decide(item['id'], True, item['digest'])
    approved = owner_decide(approvals, item['id'], True, item['digest'])
    assert approved['status'] == 'approved'
    member = registry.member('staff-reader')
    assert member['department'] == 'dept-reading'
    routine = Routines(str(tmp_path)).get('routine-reading')
    assert routine['binding']['member'] == member
    assert routine['binding']['role'] == registry.role('reader')
    assert owner_decide(approvals, item['id'], True, item['digest']) == approved


@pytest.mark.parametrize('bad', ['permission', 'routine', 'too_many', 'existing', 'empty'])
def test_invalid_or_existing_registration_rejects_the_entire_bundle(tmp_path, bad):
    data = bundle(); registry = StaffRegistry(str(tmp_path)); before = registry.roles()
    if bad == 'permission': data['role']['grants']['operations'] = ['deliver']
    elif bad == 'routine': data['routines'][0]['schedule'] = 'invalid schedule'
    elif bad == 'too_many': data['routines'] *= 5
    elif bad == 'empty': data = {}
    else: registry.register_role(data['role'])
    with pytest.raises(ValueError):
        StaffApprovals(str(tmp_path)).propose('owner', 'bundle', data, '核对')
    assert registry.members() == [] and Routines(str(tmp_path)).list() == []
    if bad != 'existing': assert registry.roles() == before


@pytest.mark.parametrize('complete', [True, False])
def test_interrupted_bundle_never_exposes_partial_authority_and_can_finish_or_cancel(tmp_path, monkeypatch, complete):
    approvals = StaffApprovals(str(tmp_path))
    item = approvals.propose('owner', 'bundle', bundle(), '核对')
    original = StaffRegistry._write
    def fail(registry, name, value):
        if name == 'members.json': raise OSError('fixture interrupted registration')
        return original(registry, name, value)
    with monkeypatch.context() as patch:
        patch.setattr(StaffRegistry, '_write', fail)
        with pytest.raises(OSError): owner_decide(approvals, item['id'], True, item['digest'])
    assert approvals.get(item['id'])['status'] == 'applying'
    # Fresh readers model another service after a process restart.
    registry = StaffRegistry(str(tmp_path))
    assert 'reader' not in {x['id'] for x in registry.roles()}
    assert registry.members() == registry.departments() == Routines(str(tmp_path)).list() == []
    result = owner_decide(StaffApprovals(str(tmp_path)), item['id'], complete, item['digest'])
    assert result['status'] == ('approved' if complete else 'rejected')
    assert len(registry.members()) == int(complete)
    assert ('reader' in {x['id'] for x in registry.roles()}) is complete


def test_every_entry_is_part_of_the_digest_and_model_fields_cannot_approve(tmp_path):
    approvals = StaffApprovals(str(tmp_path)); data = bundle()
    item = approvals.propose('owner', 'bundle', data, '核对')
    data['routines'][0]['timezone'] = 'Asia/Shanghai'
    assert item['snapshot']['data']['routines'][0]['timezone'] == 'UTC'
    forged = deepcopy(item); forged['snapshot']['data']['routines'][0]['timezone'] = 'Asia/Shanghai'
    assert digest({k: v for k, v in forged.items() if k not in {'digest', 'instance'}}) != item['digest']
    with pytest.raises(ValueError, match='变化'):
        owner_decide(approvals, item['id'], True, 'different-digest')
    assert StaffRegistry(str(tmp_path)).members() == []


@pytest.mark.parametrize('specialist', ['calendar', 'mail', 'research'])
async def test_actual_plan_tool_returns_a_complete_bounded_bundle_without_writes(tmp_path, specialist):
    from src.agents.staff import build_staff_tools
    from src.agents.main_agent import MainAgent
    from src.agents.gate import make_confirm_gate
    from tests.unit.test_standing_staff import request
    args = request(specialist)
    if specialist == 'research': args['topic'] = '公开资料' * 125
    agent = MainAgent(build_staff_tools(str(tmp_path), owner='owner', confirm=make_confirm_gate()), auto_recall=False)
    output = await agent._run_tool('staff_plan', args, 'plan', lambda _: None)
    data = json.loads(output)['bundle']
    snapshot = StaffApprovals(str(tmp_path)).snapshot('bundle', data['data'])
    assert len(snapshot['entries']) == (5 if specialist == 'mail' else 4)
    assert len(output) <= 4000
    assert not (tmp_path / '.vortoone/staff').exists()


@pytest.mark.parametrize('file', ['roles.json', 'members.json', 'departments.json', 'routines.json', 'commit'])
def test_every_write_boundary_keeps_all_new_rows_hidden_until_the_commit(tmp_path, monkeypatch, file):
    approvals = StaffApprovals(str(tmp_path)); data = bundle()
    proposal = approvals.propose('owner', 'bundle', data, '核对')
    original = StaffRegistry._write
    def fail(registry, name, value):
        if name == file or file == 'commit' and name == 'proposals.json' and any(row['status'] == 'approved' for row in value['proposals']):
            raise OSError('fixture write interruption')
        return original(registry, name, value)
    with monkeypatch.context() as patch:
        patch.setattr(StaffRegistry, '_write', fail)
        with pytest.raises(OSError): owner_decide(approvals, proposal['id'], True, proposal['digest'])
    registry = StaffRegistry(str(tmp_path))
    assert 'reader' not in {row['id'] for row in registry.roles()}
    assert registry.members() == registry.departments() == Routines(str(tmp_path)).list() == []
    assert owner_decide(approvals, proposal['id'], True, proposal['digest'])['status'] == 'approved'


def test_cancel_can_resume_after_interruption_and_cannot_switch_back_to_approval(tmp_path, monkeypatch):
    approvals = StaffApprovals(str(tmp_path)); item = approvals.propose('owner', 'bundle', bundle(), '核对')
    original = StaffRegistry._write
    def stop_commit(registry, name, value):
        if name == 'proposals.json' and any(row['status'] == 'approved' for row in value['proposals']):
            raise OSError('fixture commit failure')
        return original(registry, name, value)
    with monkeypatch.context() as patch:
        patch.setattr(StaffRegistry, '_write', stop_commit)
        with pytest.raises(OSError): owner_decide(approvals, item['id'], True, item['digest'])
    def stop_cancel(registry, name, value):
        if name == 'members.json': raise OSError('fixture cancellation failure')
        return original(registry, name, value)
    with monkeypatch.context() as patch:
        patch.setattr(StaffRegistry, '_write', stop_cancel)
        with pytest.raises(OSError): owner_decide(approvals, item['id'], False, item['digest'])
    assert approvals.get(item['id'])['status'] == 'cancelling'
    assert StaffRegistry(str(tmp_path)).members() == []
    with pytest.raises(ValueError, match='已经处理'):
        owner_decide(approvals, item['id'], True, item['digest'])
    assert owner_decide(approvals, item['id'], False, item['digest'])['status'] == 'rejected'


def test_unrelated_writes_continue_without_erasing_or_exposing_partial_bundle_rows(tmp_path, monkeypatch):
    approvals = StaffApprovals(str(tmp_path)); item = approvals.propose('owner', 'bundle', bundle(), '核对')
    original = StaffRegistry._write
    def stop(registry, name, value):
        if name == 'members.json': raise OSError('fixture interruption')
        return original(registry, name, value)
    with monkeypatch.context() as patch:
        patch.setattr(StaffRegistry, '_write', stop)
        with pytest.raises(OSError): owner_decide(approvals, item['id'], True, item['digest'])
    registry = StaffRegistry(str(tmp_path))
    registry.set_role_enabled('reviewer', False)
    assert not registry.role('reviewer')['enabled']
    with pytest.raises(ValueError, match='开通单'):
        registry.register_role(role())
    owner_decide(approvals, item['id'], True, item['digest'])
    assert registry.role('reader')['enabled'] and not registry.role('reviewer')['enabled']


def test_bundle_owner_http_uses_the_original_confirmation_binding(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.server import app
    from tests.unit.staff_owner_helpers import proposal_body
    monkeypatch.chdir(tmp_path); monkeypatch.setenv('VORTOONE_API_TOKEN', 'fixture-owner')
    item = StaffApprovals(str(tmp_path)).propose('owner', 'bundle', bundle(), '核对')
    client = TestClient(app); endpoint = f"/api/staff/proposals/{item['id']}/decision"
    assert client.post(endpoint, json=proposal_body(item)).status_code == 401
    headers = {'Authorization': 'Bearer fixture-owner'}
    assert client.post(endpoint, headers=headers, json=proposal_body(item, nonce='changed')).status_code == 409
    assert client.post(endpoint, headers=headers, json=proposal_body(item)).json()['status'] == 'approved'


async def test_proposal_tool_does_not_deliver_referenced_notes_into_a_clean_turn(tmp_path, monkeypatch):
    from src.agents.staff_proposals import build_staff_proposal_tool
    from src.agents.gate import make_confirm_gate
    from src.agents.taint import reset_taint
    from src.workflow.staff.notes import StaffNotes
    reset_taint()
    original = StaffNotes.snapshot
    def notes(self, department):
        value = original(self, department)
        return {**value, 'text': '外部资料只在主人审批页核对', 'tainted': True}
    monkeypatch.setattr(StaffNotes, 'snapshot', notes)
    async def yes(*args): return True
    tool = build_staff_proposal_tool(str(tmp_path), owner='owner', confirm=make_confirm_gate(yes, can_ask_human=True))
    result = await tool.handler({'kind': 'bundle', 'data': bundle(), 'reason': '核对'})
    assert '外部资料只在主人审批页核对' not in result
    item = json.loads(result)['proposal']
    assert item['kind'] == 'bundle' and item['status'] == 'pending'
    snapshot = StaffApprovals(str(tmp_path)).get(item['id'])['snapshot']
    assert any(entry.get('notes_snapshot', {}).get('text') == '外部资料只在主人审批页核对' for entry in snapshot['entries'])
    assert StaffRegistry(str(tmp_path)).members() == []
