"""S4: live coroutine, durable action identity, human decisions and pool isolation."""
import asyncio
from copy import deepcopy

import pytest

from src.agents.taint import reset_taint
from src.workflow.staff.approvals import StaffApprovals
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskLedger, TaskRunner


@pytest.fixture(autouse=True)
def clean_taint():
    reset_taint()
    yield
    reset_taint()


def live_staff(root, monkeypatch, *, kind='own_data', count=1, real_goal=False):
    from src.agents.staff import build_staff_member
    from src.agents.tool import PreparedToolCall, Tool
    import src.workflow.staff.registry as module
    prepared, invoked, results, starts = [], [], [], []
    async def prepare(args):
        frozen = deepcopy(args)
        prepared.append(frozen)
        async def apply():
            invoked.append(frozen)
            return '已执行当前动作'
        return PreparedToolCall(apply, '整理当前资料：' + frozen['text'], kind)
    catalog = module.tool_catalog
    if not real_goal:
        monkeypatch.setattr(module, 'tool_catalog', lambda root: {**catalog(root),
            'fixture_action': Tool.prepared('fixture_action', '离线动作夹具', {'text': '正文'}, prepare,
                operation_kind=kind, required_args=('text',))})
    registry = StaffRegistry(str(root))
    name = 'personal_goal_create' if real_goal else 'fixture_action'
    assigned = registry.register_role({'id': 'assistant', 'title': '整理岗位', 'brief': '只做所派任务',
        'grants': {'operations': ['read', kind], 'tools': [name], 'sees': ['goals'] if real_goal else [],
                   'web': False, 'project': False}, 'engine': ['vortocode'], 'max_steps': 12, 'timeout_seconds': 600})
    member = registry.register_member({'id': 'staff-assistant', 'name': '整理专员', 'role': assigned['id'], 'engine': 'vortocode'})
    pool = TaskRunner(str(root), None, max_concurrent=1)
    waiting = asyncio.Queue()
    notified = set()
    def update(task):
        card = task.collaboration.get('approval', {})
        if card.get('status') == 'pending' and card['payload']['id'] not in notified:
            notified.add(card['payload']['id'])
            waiting.put_nowait((task, deepcopy(card)))
    async def execute(task, work, role, person, check):
        starts.append((task.id, task.collaboration['round']))
        if task.prompt == '独立只读任务':
            return '只读完成'
        async def confirm(call):
            return await service.confirm_action(task, role, person, call)
        agent = build_staff_member(str(root), role, person, check=check, llm=object(), confirm_action=confirm)
        for number in range(count):
            args = {'objective': '整理学习计划', 'acceptance_criteria': ['列出书单'], 'next_action': '列出书单'} if real_goal else {'text': f'第 {number + 1} 步'}
            results.append(await agent._run_tool(name, args, 'build', lambda _: None))
        return '专员已核对动作结果'
    service = StaffService(str(root), pool, execute=execute, on_update=update)
    return service, member, waiting, prepared, invoked, results, starts


async def next_card(waiting):
    return await asyncio.wait_for(waiting.get(), 2)


def decide(service, task, card, approve=True, **kwargs):
    return service.actions.decide(task.id, card['payload']['id'], approve=approve,
        expected_digest=card['digest'], **kwargs)


@pytest.mark.parametrize('approve', [True, False])
async def test_actual_goal_action_waits_for_owner_and_only_approved_action_writes(tmp_path, monkeypatch, approve):
    from src.workflow.workbench.personal_goals import PersonalGoals
    service, member, waiting, _, _, results, starts = live_staff(tmp_path, monkeypatch, real_goal=True)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '整理学习计划'})
        _, card = await next_card(waiting)
        current = TaskLedger(tmp_path).load(task.id)
        assert current.status == 'blocked' and not PersonalGoals(str(tmp_path)).list()
        assert card['payload']['args']['objective'] == '整理学习计划'
        decide(service, task, card, approve, reason='' if approve else '先给我清单，不要保存')
        await service.runner.join(task.id)
        assert TaskLedger(tmp_path).load(task.id).status == 'done'
        assert len(PersonalGoals(str(tmp_path)).list()) == int(approve)
        assert starts == [(task.id, 1)]  # Same execution and round, no model/task replay.
        if not approve:
            assert '先给我清单，不要保存' in results[0]
    finally:
        await service.runner.shutdown()


