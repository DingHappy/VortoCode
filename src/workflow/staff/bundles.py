"""New-registration bundles on the original proposal journal and registry files.

Until the final proposal receipt commits, ordinary readers hide new rows. Ordinary
writes preserve those hidden rows, so unrelated existing work can still progress.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import json
from pathlib import Path

from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.roles import builtin_roles

FILES = {'role': ('roles.json', 'roles'), 'member': ('members.json', 'members'),
         'department': ('departments.json', 'departments'), 'routine': ('routines.json', 'routines')}
_APPLYING = ContextVar('staff_bundle_registration', default=None)
_PREVIEW_AT = '1970-01-01T00:00:00+00:00'


def pending_bundles(registry):
    from src.workflow.staff.approvals import digest
    records = registry._read_raw('proposals.json', {'version': 1, 'proposals': []}).get('proposals')
    if not isinstance(records, list):
        raise ValueError('专员开通单历史无效')
    result = []
    for item in records:
        if not isinstance(item, dict):
            raise ValueError('专员提议历史无效')
        if not isinstance(item.get('snapshot'), dict):
            raise ValueError('专员提议快照无效')
        if item['snapshot'].get('kind') != 'bundle' or item.get('status') not in {'applying', 'cancelling'}:
            continue
        try:
            original = {key: item[key] for key in ('id', 'snapshot', 'owner', 'reason', 'created_at', 'nonce', 'approval_level', 'round')}
            original['status'] = 'pending'
            if digest(original) != item['digest'] or not isinstance(item['snapshot']['entries'], list):
                raise ValueError('开通单摘要已变化，登记不可用')
            for entry in item['snapshot']['entries']:
                if entry['kind'] not in FILES or entry['previous'] is not None or not isinstance(entry['data']['id'], str):
                    raise ValueError('开通单登记范围无效')
        except (KeyError, TypeError) as error:
            raise ValueError('开通单登记历史损坏，未开放部分登记') from error
        result.append(item)
    if len(result) > 1:
        raise ValueError('存在多个未完成的开通单，需先核对历史')
    return result


def hidden_ids(registry, name):
    result = set()
    for item in pending_bundles(registry):
        if _APPLYING.get() == (str(Path(registry.root).resolve()), item['id']):
            continue
        result.update(entry['data']['id'] for entry in item['snapshot']['entries'] if FILES[entry['kind']][0] == name)
    return result


def visible(registry, name, value):
    field = next((field for filename, field in FILES.values() if filename == name), None)
    if field is None:
        return value
    hidden = hidden_ids(registry, name)
    if hidden:
        if not isinstance(value.get(field), list) or any(not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in value[field]):
            raise ValueError('开通单涉及的登记文件损坏，未开放部分登记')
        value = deepcopy(value)
        value[field] = [row for row in value[field] if row['id'] not in hidden]
    return value


def preserve_pending(registry, name, value):
    field = next((field for filename, field in FILES.values() if filename == name), None)
    if field is None:
        return value
    hidden = hidden_ids(registry, name)
    if not hidden:
        return value
    if not isinstance(value.get(field), list) or any(not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in value[field]):
        raise ValueError('开通单涉及的登记写入无效')
    if any(row['id'] in hidden for row in value[field]):
        raise ValueError('同名登记属于未完成的开通单，请先完成或取消原单')
    raw = registry._read_raw(name, {'version': 1, field: []})
    value = deepcopy(value)
    value[field].extend(row for row in raw[field] if row['id'] in hidden)
    return value


@contextmanager
def _applying(registry, proposal_id):
    token = _APPLYING.set((str(Path(registry.root).resolve()), proposal_id))
    try:
        yield
    finally:
        _APPLYING.reset(token)


class _PreviewRegistry(StaffRegistry):
    """Use the same validators and registration methods, with memory-only writes."""
    def __init__(self, root):
        super().__init__(root)
        self.payloads = {}
        source = StaffRegistry(root)
        for name, field in FILES.values():
            default = ({'version': 1, 'roles': builtin_roles()} if field == 'roles' else
                {'version': 1, 'revision': 1, 'timezone': '', 'routines': [], 'migrations': []} if field == 'routines' else
                {'version': 1, field: []})
            self.payloads[name] = source._read(name, default)

    def _read(self, name, default):
        return deepcopy(self.payloads.get(name, default))

    def _write(self, name, value):
        if len(json.dumps(value, ensure_ascii=False).encode()) > 1024 * 1024:
            raise ValueError('开通单登记将超过文件大小上限')
        self.payloads[name] = deepcopy(value)


class StaffBundles:
    def __init__(self, registry):
        self.registry = registry

    def _build(self, data, *, at=_PREVIEW_AT, owner='owner'):
        from src.workflow.staff.approvals import StaffApprovals
        from src.workflow.staff.routines import Routines
        if (not isinstance(data, dict) or set(data) - {'role', 'member', 'department', 'routines'}
                or any(data.get(kind) is not None and not isinstance(data[kind], dict) for kind in ('role', 'member', 'department'))
                or not isinstance(data.get('routines', []), list) or len(data.get('routines', [])) > 4):
            raise ValueError('开通单最多包含一个岗位、一个专员、一个部门、四条例行工作')
        virtual = _PreviewRegistry(self.registry.root)
        approvals = StaffApprovals(self.registry.root); approvals.registry = virtual
        entries, ids = [], {kind: set() for kind in FILES}
        inputs = [(kind, data[kind]) for kind in ('role', 'member', 'department') if data.get(kind) is not None]
        inputs.extend(('routine', row) for row in data.get('routines', []))
        if not inputs:
            raise ValueError('开通单不能为空')
        for kind, definition in inputs:
            snapshot = approvals.snapshot(kind, definition)
            if snapshot['previous'] is not None or snapshot['data']['id'] in ids[kind] or definition.get('revision', 1) != 1:
                raise ValueError('开通单只用于新建登记，不能修改或重复已有登记')
            ids[kind].add(snapshot['data']['id'])
            entries.append(snapshot)
            if kind == 'role':
                if snapshot['data']['builtin']:
                    virtual.install_builtin(snapshot['data'])
                else:
                    virtual.register_role(snapshot['data'])
            elif kind == 'member':
                virtual.register_member({**snapshot['data'], 'approved_at': at})
            elif kind == 'department':
                virtual.register_department(snapshot['data'])
            else:
                routines = Routines(virtual.root, registry=virtual)
                target = routines.target(snapshot, at, owner)
                value = routines.read()
                if len(value['routines']) >= 256:
                    raise ValueError('例行工作记录已达上限')
                value['routines'].append({**target, 'state': {'cursor': at, 'task': None, 'error': ''}})
                virtual._write('routines.json', value)
        targets = {name: [row for row in virtual._read(name, {})[field] if row['id'] in ids[kind]]
                   for kind, (name, field) in FILES.items() if ids[kind]}
        return {'kind': 'bundle', 'data': deepcopy(data), 'previous': None, 'entries': entries}, targets

    def snapshot(self, data):
        return self._build(data)[0]

    def target(self, item):
        for pending in pending_bundles(self.registry):
            if pending['id'] != item['id']:
                raise ValueError('先完成或取消原开通单，再批准其它开通单')
        if self.snapshot(item['snapshot']['data']) != item['snapshot']:
            raise ValueError('开通单引用的登记或权限已变化，请重新核对')
        return self._build(item['snapshot']['data'], at=item['decision']['at'], owner=item['owner'])[1]

    def apply(self, item):
        if self.target(item) != item['target']:
            raise ValueError('开通单登记目标已变化')
        with _applying(self.registry, item['id']):
            for kind, (name, field) in FILES.items():
                if name not in item['target']:
                    continue
                default = {'version': 1, field: builtin_roles() if kind == 'role' else []}
                if kind == 'routine':
                    default = {'version': 1, 'revision': 1, 'timezone': '', 'routines': [], 'migrations': []}
                value = self.registry._read(name, default)
                for target in item['target'][name]:
                    current = next((x for x in value[field] if x['id'] == target['id']), None)
                    if current is not None and current != target:
                        raise ValueError('同名登记已变化，不能覆盖')
                    if current is None:
                        value[field].append(deepcopy(target))
                self.registry._write(name, value)

    def cancel(self, item):
        with _applying(self.registry, item['id']):
            for kind, (name, field) in FILES.items():
                ids = {entry['data']['id'] for entry in item['snapshot']['entries'] if entry['kind'] == kind}
                if not ids:
                    continue
                path = self.registry.directory / name
                if not path.exists():
                    continue
                value = self.registry._read_raw(name, {})
                for row in value[field]:
                    target = next((x for x in item['target'].get(name, []) if x['id'] == row['id']), None)
                    if row['id'] in ids and row != target:
                        raise ValueError('未完成的登记已被改写，不能直接取消，请核对')
                value[field] = [x for x in value[field] if x['id'] not in ids]
                self.registry._write(name, value)
