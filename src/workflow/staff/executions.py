"""Bind observations to an existing staff task; never schedule, resume or approve."""
from __future__ import annotations

import logging
from dataclasses import replace
from time import time

from src.agents.backends.execution import (
    ExecutionFailure, ExecutionRef, ExecutionSnapshot, ExecutionState, new_run_id, process_instance,
)
from src.memory.write_policy import redact_secret_like

logger = logging.getLogger(__name__)
_TERMINAL = {ExecutionState.FINISHED, ExecutionState.FAILED, ExecutionState.CANCELLED, ExecutionState.UNKNOWN}


def _bounded(snapshot):
    def text(value, limit):
        return redact_secret_like(value)[0][:limit]
    result = snapshot.result
    if result is not None:
        result = replace(result, summary=text(result.summary, 2000), evidence=tuple(
            replace(item, summary=text(item.summary, 500)) for item in result.evidence[:8]))
    return replace(snapshot, native_state=text(snapshot.native_state, 100), result=result,
        progress=replace(snapshot.progress, summary=text(snapshot.progress.summary, 500)),
        error=replace(snapshot.error, message=text(snapshot.error.message, 1000)) if snapshot.error else None)


class StaffExecution:
    """Trusted host callback, fenced by original task/round/instance/attempt.

    Persistence failures only make observation unavailable; they cannot replay
    work, authorize it or change the original task's result/acceptance policy.
    """
    def __init__(self, work, task, backend_id):
        self.work, self.task_id, self.number = work, task.id, task.collaboration['round']
        self.ref = ExecutionRef(backend_id, process_instance('staff'), new_run_id(), task_id=task.id)
        self.active = False
        initial = ExecutionSnapshot(self.ref, ExecutionState.QUEUED, time())
        def begin(current):
            if current.status not in {'running', 'blocked'}:
                raise ValueError('task is not executing')
            current.collaboration['execution'] = {'round': self.number, 'snapshot': initial.to_dict(), 'closed': False}
        try:
            work.update_staff_action(self.task_id, self.number, begin)
            self.active = True
        except Exception as error:
            logger.warning('Staff execution observation unavailable (%s)', type(error).__name__)

    def publish(self, snapshot):
        if not self.active or self.ref.instance_id != process_instance('staff'):
            return False
        if not isinstance(snapshot, ExecutionSnapshot) or snapshot.ref.key != self.ref.key \
                or snapshot.ref.task_id != self.task_id or snapshot.ref.scope != self.ref.scope:
            return False
        snapshot = _bounded(snapshot)
        def update(current):
            record = current.collaboration.get('execution') or {}
            previous = ExecutionSnapshot.from_dict(record.get('snapshot', {}))
            if current.status not in {'running', 'blocked'} or record.get('round') != self.number \
                    or previous.ref.key != self.ref.key or record.get('closed') \
                    or snapshot.observed_at < previous.observed_at or snapshot == previous:
                raise ValueError('stale execution observation')
            for name in ('session_id', 'turn_id'):
                if getattr(previous.ref, name) and getattr(previous.ref, name) != getattr(snapshot.ref, name):
                    raise ValueError('native execution identity changed')
            current.collaboration['execution'] = {'round': self.number, 'snapshot': snapshot.to_dict(),
                'closed': snapshot.state in _TERMINAL}
        try:
            self.work.update_staff_action(self.task_id, self.number, update)
            return True
        except ValueError:
            return False
        except Exception as error:
            logger.warning('Staff execution observation unavailable (%s)', type(error).__name__)
            return False


def execution_projection(task):
    """Read old records conservatively; stale observations cannot look live."""
    try:
        record = task.collaboration.get('execution')
        if not isinstance(record, dict) or type(record.get('round')) is not int:
            return None
        snapshot = ExecutionSnapshot.from_dict(record['snapshot'])
        if snapshot.ref.task_id != task.id:
            return None
        reason = ''
        if record['round'] != task.collaboration['round']:
            reason = 'stale_round'
        elif snapshot.ref.instance_id != process_instance('staff') or snapshot.ref.scope != 'process':
            reason = 'stale_instance'
        elif task.status not in {'running', 'blocked'} and snapshot.state not in _TERMINAL:
            reason = 'task_stopped'
        if reason:
            snapshot = replace(snapshot, state=ExecutionState.UNKNOWN, native_state=reason, result=None,
                error=ExecutionFailure('observation_stale', '请核对原任务；此报告不能代表当前执行', effect='possible'))
        return {'round': record['round'], 'stale': bool(reason), 'snapshot': _bounded(snapshot).to_dict()}
    except (KeyError, TypeError, ValueError):
        return None
