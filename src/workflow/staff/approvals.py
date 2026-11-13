"""Owner-approved staff registration, with durable receipts before granting authority."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone

from src.workflow.staff.owner_confirmation import owner_instance, require_owner_route
from src.workflow.staff.registry import StaffRegistry
from src.workflow.tasks.tasks import TASK_STATE_LOCK


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class StaffApprovals:
    def __init__(self, root, *, clock=None):
        self.registry = StaffRegistry(root)
        self.clock = clock or time.time

    @property
    def instance(self):
        return owner_instance(self.registry.root)

    def snapshot(self, kind, data):
        if not isinstance(data, dict):
            raise ValueError('提议内容必须是对象')
        if kind == 'bundle':
            from src.workflow.staff.bundles import StaffBundles
            return StaffBundles(self.registry).snapshot(data)
        if kind == 'routine':
            from src.workflow.staff.routines import Routines
            return Routines(self.registry.root, registry=self.registry).snapshot(data)
        if kind == 'routine_migration':
            from src.workflow.staff.routine_migration import RoutineMigration
            return RoutineMigration(self.registry.root).snapshot(data)
        if kind == 'role':
            previous = next((x for x in self.registry.roles() if x['id'] == data.get('id')), None)
            if 'install' in data:
                from src.workflow.staff.roles import standing_roles
                definition = next((x for x in standing_roles() if x['id'] == data.get('id')), None)
                if set(data) != {'id', 'install'} or data['install'] is not True or previous is not None or definition is None:
                    raise ValueError('常设岗位安装须使用未占用的预置 id 和 install=true，不能附带权限')
                return {'kind': kind, 'data': self.registry.validate_role(definition, builtin=True), 'previous': None}
            if previous and previous['builtin']:
                if set(data) != {'id', 'revision', 'upgrade'} or data['upgrade'] is not True or data['revision'] != previous['revision']:
                    raise ValueError('内置岗位不能改写；升级须明确给原版本与 upgrade=true')
                from src.workflow.staff.roles import published_roles
                normalized = {**next(x for x in published_roles() if x['id'] == previous['id']),
                              'enabled': previous['enabled'], 'revision': previous['revision']}
                if normalized == previous:
                    raise ValueError('内置岗位已是当前定义')
                return {'kind': kind, 'data': normalized, 'previous': previous}
            if previous and data.get('revision') != previous['revision']:
                raise ValueError('岗位版本变化')
            from src.workflow.staff.projects import bind
            normalized = self.registry.validate_role(bind(data))
            return {'kind': kind, 'data': normalized, 'previous': previous}
        if kind == 'member':
            if set(data) - {'id', 'name', 'role', 'engine', 'model', 'department', 'limits', 'revision'}:
                raise ValueError('专员提议不能附带权限、状态或批准记录')
            previous = next((x for x in self.registry.members() if x['id'] == data.get('id')), None)
            if previous and (previous['status'] == 'retired' or data.get('revision') != previous['revision']):
                raise ValueError('专员已撤掉或版本变化')
            normalized = self.registry.validate_member(data)
            for key in ('approved_at', 'created_by', 'status'):
                normalized.pop(key, None)
            return {'kind': kind, 'data': normalized, 'previous': previous,
                    'role_snapshot': self.registry.role(normalized['role'])}
        if kind == 'department':
            from src.workflow.staff.departments import Departments
            from src.workflow.staff.notes import StaffNotes
            if set(data) - {'id', 'name', 'duty', 'members', 'lead', 'limits', 'revision', 'status'}:
                raise ValueError('部门提议不能附带权限、笔记版本或例行工作')
            previous = next((x for x in self.registry.departments() if x['id'] == data.get('id')), None)
            if previous and (previous['status'] == 'retired' or data.get('revision') != previous['revision']):
                raise ValueError('部门已撤掉或版本变化')
            normalized = Departments(self.registry).validate(data)
            return {'kind': kind, 'data': normalized, 'previous': previous,
                'members_snapshot': [{**self.registry.member(member), 'role_snapshot': self.registry.role(self.registry.member(member)['role'])}
                                     for member in normalized['members']],
                'notes_snapshot': StaffNotes(self.registry.root).snapshot(normalized['id'])}
        raise ValueError('登记提议只支持 role / member / department')

    @staticmethod
    def approval_level(snapshot):
        if snapshot['kind'] == 'bundle':
            return 'desktop'
        if snapshot['kind'] == 'routine_migration':
            return 'desktop'
        if snapshot['kind'] == 'department':
            return 'desktop' if any(StaffApprovals.approval_level({'kind': 'role', 'data': member['role_snapshot']}) != 'normal'
                                    for member in snapshot['members_snapshot']) else 'normal'
        assigned = snapshot['data'] if snapshot['kind'] == 'role' else snapshot['role_snapshot']
        grants = assigned['grants']
        return 'normal' if set(grants['operations']) == {'read'} and not grants['web'] and not grants['sees'] else 'desktop'

    @staticmethod
    def _round(item):
        previous = item['snapshot']['previous']
        return previous['revision'] + 1 if previous else 1

    def _records(self):
        with TASK_STATE_LOCK:
            records = self.registry._read('proposals.json', {'version': 1, 'proposals': []}).get('proposals')
            if not isinstance(records, list):
                raise ValueError('专员审批历史损坏')
            for record in records:
                try:
                    keys = ('id', 'snapshot', 'owner', 'reason', 'created_at', 'nonce')
                    level = self.approval_level(record['snapshot'])
                    if 'requires_desktop_key' in record:
                        # Preserve the original digest and receipt; the retired key file is never read.
                        keys += ('requires_desktop_key',)
                        if type(record['requires_desktop_key']) is not bool or record['requires_desktop_key'] != (level != 'normal'):
                            raise ValueError('旧提议批准分级不一致')
                    else:
                        keys += ('approval_level', 'round')
                        if record['approval_level'] != level or type(record['round']) is not int or record['round'] != self._round(record):
                            raise ValueError('提议批准分级或轮次不一致')
                    original = {key: record[key] for key in keys}
                    original['status'] = 'pending'
                    if digest(original) != record['digest']:
                        raise ValueError('提议摘要不一致')
                except (KeyError, TypeError) as error:
                    raise ValueError('专员审批历史无效') from error
            return deepcopy(records)

    def _view(self, item):
        value = deepcopy(item)
        value.pop('requires_desktop_key', None)
        value.update(approval_level=self.approval_level(item['snapshot']), round=self._round(item), instance=self.instance)
        return value

    def list(self):
        return [self._view(item) for item in self._records()]

    def propose(self, owner, kind, data, reason, *, expected=None):
        if not isinstance(owner, str) or not owner or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError('提议需要发起会话和明确理由')
        with TASK_STATE_LOCK:
            snapshot = self.snapshot(kind, data)
            if expected is not None and snapshot != expected:
                raise ValueError('确认期间岗位或专员已变化')
            item = {'id': 'staff-proposal-' + uuid.uuid4().hex, 'snapshot': snapshot,
                    'owner': owner, 'reason': reason, 'status': 'pending',
                    'created_at': datetime.now(timezone.utc).isoformat(), 'nonce': uuid.uuid4().hex,
                    'approval_level': self.approval_level(snapshot),
                    'round': snapshot['previous']['revision'] + 1 if snapshot['previous'] else 1}
            item['digest'] = digest(item)
            entries = self._records()
            self.registry._write('proposals.json', {'version': 1, 'proposals': [item, *entries]})
            return self._view(item)

    def _get(self, proposal_id):
        return next((x for x in self._records() if x['id'] == proposal_id), None) or self.registry._missing('提议不存在')

    def get(self, proposal_id):
        return self._view(self._get(proposal_id))

    def _save(self, item):
        entries = self._records()
        self.registry._write('proposals.json', {'version': 1,
            'proposals': [deepcopy(item) if x['id'] == item['id'] else x for x in entries]})

    def _target(self, item):
        snapshot = item['snapshot']
        if snapshot['kind'] == 'bundle':
            from src.workflow.staff.bundles import StaffBundles
            return StaffBundles(self.registry).target(item)
        if snapshot['kind'] == 'routine':
            from src.workflow.staff.routines import Routines
            return Routines(self.registry.root).target(snapshot, item['decision']['at'], item['owner'])
        if snapshot['kind'] == 'routine_migration':
            from src.workflow.staff.routine_migration import RoutineMigration
            return RoutineMigration(self.registry.root).target(snapshot, item['decision']['at'], item['owner'])
        data = deepcopy(snapshot['data'])
        previous = snapshot['previous']
        if snapshot['kind'] == 'role':
            target = self.registry.validate_role(data, builtin=bool(data.get('builtin')))
        elif snapshot['kind'] == 'member':
            data['approved_at'] = item['decision']['at']
            if previous:
                data['status'] = previous['status']
            target = self.registry.validate_member(data)
        else:
            from src.workflow.staff.departments import Departments
            target = Departments(self.registry).validate(data)
        target['revision'] = previous['revision'] + 1 if previous else 1
        return target

    def decide(self, proposal_id, approve, expected_digest, *, nonce=None, round=None, instance=None):
        require_owner_route(self.registry.root)
        if type(approve) is not bool:
            raise ValueError('需要主人明确批准或拒绝')
        with TASK_STATE_LOCK:
            item = self._get(proposal_id)
            if instance != self.instance or nonce != item['nonce'] or type(round) is not int or round != self._round(item):
                raise ValueError('提议确认实例、一次性标识或轮次已变化，请刷新')
            if item['digest'] != expected_digest:
                raise ValueError('提议内容已经变化，请刷新')
            desired = 'approved' if approve else 'rejected'
            if item['status'] == desired:
                return self._view(item)
            if item['snapshot']['kind'] == 'bundle' and item['status'] in {'applying', 'cancelling'} and not approve:
                from src.workflow.staff.bundles import StaffBundles
                self._audit(item, False)
                if item['status'] != 'cancelling':
                    item['status'] = 'cancelling'
                    item['cancellation'] = {'at': datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
                                            'instance': self.instance, 'source': 'owner_route'}
                    self._save(item)
                StaffBundles(self.registry).cancel(item)
                item['status'] = 'rejected'
                self._save(item)
                return self._view(item)
            if item['status'] not in {'pending', 'applying'} or (item['status'] == 'applying' and not approve):
                raise ValueError('提议已经处理，不能改换决定')
            if item['status'] == 'applying':
                self._audit(item, approve)
            if item['status'] == 'pending':
                snapshot = item['snapshot']
                kind, data = snapshot['kind'], snapshot['data']
                if kind == 'member':
                    allowed = {'id', 'name', 'role', 'engine', 'model', 'department', 'limits', 'revision'}
                    data = {key: value for key, value in data.items() if key in allowed}
                elif kind == 'role' and data.get('builtin'):
                    data = ({'id': data['id'], 'install': True} if snapshot['previous'] is None else
                            {'id': data['id'], 'revision': data['revision'], 'upgrade': True})
                elif kind == 'department':
                    data = {key: value for key, value in data.items() if key not in {'notes_revision', 'routines'}}
                if (approve or kind != 'bundle') and self.snapshot(kind, data) != snapshot:
                    raise ValueError('岗位或专员版本已变化，旧提议不能批准')
                self._audit(item, approve)
                item['decision'] = {'at': datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
                    'approve': approve, 'source': 'owner_route', 'instance': self.instance,
                    'digest': item['digest'], 'nonce': item['nonce'], 'round': self._round(item)}
                item['status'] = 'applying' if approve else 'rejected'
                if approve:
                    item['target'] = self._target(item)
                self._save(item)  # Durable owner receipt before any authority is written.
            if approve:
                if self._target(item) != item['target']:
                    raise ValueError('批准后的登记目标已变化')
                snapshot = item['snapshot']
                if snapshot['kind'] == 'bundle':
                    from src.workflow.staff.bundles import StaffBundles
                    StaffBundles(self.registry).apply(item)
                    item['status'] = 'approved'
                    self._save(item)
                    return self._view(item)
                if snapshot['kind'] == 'routine_migration':
                    from src.workflow.staff.routine_migration import RoutineMigration
                    RoutineMigration(self.registry.root).apply(item['target'], snapshot)
                    item['status'] = 'approved'
                    self._save(item)
                    return self._view(item)
                from src.workflow.staff.routines import Routines
                entries = (Routines(self.registry.root).definitions() if snapshot['kind'] == 'routine' else
                           self.registry.roles() if snapshot['kind'] == 'role' else self.registry.members()
                           if snapshot['kind'] == 'member' else self.registry.departments())
                current = next((x for x in entries if x['id'] == item['target']['id']), None)
                if current != item['target']:
                    if current != snapshot['previous']:
                        raise ValueError('登记已变化，保留批准历史，请重新提议')
                    if snapshot['kind'] == 'member' and self.registry.role(snapshot['data']['role']) != snapshot['role_snapshot']:
                        raise ValueError('所属岗位已变化，请重新提议')
                    if snapshot['kind'] == 'routine':
                        Routines(self.registry.root).check_binding(item['target'])
                        Routines(self.registry.root).apply(item['target'], snapshot['previous'])
                    elif snapshot['kind'] == 'role':
                        if snapshot['data'].get('builtin'):
                            if snapshot['previous'] is None:
                                self.registry.install_builtin(item['target'])
                            else:
                                self.registry.upgrade_builtin(snapshot['data'])
                        else:
                            self.registry.register_role(snapshot['data'])
                    elif snapshot['kind'] == 'member':
                        payload = {**item['target'], 'revision': snapshot['data']['revision']}
                        self.registry.register_member(payload)
                    else:
                        # Recheck the exact member/role and note snapshots before joining any group.
                        from src.workflow.staff.notes import StaffNotes
                        if StaffNotes(self.registry.root).snapshot(snapshot['data']['id']) != snapshot['notes_snapshot']:
                            raise ValueError('部门笔记已变化，请重新提议')
                        current_members = [{**self.registry.member(member['id']), 'role_snapshot': self.registry.role(member['role'])}
                                           for member in snapshot['members_snapshot']]
                        if current_members != snapshot['members_snapshot']:
                            raise ValueError('部门成员或岗位已变化，请重新提议')
                        self.registry.register_department({**item['target'], 'revision': snapshot['data']['revision']})
                item['status'] = 'approved'
                self._save(item)
            return self._view(item)

    def _audit(self, item, approve):
        from src.gateway.audit import append_audit
        from src.workflow.staff.approval_stats import _instant
        created = _instant(item.get('created_at'))
        timing = {'wait_seconds': max(0, (datetime.now(timezone.utc) - created).total_seconds())} if created else {}
        if not append_audit(self.registry.root, {'category': 'decision', 'operation': '专员登记：' + item['snapshot']['kind'],
                'decision': 'allowed' if approve else 'denied', 'decision_source': 'user', 'operation_kind': 'execute',
                'approval_scope': 'once', 'approval_outcome': 'approved' if approve else 'denied',
                'staff_proposal': item['id'], 'action_digest': item['digest'],
                'approval_source': 'registration', **timing,
                'nonce': item['nonce'], 'round': self._round(item), 'instance': self.instance}, durable=True):
            raise OSError('审批审计保存失败，未授权登记')
