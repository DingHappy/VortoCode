"""Owner-approved staff registration, with durable receipts before granting authority."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone

from src.gateway.mobile_signing import normalize_public_key, verify
from src.workflow.staff.registry import StaffRegistry
from src.workflow.tasks.tasks import TASK_STATE_LOCK


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _public(value):
    if not isinstance(value, str) or len(value) != 130:
        raise ValueError('批准钥匙公钥无效')
    try:
        raw = bytes.fromhex(value)
        normalize_public_key(base64.b64encode(raw).decode())
    except ValueError as error:
        raise ValueError('批准钥匙公钥无效') from error
    return raw.hex()


def _verify(public, message, signature):
    try:
        if not isinstance(signature, str) or len(signature) > 160:
            raise ValueError('签名无效')
        verify(base64.b64encode(bytes.fromhex(public)).decode(), message,
               base64.b64encode(bytes.fromhex(signature)).decode())
    except ValueError as error:
        raise ValueError('批准钥匙签名无效') from error


class StaffApprovals:
    def __init__(self, root, *, clock=None):
        self.registry = StaffRegistry(root)
        self.clock = clock or time.time

    @contextmanager
    def _key_lock(self):
        # The local authorization CLI and the server are separate processes.
        with TASK_STATE_LOCK:
            directory = self.registry.directory
            if directory.is_symlink():
                raise ValueError('批准钥匙登记目录不能是符号链接')
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / '.approval-key.lock'
            if path.is_symlink():
                raise ValueError('批准钥匙登记锁无效')
            if os.name != 'posix':
                raise ValueError('此平台尚未支持批准钥匙登记')
            import fcntl
            fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            with os.fdopen(fd, 'r+b') as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    raise ValueError('批准钥匙正在更新，请重试') from error
                yield

    def key_info(self):
        with self._key_lock():
            return self._key_state()

    def _key_state(self):
        path = self.registry.directory / 'approval-key.json'
        if not path.exists():
            self.registry._write('approval-key.json', {'version': 1, 'instance': uuid.uuid4().hex, 'key': None})
        state = self.registry._read('approval-key.json', {})
        instance, key = state.get('instance'), state.get('key')
        if (set(state) not in ({'version', 'instance', 'key'}, {'version', 'instance', 'key', 'enrollment'}) or not isinstance(instance, str)
                or len(instance) != 32 or any(x not in '0123456789abcdef' for x in instance)):
            raise ValueError('批准钥匙登记损坏，未重新生成身份')
        if key is not None and (not isinstance(key, dict) or set(key) != {'id', 'public_key', 'enrolled_at'}
                or _public(key.get('public_key')) != key['public_key'] or digest(key['public_key']) != key.get('id')):
            raise ValueError('批准钥匙登记损坏，未恢复默认授权')
        enrollment = state.get('enrollment')
        if 'enrollment' in state and (key is not None or not isinstance(enrollment, dict)
                or set(enrollment) != {'digest', 'expires_at'}
                or not isinstance(enrollment.get('digest'), str) or len(enrollment['digest']) != 64
                or any(x not in '0123456789abcdef' for x in enrollment['digest'])
                or type(enrollment.get('expires_at')) is not int or enrollment['expires_at'] <= 0):
            raise ValueError('本地一次性授权记录损坏，未开放绑定')
        return state

    @staticmethod
    def _enrollment_message(state, public):
        return f"vortoone-staff-key-v1\n{state['instance']}\n{_public(public)}".encode()

    def enrollment_message(self, public):
        return self._enrollment_message(self.key_info(), public)

    def verify_action(self, message, signature):
        key = self.key_info().get('key')
        if not key or not signature:
            raise ValueError('此动作需要 Desktop 批准钥匙')
        _verify(key['public_key'], message, signature)

    def authorize_enrollment(self):
        """Local interactive CLI only; deliberately absent from every HTTP/tool surface."""
        with self._key_lock():
            state = self._key_state()
            if state.get('key'):
                raise ValueError('此实例已有批准钥匙；本批不支持替换或自动重置')
            authorization = secrets.token_urlsafe(32)
            state['enrollment'] = {'digest': digest(authorization), 'expires_at': int(self.clock()) + 300}
            self.registry._write('approval-key.json', state)
            return authorization

    def bind_key(self, public, signature, *, authorization=''):
        """Only the authenticated Desktop enrollment route may call this."""
        with self._key_lock():
            public = _public(public)
            state = self._key_state()
            _verify(public, self._enrollment_message(state, public), signature)
            previous = state.get('key')
            if previous and previous['public_key'] != public:
                raise ValueError('此实例已有批准钥匙；本批不支持替换或自动重置')
            if not previous:
                enrollment = state.get('enrollment')
                if (not enrollment or not isinstance(authorization, str) or not 1 <= len(authorization) <= 128
                        or self.clock() >= enrollment['expires_at']
                        or not secrets.compare_digest(digest(authorization), enrollment['digest'])):
                    raise ValueError('首次绑定需要有效的主机本地一次性授权码')
                state['key'] = {'id': digest(public), 'public_key': public,
                                'enrolled_at': datetime.now(timezone.utc).isoformat()}
                state.pop('enrollment', None)
                self.registry._write('approval-key.json', state)
            return deepcopy(state['key'])

    def snapshot(self, kind, data):
        if not isinstance(data, dict):
            raise ValueError('提议内容必须是对象')
        if kind == 'role':
            previous = next((x for x in self.registry.roles() if x['id'] == data.get('id')), None)
            if previous and previous['builtin']:
                raise ValueError('内置岗位不能改写')
            if previous and data.get('revision') != previous['revision']:
                raise ValueError('岗位版本变化')
            normalized = self.registry.validate_role(data)
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
    def needs_key(snapshot):
        if snapshot['kind'] == 'department':
            return any(StaffApprovals.needs_key({'kind': 'role', 'data': member['role_snapshot']})
                       for member in snapshot['members_snapshot'])
        assigned = snapshot['data'] if snapshot['kind'] == 'role' else snapshot['role_snapshot']
        grants = assigned['grants']
        return not (set(grants['operations']) == {'read'} and not grants['web'] and not grants['sees'])

    def list(self):
        with TASK_STATE_LOCK:
            records = self.registry._read('proposals.json', {'version': 1, 'proposals': []}).get('proposals')
            if not isinstance(records, list):
                raise ValueError('专员审批历史损坏')
            for record in records:
                try:
                    original = {key: record[key] for key in ('id', 'snapshot', 'owner', 'reason', 'created_at',
                                                             'nonce', 'requires_desktop_key')}
                    original['status'] = 'pending'
                    if digest(original) != record['digest'] or self.needs_key(record['snapshot']) != record['requires_desktop_key']:
                        raise ValueError('提议摘要或批准分级不一致')
                except (KeyError, TypeError) as error:
                    raise ValueError('专员审批历史无效') from error
            return deepcopy(records)

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
                    'requires_desktop_key': self.needs_key(snapshot)}
            item['digest'] = digest(item)
            entries = self.list()
            self.registry._write('proposals.json', {'version': 1, 'proposals': [item, *entries]})
            return deepcopy(item)

    def get(self, proposal_id):
        return next((x for x in self.list() if x['id'] == proposal_id), None) or self.registry._missing('提议不存在')

    def approval_message(self, item):
        state = self.key_info()
        key = state.get('key')
        if not key:
            raise ValueError('尚未登记 Desktop 批准钥匙')
        return (f"vortoone-staff-approval-v1\n{state['instance']}\n{key['id']}\n"
                f"{item['id']}\n{item['digest']}\n{item['nonce']}").encode()

    def _save(self, item):
        entries = self.list()
        self.registry._write('proposals.json', {'version': 1,
            'proposals': [deepcopy(item) if x['id'] == item['id'] else x for x in entries]})

    def _target(self, item):
        snapshot = item['snapshot']
        data = deepcopy(snapshot['data'])
        previous = snapshot['previous']
        if snapshot['kind'] == 'role':
            target = self.registry.validate_role(data)
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

    def decide(self, proposal_id, approve, expected_digest, *, owner_confirmation=False, signature=None):
        if type(approve) is not bool or not owner_confirmation:
            raise ValueError('需要主人明确批准或拒绝')
        with TASK_STATE_LOCK:
            item = self.get(proposal_id)
            if item['digest'] != expected_digest:
                raise ValueError('提议内容已经变化，请刷新')
            desired = 'approved' if approve else 'rejected'
            if item['status'] == desired:
                return item
            if item['status'] not in {'pending', 'applying'} or (item['status'] == 'applying' and not approve):
                raise ValueError('提议已经处理，不能改换决定')
            if item['status'] == 'pending':
                snapshot = item['snapshot']
                kind, data = snapshot['kind'], snapshot['data']
                if kind == 'member':
                    allowed = {'id', 'name', 'role', 'engine', 'model', 'department', 'limits', 'revision'}
                    data = {key: value for key, value in data.items() if key in allowed}
                elif kind == 'department':
                    data = {key: value for key, value in data.items() if key not in {'notes_revision', 'routines'}}
                if self.snapshot(kind, data) != snapshot:
                    raise ValueError('岗位或专员版本已变化，旧提议不能批准')
                if approve and item['requires_desktop_key']:
                    state = self.key_info()
                    key = state.get('key')
                    if not key or not signature:
                        raise ValueError('此提议必须在 Desktop 上使用批准钥匙')
                    _verify(key['public_key'], self.approval_message(item), signature)
                item['decision'] = {'at': datetime.now(timezone.utc).isoformat(), 'approve': approve,
                    'source': 'desktop_key' if approve and item['requires_desktop_key'] else 'owner',
                    **({'signature': signature, 'key_id': self.key_info()['key']['id']}
                       if approve and item['requires_desktop_key'] else {})}
                item['status'] = 'applying' if approve else 'rejected'
                if approve:
                    item['target'] = self._target(item)
                self._save(item)  # Durable owner receipt before any authority is written.
            if approve:
                if item['requires_desktop_key']:
                    key = self.key_info().get('key')
                    if not key or item['decision'].get('key_id') != key['id']:
                        raise ValueError('原批准钥匙已变化，不能恢复登记')
                    _verify(key['public_key'], self.approval_message(item), item['decision'].get('signature'))
                if self._target(item) != item['target']:
                    raise ValueError('批准后的登记目标已变化')
                snapshot = item['snapshot']
                entries = (self.registry.roles() if snapshot['kind'] == 'role' else self.registry.members()
                           if snapshot['kind'] == 'member' else self.registry.departments())
                current = next((x for x in entries if x['id'] == item['target']['id']), None)
                if current != item['target']:
                    if current != snapshot['previous']:
                        raise ValueError('登记已变化，保留批准历史，请重新提议')
                    if snapshot['kind'] == 'member' and self.registry.role(snapshot['data']['role']) != snapshot['role_snapshot']:
                        raise ValueError('所属岗位已变化，请重新提议')
                    if snapshot['kind'] == 'role':
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
            return deepcopy(item)