async def test_waiting_releases_global_and_member_capacity_and_replays_never_run_twice(tmp_path, monkeypatch):
    service, member, waiting, prepared, invoked, _, _ = live_staff(tmp_path, monkeypatch)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '需审批的任务'})
        _, card = await next_card(waiting)
        independent = service.assign('owner', {'member': member['id'], 'prompt': '独立只读任务'})
        await asyncio.wait_for(service.runner.join(independent.id), 2)
        assert TaskLedger(tmp_path).load(independent.id).status == 'done'
        assert len(prepared) == 1 and not invoked
        assert decide(service, task, card)['replayed'] is False
        assert decide(service, task, card)['replayed'] is True
        await service.runner.join(task.id)
        assert invoked == prepared == [{'text': '第 1 步'}]
        assert decide(service, task, card)['replayed'] is True
        assert len(invoked) == 1
    finally:
        await service.runner.shutdown()


async def test_every_action_needs_new_card_and_previous_card_cannot_approve_next_step(tmp_path, monkeypatch):
    service, member, waiting, prepared, invoked, _, _ = live_staff(tmp_path, monkeypatch, count=2)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '两步任务'})
        _, first = await next_card(waiting)
        decide(service, task, first)
        _, second = await next_card(waiting)
        assert len(prepared) == 2 and len(invoked) == 1
        assert first['digest'] != second['digest']
        with pytest.raises(ValueError):
            decide(service, task, first)
        decide(service, task, second, False, reason='第二步先不做')
        await service.runner.join(task.id)
        assert len(invoked) == 1
        assert TaskLedger(tmp_path).load(task.id).collaboration['approval_history'][0]['id'] == first['payload']['id']
    finally:
        await service.runner.shutdown()


@pytest.mark.parametrize('change', ['digest', 'member', 'role', 'cancel', 'expired', 'payload'])
async def test_stale_cancelled_or_changed_action_cannot_execute(tmp_path, monkeypatch, change):
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '核对后执行'})
        _, card = await next_card(waiting)
        if change == 'digest':
            card['digest'] = '0' * 64
        elif change == 'member':
            service.registry.set_member_status(member['id'], 'paused', member['revision'])
        elif change == 'role':
            updated = service.registry.role('assistant'); updated['brief'] += '新版本'
            service.registry.register_role(updated)
        elif change == 'cancel':
            service.runner.cancel(task.id)
            await service.runner.join(task.id)
        elif change == 'expired':
            service.actions.clock = lambda: card['payload']['expires_at']
        else:
            ledger = TaskLedger(tmp_path); current = ledger.load(task.id)
            current.collaboration['approval']['payload']['args']['text'] = '替换动作'
            assert ledger.save(current)
        with pytest.raises(ValueError):
            decide(service, task, card)
        assert not invoked
    finally:
        await service.runner.shutdown()


@pytest.mark.parametrize('failure', ['audit', 'store'])
async def test_decision_persistence_failure_keeps_action_waiting(tmp_path, monkeypatch, failure):
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '核对后执行'})
        _, card = await next_card(waiting)
        with monkeypatch.context() as patch:
            if failure == 'audit':
                patch.setattr('src.gateway.audit.append_audit', lambda *args, **kwargs: False)
            else:
                patch.setattr(TaskLedger, 'save', lambda *args: False)
            with pytest.raises(OSError):
                decide(service, task, card)
        assert service.actions.view(task.id)['status'] == 'pending'
        assert not invoked
        decide(service, task, card)
        await service.runner.join(task.id)
        assert len(invoked) == 1
    finally:
        await service.runner.shutdown()


