"""Deferred human decisions on the existing collaboration task; no executable replay log."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import time
import uuid

from src.agents.confirmation_context import approval_details
from src.workflow.staff.approvals import digest
from src.workflow.staff.owner_confirmation import owner_instance, require_owner_route
from src.workflow.tasks.tasks import TASK_STATE_LOCK, TaskLedger

ACTION_TTL = 24 * 60 * 60


class StaffActions:
    def __init__(self, service, *, clock=time.time, timeout=ACTION_TTL):
        self.service, self.clock, self.timeout = service, clock, timeout
        self.runtime = uuid.uuid4().hex
        self.waiters = {}

    def _task(self, task_id):
        task = TaskLedger(self.service.root).load(task_id)
        from src.workflow.staff.team_roster import is_managed
        if task is None or not is_managed(task):
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
            if card['status'] == 'pending' and (p['runtime'] != self.runtime or p['instance'] != owner_instance(self.service.root)
                                               or p['id'] not in self.waiters):
                card['status'] = 'interrupted'
            elif card['status'] == 'pending' and p['expires_at'] <= self.clock():
                card['status'] = 'expired'
            return card

    @staticmethod
    def _mobile_category(card):
        p = card['payload']
        return (isinstance(p.get('kind'), str) and p['kind'] in {'read', 'own_data', 'draft'} and p.get('risk') == 'normal'
                and p.get('approval_level') == 'normal')

    def _mobile_view(self, card):
        p = card['payload']
        fields = ('id', 'task_id', 'round', 'member_name', 'role_title', 'tool', 'kind', 'message',
                  'tainted', 'nonce', 'instance', 'runtime', 'expires_at', 'risk', 'approval_level')
        return {**{key: p[key] for key in fields}, 'digest': card['digest'], 'status': card['status'],
                'args_text': json.dumps(p['args'], ensure_ascii=False, sort_keys=True, indent=2),
                'can_decide': card['status'] == 'pending' and self._mobile_category(card)}

    def mobile_snapshot(self, *, task_id=None, after=''):
        """Bounded read projection; no Agent, continuation or new approval is created."""
        from src.utils.ids import typed_id
        from src.workflow.staff.team_roster import is_managed
        if (task_id is not None and not typed_id(task_id, 'task') or not isinstance(after, str)
                or after and not typed_id(after, 'task')):
            raise ValueError('专员任务编号无效')
        with TASK_STATE_LOCK:
            if task_id is not None:
                return {'cards': [self._mobile_view(self.view(task_id))], 'next': None, 'source_complete': True}
            scan = TaskLedger(self.service.root).scan()
            tasks = sorted((task for task in scan.tasks if is_managed(task)
                            and (task.collaboration.get('approval') or {}).get('status') == 'pending'
                            and task.id > after), key=lambda task: task.id)
            cards, size, complete, cursor, more = [], 0, scan.complete, after, False
            for task in tasks:
                try:
                    row = self._mobile_view(self.view(task.id))
                    count = len(json.dumps(row, ensure_ascii=False).encode())
                    if count > 256 * 1024:
                        raise ValueError('审批内容超过手机读取上限，须在电脑核对')
                except (ValueError, KeyError, TypeError):
                    complete, cursor = False, task.id
                    continue
                if cards and (len(cards) == 8 or size + count > 256 * 1024):
                    more = True
                    break
                cards.append(row); size += count; cursor = task.id
            return {'cards': cards, 'next': cursor if more else None, 'source_complete': complete}

    async def request(self, task, role, member, call, budget):
        """Only the running tool adapter supplies call metadata; model arguments cannot do so."""
        self.service.check(member, role)
        if (not isinstance(call, dict) or set(call) != {'tool', 'args', 'kind', 'message'}
                or call['tool'] not in role['grants']['tools'] or call['kind'] not in role['grants']['operations']):
            raise ValueError('审批动作不在登记岗位权限内')
        if len(json.dumps(call, ensure_ascii=False).encode()) > 32 * 1024:
            raise ValueError('审批内容过长，未执行或静默截断')
        from src.agents.taint import is_tainted
        now = self.clock()
        details = approval_details(call['kind'])
        details.pop('required_key')
        details['approval_level'] = 'normal' if details['risk'] == 'normal' else 'desktop'
        p = {'id': 'staff-action-' + uuid.uuid4().hex, 'task_id': task.id,
             'round': task.collaboration['round'], 'member': member['id'], 'member_name': member['name'], 'member_revision': member['revision'],
             'role': role['id'], 'role_title': role['title'], 'role_revision': role['revision'], 'runtime': self.runtime,
             'instance': owner_instance(self.service.root),
             'nonce': uuid.uuid4().hex, 'created': datetime.fromtimestamp(now, timezone.utc).isoformat(),
             'expires_at': now + self.timeout, 'tainted': is_tainted(), **deepcopy(call),
             **details}
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

    def decide(self, task_id, action_id, *, approve, expected_digest, reason='', nonce=None, round=None,
               instance=None, runtime=None):
        require_owner_route(self.service.root)
        return self._decide(task_id, action_id, approve=approve, expected_digest=expected_digest, reason=reason,
                            nonce=nonce, round=round, instance=instance, runtime=runtime)

    def decide_mobile(self, body):
        from src.workflow.staff.owner_confirmation import require_signed_staff_action
        require_signed_staff_action(self.service.root, body)
        required = {'task_id', 'action_id', 'approve', 'digest', 'reason', 'nonce', 'round', 'instance', 'runtime'}
        if not isinstance(body, dict) or set(body) != required:
            raise ValueError('手机只允许本次具体决定，不能附带同类放行或权限字段')
        with TASK_STATE_LOCK:
            card = self._card(self._task(body['task_id']))
            if not self._mobile_category(card):
                raise ValueError('这张卡需要在电脑上批准，手机仅可查看')
            result = self._decide(body['task_id'], body['action_id'], approve=body['approve'], expected_digest=body['digest'],
                reason=body['reason'], nonce=body['nonce'], round=body['round'], instance=body['instance'], runtime=body['runtime'])
            return {**{key: body[key] for key in required - {'approve'}}, **result}

    def _decide(self, task_id, action_id, *, approve, expected_digest, reason='', nonce=None, round=None,
                instance=None, runtime=None):
        if type(approve) is not bool or not isinstance(reason, str) or len(reason) > 2000 or (not approve and not reason.strip()):
            raise ValueError('拒绝需要明确理由；批准必须为布尔值')
        with TASK_STATE_LOCK:
            task = self._task(task_id)
            card = self._card(task)
            p = card['payload']
            if (nonce != p['nonce'] or type(round) is not int or round != p['round']
                    or instance != p['instance'] or instance != owner_instance(self.service.root)
                    or runtime != p['runtime'] or runtime != self.runtime):
                raise ValueError('审批确认实例、一次性标识或轮次已变化')
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
                member, role = self.service.execution_identity(task)
                self.service.check(member, role)
                if member['revision'] != p['member_revision'] or role['revision'] != p['role_revision']:
                    raise ValueError('岗位或专员权限已变化，旧卡不能批准')
            from src.gateway.audit import append_audit
            if not append_audit(self.service.root, {'category': 'decision', 'operation': '专员审批：' + p['tool'],
                    'decision': 'allowed' if approve else 'denied', 'operation_kind': p['kind'], 'approval_scope': 'once',
                    'decision_source': 'user', 'approval_outcome': desired, 'tainted': p['tainted'],
                    'staff_task': task.id, 'staff_action': action_id, 'action_digest': card['digest'],
                    **{key: p[key] for key in ('nonce', 'round', 'instance', 'runtime')}}, durable=True):
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
            from src.workflow.staff.team_roster import is_managed
            if not is_managed(task):
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
