"""Existing-task restart proof and version-bound owner recovery, without a replay log."""
from copy import deepcopy
import hashlib
import json

from src.workflow.staff.owner_confirmation import owner_instance, require_owner_route
from src.workflow.staff.team_roster import is_managed
from src.workflow.tasks.tasks import TASK_STATE_LOCK, TaskLedger


def task_digest(task):
    value = task.to_dict()
    value.pop('updated', None)
    value['collaboration'] = {k: v for k, v in value['collaboration'].items() if k != 'queue_proof'}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def seed_queue_proof(task):
    """Trusted creation only; never retrofit a legacy queue or a later round."""
    if task.status != 'queued' or task.collaboration['round'] != 1:
        raise ValueError('只有尚未开始的首轮任务能记录排队证明')
    task.collaboration['queue_proof'] = {'version': 1, 'round': 1, 'digest': task_digest(task)}


def never_started(task):
    proof = task.collaboration.get('queue_proof')
    return (task.status == 'queued' and task.collaboration.get('round') == 1
            and isinstance(proof, dict) and type(proof.get('version')) is int and proof['version'] == 1
            and type(proof.get('round')) is int and proof['round'] == 1 and proof.get('digest') == task_digest(task))


class StaffRecovery:
    def __init__(self, service):
        self.service, self.root = service, service.root

    def snapshot(self):
        with TASK_STATE_LOCK:
            items = []
            for task in TaskLedger(self.root).list():
                if not is_managed(task) or task.status != 'interrupted':
                    continue
                state = task.collaboration
                card = state.get('approval') or {}
                items.append({'id': task.id, 'digest': task_digest(task), 'prompt': task.prompt,
                    'member': state['dispatch']['member'], 'team': state['dispatch'].get('root'),
                    'round': state['round'], 'error': task.error, 'result': task.result,
                    'log': list(task.log), 'messages': deepcopy(state.get('messages', [])),
                    'usage': deepcopy(state.get('usage')), 'approval': deepcopy(card),
                    'execution': {key: deepcopy(state[key]) for key in ('approval_history', 'model_target', 'model_observations', 'delivery', 'output', 'quota') if key in state},
                    'effects': '审批或请求记录不代表动作完成；已经发出的动作可能生效，请核对原系统。'})
            return {'instance': owner_instance(self.root), 'items': items}

    def decide(self, selected, action, instance):
        require_owner_route(self.root)
        if (instance != owner_instance(self.root) or action not in {'rework', 'abandon'}
                or not isinstance(selected, list) or not 1 <= len(selected) <= 100
                or any(not isinstance(x, dict) or set(x) != {'id', 'digest'} or not isinstance(x['id'], str) for x in selected)
                or len({x['id'] for x in selected}) != len(selected)):
            raise ValueError('恢复实例或所选任务无效，请刷新核对')
        with TASK_STATE_LOCK:
            tasks = []
            for row in selected:
                task = TaskLedger(self.root).load(row['id'])
                if not task or not is_managed(task) or task.status != 'interrupted' or task_digest(task) != row['digest']:
                    raise ValueError('所选任务已变化；未开始这批操作，请刷新核对')
                tasks.append(task)
            results = []
            for task in tasks:
                try:
                    from src.gateway.audit import append_audit
                    if not append_audit(self.root, {'category': 'decision', 'operation': '中断专员任务：' + action,
                            'decision': 'allowed', 'decision_source': 'user', 'approval_scope': 'once',
                            'staff_task': task.id, 'action_digest': task_digest(task), 'round': task.collaboration['round'],
                            'instance': instance}, durable=True):
                        raise OSError('核对审计未保存，未恢复任务')
                    if action == 'abandon':
                        def abandon(current):
                            current.status, current.error = 'cancelled', '主人核对后放弃；未恢复执行。'
                        self.service.collaboration(task.owner_session).update_staff_action(task.id, task.collaboration['round'], abandon)
                    else:
                        self._rework(task)
                    results.append({'id': task.id, 'status': 'applied'})
                except (ValueError, OSError) as error:
                    results.append({'id': task.id, 'status': 'failed', 'error': str(error)})
            return {'results': results}

    def _rework(self, task):
        dispatch = task.collaboration['dispatch']
        note = '主人核对重启前记录后要求返工；先核对可能已生效的动作，不重放旧审批。'
        if dispatch['source'] == 'team':
            from src.workflow.tasks.team import TeamService
            TeamService(self.root).review(dispatch['root'], task.id, 'rework', note)
            # The HTTP host wakes the existing TeamRunner after this durable decision.
            return
        from src.workflow.staff.routines import Routines
        routines = Routines(self.root)
        previous = routines.prepare_rework(task) if dispatch.get('routine') else None
        try:
            self.service.followup(task.owner_session, task.id, task.collaboration['round'], note)
        except Exception:
            if previous is not None:
                routines.restore_rework(previous)
            raise