async def test_restart_and_followup_invalidate_old_card_without_replaying_an_action(tmp_path, monkeypatch):
    from src.workflow.staff.actions import StaffActions
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '核对后执行'})
        _, card = await next_card(waiting)
        # A newly started host has no Python continuation or authority for the old card.
        recovered = StaffActions(service)
        assert recovered.view(task.id)['status'] == 'interrupted'
        recovered.recover()
        with pytest.raises(ValueError):
            recovered.decide(task.id, card['payload']['id'], approve=True, expected_digest=card['digest'])
        assert TaskLedger(tmp_path).load(task.id).status == 'interrupted' and not invoked
        service.runner.cancel(task.id); await service.runner.join(task.id)
        service.followup('owner', task.id, 1, '已核对，请重新检查当前状态')
        _, fresh = await next_card(waiting)
        assert fresh['payload']['round'] == 2
        with pytest.raises(ValueError):
            decide(service, task, card)
        decide(service, task, fresh)
        await service.runner.join(task.id)
        assert len(invoked) == 1
    finally:
        await service.runner.shutdown()


async def test_high_risk_signature_binds_instance_key_task_round_and_full_action(tmp_path, monkeypatch):
    from src.workflow.staff.actions import action_message
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch, kind='execute')
    approvals = StaffApprovals(str(tmp_path))
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint).hex()
    sign = lambda data: private.sign(data, ec.ECDSA(hashes.SHA256())).hex()
    approvals.bind_key(public, sign(approvals.enrollment_message(public)), authorization=approvals.authorize_enrollment())
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '需要批准钥匙的动作'})
        _, card = await next_card(waiting)
        assert card['payload']['required_key'] == 'approval'
        for signature in [None, '00', sign(approvals.enrollment_message(public))]:
            with pytest.raises(ValueError):
                decide(service, task, card, signature=signature)
        for field in ['instance', 'key_id', 'task_id', 'round', 'nonce']:
            changed = deepcopy(card); changed['payload'][field] = '另一个身份'
            with pytest.raises(ValueError):
                decide(service, task, card, signature=sign(action_message(changed)))
        assert not invoked
        decide(service, task, card, signature=sign(action_message(card)))
        await service.runner.join(task.id)
        assert len(invoked) == 1
    finally:
        await service.runner.shutdown()


async def test_permission_change_while_approved_action_reacquires_capacity_prevents_execution(tmp_path, monkeypatch):
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.execute
    async def execute(task, *args):
        if task.prompt == '独立只读任务':
            entered.set()
            await release.wait()
        return await original(task, *args)
    service.execute = execute
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '等待批准'})
        _, card = await next_card(waiting)
        other = service.assign('owner', {'member': member['id'], 'prompt': '独立只读任务'})
        await asyncio.wait_for(entered.wait(), 2)
        decide(service, task, card)
        service.registry.set_member_status(member['id'], 'paused', member['revision'])
        release.set()
        await service.runner.join(other.id)
        await service.runner.join(task.id)
        assert not invoked and TaskLedger(tmp_path).load(task.id).status == 'failed'
    finally:
        release.set()
        await service.runner.shutdown()


async def test_waiting_does_not_spend_the_registered_execution_budget(monkeypatch):
    from src.workflow.staff.service import _ActiveBudget
    from unittest.mock import Mock
    clock = Mock()
    clock.time.return_value = 10
    handles = []
    def schedule(seconds, callback):
        timer = Mock(); handles.append((seconds, timer, callback)); return timer
    clock.call_later.side_effect = schedule
    monkeypatch.setattr(asyncio, 'get_running_loop', lambda: clock)
    budget = _ActiveBudget('task-budget', 60)
    budget.resume()
    clock.time.return_value = 25
    budget.pause()
    handles[0][1].cancel.assert_called_once()
    clock.time.return_value = 1025  # Owner wait and pool reacquisition are outside active execution.
    budget.resume()
    assert [entry[0] for entry in handles] == [60, 45]
    budget.pause()


