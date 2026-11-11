"""Deferred human decisions on the existing collaboration task; no executable replay log."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import time
import uuid

from src.agents.confirmation_context import approval_details
from src.workflow.staff.approvals import StaffApprovals, digest
from src.workflow.tasks.tasks import TASK_STATE_LOCK, TaskLedger

ACTION_TTL = 24 * 60 * 60


def action_message(card):
    p = card['payload']
    return (f"vortoone-staff-action-v1\n{p['instance']}\n{p['key_id']}\n{p['task_id']}\n"
            f"{p['round']}\n{p['id']}\n{card['digest']}\n{p['nonce']}").encode()


class StaffActions:
    def __init__(self, service, *, clock=time.time, timeout=ACTION_TTL):
        self.service, self.clock, self.timeout = service, clock, timeout
        self.runtime = uuid.uuid4().hex
        self.waiters = {}

    def _task(self, task_id):
        task = TaskLedger(self.service.root).load(task_id)
        if task is None or task.collaboration.get('dispatch', {}).get('source') != 'staff':
            raise ValueError('专员任务不存在')
        return task

    @staticmethod
    def _card(task):
        card = task.collaboration.get('approval')
        if not isinstance(card, dict) or not isinstance(card.get('payload'), dict):
            raise ValueError('审批卡不存在')
        if card.get('digest') != digest(card['payload']):
            raise ValueError('审批卡内容损坏，未授权')
        p = card['payload']
        if p.get('task_id') != task.id or p.get('round') != task.collaboration.get('round'):
            raise ValueError('审批卡任务或轮次已变化')
        return card

    def view(self, task_id):
        with TASK_STATE_LOCK:
            task = self._task(task_id)
            card = deepcopy(self._card(task))
            p = card['payload']
            if card['status'] == 'pending' and (p['runtime'] != self.runtime or p['id'] not in self.waiters):
                card['status'] = 'interrupted'
            elif card['status'] == 'pending' and p['expires_at'] <= self.clock():
                card['status'] = 'expired'
            return card

    async def request(self, task, role, member, call, budget):
        """Only the running tool adapter supplies call metadata; model arguments cannot do so."""
        self.service.check(member, role)
        if (not isinstance(call, dict) or set(call) != {'tool', 'args', 'kind', 'message'}
                or call['tool'] not in role['grants']['tools'] or call['kind'] not in role['grants']['operations']):
            raise ValueError('审批动作不在登记岗位权限内')
        if len(json.dumps(call, ensure_ascii=False).encode()) > 32 * 1024:
            raise ValueError('审批内容过长，未执行或静默截断')
        from src.agents.taint import is_tainted
        identity = StaffApprovals(self.service.root).key_info()
        now = self.clock()
        p = {'id': 'staff-action-' + uuid.uuid4().hex, 'task_id': task.id,
             'round': task.collaboration['round'], 'member': member['id'], 'member_name': member['name'], 'member_revision': member['revision'],
             'role': role['id'], 'role_title': role['title'], 'role_revision': role['revision'], 'runtime': self.runtime,
             'instance': identity['instance'], 'key_id': (identity.get('key') or {}).get('id', ''),
             'nonce': uuid.uuid4().hex, 'created': datetime.fromtimestamp(now, timezone.utc).isoformat(),
             'expires_at': now + self.timeout, 'tainted': is_tainted(), **deepcopy(call),
             **approval_details(call['kind'])}
        card = {'payload': p, 'digest': digest(p), 'status': 'pending', 'reason': '', 'decision': None}
        work = self.service.collaboration(task.owner_session)
        future = asyncio.get_running_loop().create_future()
        self.waiters[p['id']] = future

        def stage(current):
            if current.status != 'running' or (current.collaboration.get('approval') or {}).get('status') == 'pending':
                raise ValueError('只有当前运行中的专员任务能提出一个审批动作')
            previous = current.collaboration.get('approval')
            if previous:
                receipt = {key: previous.get(key) for key in ('digest', 'status', 'reason', 'decision', 'decided_at')}
                receipt.update({key: previous['payload'][key] for key in ('id', 'round', 'tool', 'kind', 'tainted')})
                receipt['reason'] = str(receipt.get('reason') or '')[:240]
                current.collaboration['approval_history'] = [*current.collaboration.get('approval_history', []), receipt][-32:]
            current.collaboration['approval'] = card
            current.collaboration['tainted'] = current.collaboration.get('tainted', False) or p['tainted']
            current.status = 'blocked'

        try:
            budget.pause()
            async with self.service.runner.waiting_for_owner(task.id):
                work.update_staff_action(task.id, p['round'], stage)
                answer = await asyncio.wait_for(asyncio.shield(future), self.timeout)
            self.service.check(member, role)
            def resume(current):
                active = self._card(current)
                if current.status != 'blocked' or active['payload']['id'] != p['id'] or active['status'] not in {'approved', 'denied'}:
                    raise ValueError('审批卡已经失效，未执行')
                current.status = 'running'
            work.update_staff_action(task.id, p['round'], resume)
            budget.resume()
            if not answer['approved']:
                from src.agents.tool_runtime import record_tool_rejection
                record_tool_rejection(answer['reason'])
            return answer['approved']
        except BaseException as error:
            def close(current, error=error):
                active = current.collaboration.get('approval') or {}
                if active.get('payload', {}).get('id') == p['id'] and active.get('status') == 'pending':
                    active['status'] = 'expired' if isinstance(error, (TimeoutError, asyncio.TimeoutError)) else 'cancelled'
            try:
                work.update_staff_action(task.id, p['round'], close)
            except Exception:
                pass  # Lost storage/round identity cannot restore the Python continuation.
            if isinstance(error, asyncio.CancelledError):
                raise
            from src.agents.tool import ToolTurnYield
            raise ToolTurnYield('审批等待中断，未继续执行；请核对任务记录。') from error
        finally:
            budget.resume()
            self.waiters.pop(p['id'], None)
            if not future.done():
                future.cancel()

    def decide(self, task_id, action_id, *, approve, expected_digest, reason='', signature=None):
        if type(approve) is not bool or not isinstance(reason, str) or len(reason) > 2000 or (not approve and not reason.strip()):
            raise ValueError('拒绝需要明确理由；批准必须为布尔值')
        with TASK_STATE_LOCK:
            task = self._task(task_id)
            card = self._card(task)
            p = card['payload']
            if p['id'] != action_id or card['digest'] != expected_digest:
                raise ValueError('审批动作或摘要已变化')
            desired = 'approved' if approve else 'denied'
            if card['status'] == desired and card['reason'] == reason:
                return {'status': desired, 'replayed': True}
            future = self.waiters.get(action_id)
            if (card['status'] != 'pending' or task.status != 'blocked' or p['runtime'] != self.runtime
                    or future is None or future.done() or p['expires_at'] <= self.clock()):
                raise ValueError('审批卡已中断、处理或过期，不能执行旧动作')
            if approve:
                member, role = self.service.assignment({'member': p['member'], 'prompt': task.prompt})
                self.service.check(member, role)
                if member['revision'] != p['member_revision'] or role['revision'] != p['role_revision']:
                    raise ValueError('岗位或专员权限已变化，旧卡不能批准')
            if approve and p['required_key'] == 'approval':
                approvals = StaffApprovals(self.service.root)
                identity = approvals.key_info()
                if identity['instance'] != p['instance'] or (identity.get('key') or {}).get('id') != p['key_id']:
                    raise ValueError('批准钥匙已变化，旧卡不能批准')
                approvals.verify_action(action_message(card), signature)
            from src.gateway.audit import append_audit
            if not append_audit(self.service.root, {'category': 'decision', 'operation': '专员审批：' + p['tool'],
                    'decision': desired, 'operation_kind': p['kind'], 'approval_scope': 'once',
                    'decision_source': 'user', 'approval_outcome': desired, 'tainted': p['tainted'],
                    'staff_task': task.id, 'staff_action': action_id, 'action_digest': card['digest']}, durable=True):
                raise OSError('审批审计保存失败，未授权执行')
            work = self.service.collaboration(task.owner_session)
            def save(current):
                active = self._card(current)
                if active['status'] != 'pending':
                    raise ValueError('审批已经处理')
                active.update(status=desired, reason=reason, decision=approve,
                              decided_at=datetime.fromtimestamp(self.clock(), timezone.utc).isoformat())
            work.update_staff_action(task.id, p['round'], save)
            def deliver():
                if not future.done():
                    future.set_result({'approved': approve, 'reason': reason})
            future.get_loop().call_soon_threadsafe(deliver)
            return {'status': desired, 'replayed': False}

    def recover(self):
        """A process cannot recover a Python continuation; never replay an old approved action."""
        for task in TaskLedger(self.service.root).list():
            if task.collaboration.get('dispatch', {}).get('source') != 'staff':
                continue
            card = task.collaboration.get('approval') or {}
            if task.status not in {'blocked', 'running', 'interrupted'} or card.get('status') not in {'pending', 'approved', 'denied'}:
                continue
            if card.get('payload', {}).get('runtime') == self.runtime:
                continue
            work = self.service.collaboration(task.owner_session)
            def interrupt(current):
                current.collaboration['approval']['status'] = 'interrupted'
                current.status = 'interrupted'
                current.error = '等待审批的执行已中断；先核对历史和可能已发生的动作，旧卡不恢复执行。'
            work.update_staff_action(task.id, task.collaboration['round'], interrupt)
