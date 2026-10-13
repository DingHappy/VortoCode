"""Read-only recovery observation; it cannot authorize or enqueue execution."""
from __future__ import annotations

import re

from src.workflow.tasks.task_recovery import recovery_task_id

_STATUS = {'queued', 'running', 'blocked', 'done', 'failed', 'paused', 'interrupted', 'cancelled'}


def recovery_observation(source: dict, child: dict, *, repo_root: str) -> dict:
    contract = child.get('recovery') if isinstance(child.get('recovery'), dict) else {}
    revision = source.get('recovery_source_revision')
    scopes = [task.get('execution_scope') for task in (source, child)]
    if (any(not isinstance(task.get(key), str) for task in (source, child)
            for key in ('id', 'kind', 'status', 'plan_id', 'prompt', 'owner_session', 'goal_id', 'branch'))
            or source.get('kind') not in {'dev', 'dev-resume'}
            or source.get('status') not in {'failed', 'paused', 'interrupted', 'cancelled'}
            or not isinstance(source.get('id'), str)
            or source.get('resumed_task_id') != recovery_task_id(source['id'])
            or child.get('id') != source['resumed_task_id']
            or child.get('parent_task_id') != source['id'] or child.get('kind') != 'dev-resume'
            or not source.get('plan_id') or child.get('plan_id') != source['plan_id']
            or any(child.get(key) != source.get(key) for key in ('prompt', 'owner_session', 'goal_id'))
            or any(not isinstance(scope, dict) or scope.get('repo_root') != repo_root for scope in scopes)
            or not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{64}', revision)
            or contract.get('source_task_id') != source['id'] or contract.get('source_revision') != revision
            or not isinstance(contract.get('plan_revision'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', contract['plan_revision'])
            or child.get('status') not in _STATUS
            or source.get('branch') and child.get('branch') and source['branch'] != child['branch']):
        raise ValueError('恢复任务与原任务、项目、计划或版本不匹配；未执行任何操作')
    return {'id': child['id'], 'status': child['status'], 'parent_task_id': source['id'],
            'source_revision': revision,
            'plan_id': child['plan_id'], 'branch': str(child.get('branch') or '')[:200],
            'result': str(child.get('result') or '')[-1500:], 'error': str(child.get('error') or '')[-500:],
            'has_further_recovery': bool(child.get('resumed_task_id'))}


def observation_matches_source(observation: dict, source: dict) -> bool:
    """Validate cached association only; this does not refresh the child's progress."""
    revision = source.get('recovery_source_revision')
    return (isinstance(observation, dict)
            and source.get('kind') in ('dev', 'dev-resume')
            and source.get('status') in ('failed', 'paused', 'interrupted', 'cancelled')
            and isinstance(revision, str) and re.fullmatch(r'[a-f0-9]{64}', revision) is not None
            and observation.get('source_revision') == revision
            and observation.get('parent_task_id') == source.get('id')
            and observation.get('id') == source.get('resumed_task_id')
            and observation.get('plan_id') == source.get('plan_id'))
