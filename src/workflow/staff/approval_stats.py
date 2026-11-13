"""Read-only approval statistics from the existing audit and collaboration ledgers."""
from datetime import datetime, timedelta, timezone
import math
import re
from statistics import median

from src.agents.trust import KINDS
from src.gateway.audit import approval_history
from src.gateway.main_conversation import main_chat_key
from src.workflow.tasks.tasks import TaskLedger

BASES = ('existing_trust', 'turn', 'task', 'standing', 'task_workspace', 'protective')
OUTCOMES = ('approved', 'denied', 'expired', 'pending', 'cancelled', 'interrupted', 'unknown')


def _instant(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _seconds(value):
    try:
        return value if type(value) in (float, int) and math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _id(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,160}', value) else ''


def _task_source(task, payload):
    data = task.collaboration if isinstance(task.collaboration, dict) else {}
    dispatch = data.get('dispatch') or {}
    if not isinstance(dispatch, dict):
        return 'other', ''
    routine = dispatch.get('routine') or {}
    if isinstance(routine, dict) and _id(routine.get('id')):
        return 'routine', routine['id']
    member = _id(payload.get('member')) or _id(dispatch.get('member'))
    return ('staff', member) if member else ('other', '')


def approval_statistics(root, *, days=7, now=None):
    if type(days) is not int or not 1 <= days <= 31:
        raise ValueError('统计范围必须为 1 到 31 天')
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    history = approval_history(root)
    scan = TaskLedger(root).scan()
    tasks = {task.id: task for task in scan.tasks}
    main = main_chat_key(root)
    groups, automatic_groups, waits = {}, {}, []
    automatic = dict.fromkeys(BASES, 0)
    coverage = {'audit': history['status'],
                'tasks': 'unavailable' if not scan.readable else 'partial' if scan.truncated or scan.skipped else 'current',
                'missing_wait': 0, 'unknown_metadata': 0, 'invalid_timestamps': 0,
                'scope_grants': 0, 'audit_decisions': 0, 'task_cards': 0}
    system_denied, seen = 0, set()

    def add(record, *, task=None, payload=None, from_task=False):
        nonlocal system_denied
        at = _instant(record.get('ts') or record.get('timestamp'))
        if at is None:
            coverage['invalid_timestamps'] += 1
            return
        if not start <= at <= now:
            return
        if record.get('approval_event') == 'scope_grant':
            coverage['scope_grants'] += 1
            return
        source = record.get('approval_source')
        source_id = _id(record.get('source_id')) if source in ('staff', 'routine') else ''
        if task is not None and source not in ('staff', 'routine'):
            source, source_id = _task_source(task, payload or {})
        if source not in ('main', 'staff', 'routine', 'registration', 'other'):
            source = 'main' if main and record.get('session') == main else 'other'
        kind = record.get('operation_kind') if record.get('operation_kind') in KINDS else 'unknown'
        tainted = record['tainted'] if type(record.get('tainted')) is bool else 'unknown'
        key = (at.date().isoformat(), source, source_id, kind, tainted)
        who, decision = record.get('decision_source'), record.get('decision')
        if who == 'preauthorized' and decision == 'allowed':
            basis = record.get('approval_scope')
            if basis not in BASES:
                basis = 'unknown'
            automatic[basis] = automatic.get(basis, 0) + 1
            automatic_groups[(*key, basis)] = automatic_groups.get((*key, basis), 0) + 1
            return
        if who == 'system':
            system_denied += decision == 'denied'
            return
        group = groups.setdefault(key, {**dict.fromkeys(OUTCOMES, 0), 'count': 0, '_waits': []})
        outcome = record.get('approval_outcome')
        if outcome not in OUTCOMES:
            outcome = 'unknown'  # Legacy allowed/denied does not prove an owner decision.
        group['count'] += 1
        group[outcome] += 1
        duration = _seconds(record.get('wait_seconds'))
        if duration is not None and outcome in ('approved', 'denied', 'expired', 'cancelled'):
            group['_waits'].append(duration)
            waits.append(duration)
        elif outcome in ('approved', 'denied', 'expired', 'cancelled'):
            coverage['missing_wait'] += 1
        if kind == 'unknown' or tainted == 'unknown' or outcome == 'unknown' or source == 'other':
            coverage['unknown_metadata'] += 1
        coverage['task_cards' if from_task else 'audit_decisions'] += 1

    for record in history['entries']:
        task = tasks.get(record.get('staff_task'))
        if record.get('staff_action'):
            seen.add(record['staff_action'])
        add(record, task=task)

    # Timeout/pending/interrupted cards may have no decision audit. Project their
    # existing receipts, deduplicating cards already represented by durable audit.
    for task in tasks.values():
        data = task.collaboration
        if not isinstance(data, dict):
            continue
        cards = data.get('approval_history') or []
        if not isinstance(cards, list):
            coverage['tasks'] = 'partial'
            cards = []
        else:
            cards = list(cards)
        if len(cards) >= 32:
            coverage['tasks'] = 'partial'
        current = data.get('approval')
        if isinstance(current, dict) and isinstance(current.get('payload'), dict):
            cards.append(current)
        for card in cards:
            if not isinstance(card, dict):
                continue
            payload = card.get('payload', card)
            if not isinstance(payload, dict):
                coverage['tasks'] = 'partial'
                continue
            action_id = _id(payload.get('id'))
            if not action_id or action_id in seen:
                continue
            seen.add(action_id)
            outcome = card.get('status', 'unknown')
            created = _instant(payload.get('created'))
            decided = _instant(card.get('decided_at'))
            expires = payload.get('expires_at')
            try:
                if type(expires) in (int, float) and math.isfinite(expires):
                    expires = datetime.fromtimestamp(expires, timezone.utc)
                else:
                    if expires is not None:
                        coverage['tasks'] = 'partial'
                    expires = None
            except (ValueError, OverflowError, OSError):
                coverage['tasks'] = 'partial'
                expires = None
            if outcome == 'pending' and expires is not None and expires <= now:
                outcome = 'expired'
            at = decided or (expires if outcome == 'expired' else created)
            duration = (decided - created).total_seconds() if decided and created else None
            if outcome == 'expired' and expires and created:
                duration = (expires - created).total_seconds()
            add({'ts': at.isoformat() if at else '', 'operation_kind': payload.get('kind'),
                 'tainted': payload.get('tainted'), 'approval_outcome': outcome,
                 'wait_seconds': duration}, task=task, payload=payload, from_task=True)

    rows = []
    for key, group in sorted(groups.items(), key=lambda pair: str(pair[0])):
        durations = group.pop('_waits')
        rows.append(dict(zip(('day', 'source', 'source_id', 'kind', 'tainted'), key), **group,
                         timed=len(durations), median_wait_seconds=median(durations) if durations else None))
    totals = {name: sum(row[name] for row in rows) for name in ('count', *OUTCOMES)}
    totals.update(timed=len(waits), median_wait_seconds=median(waits) if waits else None)
    return {'days': days, 'timezone': 'UTC', 'from': start.isoformat(), 'to': now.isoformat(),
            'groups': rows, 'totals': totals, 'automatic': automatic, 'system_denied': system_denied,
            'automatic_groups': [dict(zip(('day', 'source', 'source_id', 'kind', 'tainted', 'basis'), key), count=count)
                                 for key, count in sorted(automatic_groups.items(), key=lambda pair: str(pair[0]))],
            'coverage': coverage}