async def test_action_routes_authenticate_owner_and_do_not_accept_model_authority(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.server import app
    service, member, waiting, _, invoked, _, _ = live_staff(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VORTOONE_API_TOKEN', 'offline-owner-fixture')
    monkeypatch.setattr('src.scheduling.staff_runner.get_staff_service', lambda _: service)
    client = TestClient(app)
    headers = {'Authorization': 'Bearer offline-owner-fixture'}
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '等待主人'})
        _, card = await next_card(waiting)
        path = f"/api/staff/actions/{task.id}"
        endpoint = f"{path}/{card['payload']['id']}/decision"
        body = {'approve': True, 'digest': card['digest']}
        assert client.get(path).status_code == 401
        assert client.post(endpoint, json=body, headers={'Authorization': 'Bearer device-fixture'}).status_code == 401
        assert client.get(path, headers=headers).json()['digest'] == card['digest']
        assert client.post(endpoint, headers=headers, json={**body, 'auto_approve': True}).status_code == 422
        assert client.post(endpoint, headers=headers, json={**body, 'approve': 'yes'}).status_code == 422
        assert client.post(endpoint, headers=headers, json={**body, 'approve': False}).status_code == 409
        monkeypatch.delenv('VORTOONE_API_TOKEN')
        assert client.post(endpoint, json=body).status_code == 403
        monkeypatch.setenv('VORTOONE_API_TOKEN', 'offline-owner-fixture')
        assert not invoked
        assert client.post(endpoint, headers=headers, json=body).json()['status'] == 'approved'
        await service.runner.join(task.id)
        assert len(invoked) == 1
    finally:
        await service.runner.shutdown()


async def test_approval_attention_and_notice_use_one_current_action_identity(tmp_path, monkeypatch):
    from src.workflow.tasks.decisions import build_decision_queue
    from src.scheduling.task_notices import message, notify_transition
    service, member, waiting, _, _, _, _ = live_staff(tmp_path, monkeypatch, count=2)
    sent = []
    async def notify(text): sent.append(text)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '分步核对'})
        for step in range(2):
            current, card = await next_card(waiting)
            decisions = build_decision_queue(tasks=[current], dismissed={f"task:{task.id}:approval:{card['payload']['id']}"})
            assert len(decisions) == 1 and not decisions[0]['can_dismiss']
            assert decisions[0]['title'] == '专员等待你批准这一步'
            assert '批准' in message(current) and '第' not in message(current)  # No action body in push.
            assert notify_transition(str(tmp_path), current, notify=notify)
            assert not notify_transition(str(tmp_path), current, notify=notify)
            decide(service, task, card)
        await service.runner.join(task.id)
        assert len(sent) == 2
    finally:
        await service.runner.shutdown()


async def test_real_mail_drafts_require_separate_cards_and_never_save_before_approval(tmp_path, monkeypatch):
    from src.agents.staff import build_staff_member
    from src.plugins import mail
    saved = []
    monkeypatch.setattr(mail, 'save_draft', lambda draft: saved.append(deepcopy(draft)) or '离线草稿箱')
    registry = StaffRegistry(str(tmp_path))
    role = registry.register_role({'id': 'drafts', 'title': '邮件草稿', 'brief': '只起草，不发送',
        'grants': {'operations': ['read', 'draft'], 'tools': ['mail_draft'], 'sees': ['plugin:mail'],
                   'web': False, 'project': False}, 'engine': ['vortocode'], 'max_steps': 12, 'timeout_seconds': 600})
    member = registry.register_member({'id': 'staff-drafts', 'name': '草稿专员', 'role': role['id'], 'engine': 'vortocode'})
    waiting = asyncio.Queue()
    def update(task):
        card = task.collaboration.get('approval', {})
        if card.get('status') == 'pending': waiting.put_nowait((task, deepcopy(card)))
    pool = TaskRunner(str(tmp_path), None)
    async def execute(task, work, role, member, check):
        async def confirm(call): return await service.confirm_action(task, role, member, call)
        agent = build_staff_member(str(tmp_path), role, member, check=check, llm=object(), confirm_action=confirm)
        for number in range(2):
            await agent._run_tool('mail_draft', {'to': 'reader@example.com', 'subject': '离线草稿',
                'body': '完整草稿正文' + str(number)}, 'build', lambda _: None)
        return '草稿状态已核对'
    service = StaffService(str(tmp_path), pool, execute=execute, on_update=update)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '准备两份邮件草稿'})
        _, first = await next_card(waiting)
        assert not saved and '完整草稿正文0' in first['payload']['message']
        decide(service, task, first)
        _, second = await next_card(waiting)
        assert len(saved) == 1 and second['payload']['tainted']
        decide(service, task, second, False, reason='第二封不存')
        await pool.join(task.id)
        assert len(saved) == 1 and saved[0]['body'] == '完整草稿正文0'
    finally:
        await pool.shutdown()
