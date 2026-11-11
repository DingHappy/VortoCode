"""Explicit signed migration; one durable owner marker prevents both old and new scheduling."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib

from src.scheduling.routine_schedule import RoutineSchedule, instant, zone
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.roles import routine_roles
from src.workflow.staff.routines import Routines
from src.workflow.tasks.tasks import TASK_STATE_LOCK


def legacy_owner(root, name):
    for record in Routines(root).read()['migrations']:
        if name in record['names']:
            return {'status': record['status'], 'routine': record['names'][name]}
    return None


class _PreviewRegistry(StaffRegistry):
    def __init__(self, root, roles, members):
        super().__init__(root)
        self._roles, self._members = roles, members
    def roles(self): return deepcopy(self._roles)
    def members(self): return deepcopy(self._members)


class RoutineMigration:
    def __init__(self, root, *, clock=None):
        self.root = str(root)
        self.registry = StaffRegistry(self.root)
        self.routines = Routines(self.root)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def plan(self, timezone_name):
        from src.gateway.workspace_scope import current_workspace_scope, GENERAL
        from src.scheduling.schedule_cards import config_snapshot
        zone(timezone_name)
        jobs, revision = config_snapshot(self.root)
        if len({j.name for j in jobs}) != len(jobs):
            raise ValueError('旧作业重名，不能确定迁移身份；请先核对原配置')
        existing_roles, existing_members = self.registry.roles(), self.registry.members()
        roles = routine_roles()
        for role in roles:
            previous = next((x for x in existing_roles if x['id'] == role['id']), None)
            if previous is not None and previous != role:
                raise ValueError('兼容岗位已调整或停用，不能自动覆盖')
            self.registry.validate_role(role, builtin=True)
        all_roles = existing_roles + [r for r in roles if r['id'] not in {x['id'] for x in existing_roles}]
        members, entries, deferred = [], [], []
        for job in jobs:
            reason = ('命令类保留在项目服务' if job.kind == 'command' else
                      '项目提示类暂缓迁移' if current_workspace_scope() != GENERAL else
                      '已迁移，原配置不再调度' if legacy_owner(self.root, job.name) else
                      '提示超过 4000 字符，须先明确收窄' if len(job.prompt) > 4000 else
                      '总时限超过兼容岗位上限，须先核对' if job.timeout > 3600 else '')
            if reason:
                deferred.append({'name': job.name, 'reason': reason})
                continue
            if len(entries) >= 32:
                raise ValueError('本次可迁移作业超过 32 条，请分批核对配置')
            tag = hashlib.sha256(job.name.encode()).hexdigest()[:16]
            member_data = {'id': 'staff-cron-' + tag, 'name': ('旧定时·' + job.name)[:80],
                'role': 'routine-public' if job.allow_web else 'routine-offline', 'engine': 'vortocode',
                'model': job.model or '', 'approved_at': 'pending-owner-approval'}
            if any(x['id'] == member_data['id'] for x in existing_members):
                raise ValueError('旧作业对应的专员身份已存在，不能覆盖')
            view = _PreviewRegistry(self.root, all_roles, existing_members + members)
            member = view.validate_member(member_data)
            members.append(member)
            view = _PreviewRegistry(self.root, all_roles, existing_members + members)
            from src.scheduling.cron import _env_int
            budget = job.budget or _env_int('VORTOONE_CRON_TOKEN_BUDGET', 0)
            data = {'id': 'routine-cron-' + tag, 'name': job.name, 'member': member['id'], 'prompt': job.prompt, 'token_budget': budget,
                'schedule': job.schedule.raw, 'timezone': timezone_name, 'enabled': job.enabled, 'delivery': 'notify'}
            snapshot = Routines(self.root, registry=view).snapshot(data)
            if snapshot['previous'] is not None:
                raise ValueError('旧作业对应的例行工作已存在，不能覆盖')
            snapshot['legacy'] = {'name': job.name, 'announce': job.announce, 'timeout': job.timeout,
                                  'budget': budget}
            entries.append(snapshot)
        needed = {m['role'] for m in members}
        return {'config_revision': revision, 'timezone': timezone_name, 'roles': [r for r in roles if r['id'] in needed],
                'members': members, 'routines': entries, 'deferred': deferred}

    def snapshot(self, data):
        if set(data) != {'timezone', 'config_revision'}:
            raise ValueError('迁移只接受已核对的原配置摘要与原主机时区')
        plan = self.plan(data['timezone'])
        if plan['config_revision'] != data['config_revision']:
            raise ValueError('旧定时配置已变化，请重新预览迁移清单')
        if not plan['routines']:
            raise ValueError('没有可迁移的通用范围提示作业；项目和命令作业保留原路径')
        return {'kind': 'routine_migration', 'data': dict(data), 'previous': None, 'plan': plan,
                'members_snapshot': [{**m, 'role_snapshot': next(r for r in plan['roles'] if r['id'] == m['role'])}
                                     for m in plan['members']]}

    def target(self, snapshot, at, owner):
        plan = snapshot['plan']
        members = [{**m, 'approved_at': at} for m in plan['members']]
        records = []
        for original in plan['routines']:
            prepared = deepcopy(original)
            prepared['member_snapshot'] = next(m for m in members if m['id'] == original['data']['member'])
            record = self.routines.target(prepared, at, owner)
            record['legacy'] = original['legacy']
            records.append(record)
        return {'id': 'routine-migration-' + hashlib.sha256((plan['config_revision'] + at).encode()).hexdigest()[:20],
                'config_revision': plan['config_revision'], 'timezone': plan['timezone'], 'roles': plan['roles'],
                'members': members, 'routines': records}

    def apply(self, target, snapshot):
        from src.scheduling.cron import CronState, job_lock, config_lock
        from src.scheduling.schedule_cards import config_revision
        with TASK_STATE_LOCK, ExitStack() as locks:
            locks.enter_context(config_lock(self.root))
            for item in target['routines']:
                locks.enter_context(job_lock(self.root, item['legacy']['name']))
            if config_revision(self.root) != target['config_revision']:
                raise ValueError('原配置已变化；迁移保持待核对，不自动启用新旧任一路径')
            payload = self.routines.read()
            marker = next((x for x in payload['migrations'] if x['id'] == target['id']), None)
            if marker is None:
                now = instant(self.clock())
                cursors = {}
                for item in target['routines']:
                    schedule = RoutineSchedule(item['schedule'], item['timezone'])
                    last = CronState(self.root).last_run(item['legacy']['name'])
                    if last is not None:
                        cursor = last.replace(tzinfo=zone(item['timezone'])) if last.tzinfo is None else last
                    elif schedule.schedule.kind == 'every':
                        cursor = now - schedule.schedule.interval
                    elif schedule.schedule.kind == 'at':
                        local = now.astimezone(schedule.zone)
                        h, m = schedule.schedule.at
                        today = local.replace(hour=h, minute=m, second=0, microsecond=0)
                        cursor = today - timedelta(microseconds=1) if today <= local else now
                    else:
                        cursor = now.replace(second=0, microsecond=0) - timedelta(microseconds=1)
                    cursors[item['id']] = instant(cursor).isoformat()
                marker = {'id': target['id'], 'status': 'applying', 'names': {r['legacy']['name']: r['id'] for r in target['routines']},
                          'routines': [r['id'] for r in target['routines']], 'cursors': cursors}
                payload['migrations'].append(marker)
                self.registry._write('routines.json', payload)  # Exclusive ownership before any new identity is usable.
            if marker['status'] == 'complete':
                return
            roles = self.registry.roles()
            for role in target['roles']:
                current = next((x for x in roles if x['id'] == role['id']), None)
                if current is not None and current != role:
                    raise ValueError('原迁移的岗位已变化，不能继续覆盖')
                if current is None:
                    roles.append(role)
            self.registry._write('roles.json', {'version': 1, 'roles': roles})
            for member in target['members']:
                current = next((x for x in self.registry.members() if x['id'] == member['id']), None)
                if current is not None and current != member:
                    raise ValueError('原迁移的专员已变化，不能覆盖')
                if current is None:
                    self.registry.register_member(member)
            for item in target['routines']:
                self.routines.apply(item, None)
                self.routines.check_binding(item)
            if config_revision(self.root) != target['config_revision']:
                raise ValueError('迁移期间原配置被改动，保持待核对，未启用新旧任一路径')
            payload = self.routines.read()
            for item in payload['routines']:
                if item['id'] in marker['cursors']:
                    item['state']['cursor'] = marker['cursors'][item['id']]
            next(x for x in payload['migrations'] if x['id'] == target['id'])['status'] = 'complete'
            self.registry._write('routines.json', payload)
