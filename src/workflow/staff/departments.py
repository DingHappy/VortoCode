"""Owner-approved departments; membership has one authority in departments.json."""
from copy import deepcopy

from src.workflow.tasks.tasks import TASK_STATE_LOCK


class Departments:
    def __init__(self, registry):
        self.registry = registry

    def list(self):
        records = self.registry._read('departments.json', {'version': 1, 'departments': []}).get('departments')
        if not isinstance(records, list) or any(not isinstance(x, dict) for x in records):
            raise ValueError('部门登记损坏')
        ids = [x.get('id') for x in records]
        if len(ids) != len(set(ids)) or any(not isinstance(x, str) or not x.startswith('dept-') for x in ids):
            raise ValueError('部门登记重复或无效')
        return deepcopy(records)

    def get(self, department_id):
        return next((x for x in self.list() if x['id'] == department_id), None) or self.registry._missing('部门不存在')

    def membership(self, member_id):
        own = [x['id'] for x in self.list() if x['status'] != 'retired' and member_id in x['members']]
        if len(own) > 1:
            raise ValueError('同一专员不能同时属于多个部门')
        return own[0] if own else ''

    def roles(self, department, *, replacement=None):
        result = []
        for member_id in department['members']:
            member = self.registry.member(member_id)
            if member['status'] == 'retired':
                raise ValueError('部门不能包含已撤掉的专员')
            assigned = replacement if replacement and replacement['id'] == member['role'] else self.registry.role(member['role'])
            result.append(assigned)
        return result

    def check_notes(self, department, notes, *, replacement=None):
        roles = self.roles(department, replacement=replacement)
        private = any(role['grants']['sees'] for role in roles)
        if private and any(role['grants']['web'] or not role['grants']['sees'] for role in roles):
            raise ValueError('部门资料边界不兼容：私人来源不能与联网或无私人来源岗位共享笔记')
        for role in roles:
            if not set(notes.get('sources', [])) <= set(role['grants']['sees']):
                raise ValueError('部门笔记来源超出成员的登记岗位权限')
            if private and notes.get('tainted') and not set(role['grants']['operations']) <= {'read', 'draft', 'own_data'}:
                raise ValueError('5.2：私人外部笔记只能交给读、草稿或自有数据岗位')
        return private

    def validate(self, data):
        from src.workflow.staff.registry import _ID, _strings
        fields = {'id', 'name', 'duty', 'members', 'lead', 'limits', 'status', 'revision', 'notes_revision', 'routines'}
        if not isinstance(data, dict) or set(data) - fields:
            raise ValueError('部门包含未知字段或权限字段')
        value = deepcopy(data)
        if not isinstance(value.get('id'), str) or not _ID.fullmatch(value['id']) or not value['id'].startswith('dept-'):
            raise ValueError('部门 id 必须以 dept- 开头')
        for field, limit in [('name', 80), ('duty', 2000)]:
            if not isinstance(value.get(field), str) or not value[field].strip() or len(value[field]) > limit:
                raise ValueError('部门名称或职责无效')
        members = _strings(value.get('members'), 'members')
        if len(members) > 6:
            raise ValueError('D8：每个部门最多 6 位专员')
        if value.get('lead', '') and value['lead'] not in members:
            raise ValueError('协调人必须来自部门成员；团队派活留在 S6')
        for member in members:
            own = self.membership(member)
            if own and own != value['id']:
                raise ValueError('专员已属于其它部门，先批准原部门的成员调整')
        limits = value.get('limits', {'parallel': 2, 'daily_tokens': 0})
        if (not isinstance(limits, dict) or set(limits) != {'parallel', 'daily_tokens'}
                or type(limits['parallel']) is not int or limits['parallel'] < 1
                or type(limits['daily_tokens']) is not int or limits['daily_tokens'] < 0):
            raise ValueError('部门并发必须为正整数；每日 token 额度为非负整数，0 表示不限')
        if value.get('routines'):
            raise ValueError('例行工作在 S7 接入')
        if value.get('status', 'active') not in {'active', 'paused', 'retired'}:
            raise ValueError('部门状态无效')
        if type(value.get('revision', 1)) is not int or value.get('revision', 1) < 1:
            raise ValueError('部门版本无效')
        value.update(lead=value.get('lead', ''), limits=limits, status=value.get('status', 'active'),
                     revision=value.get('revision', 1), notes_revision=0, routines=[])
        from src.workflow.staff.notes import StaffNotes
        notes = StaffNotes(self.registry.root).snapshot(value['id'])
        self.check_notes(value, notes)
        return value

    def register(self, data):
        with TASK_STATE_LOCK:
            records = self.list()
            previous = next((x for x in records if x['id'] == data.get('id')), None)
            if previous and (previous['status'] == 'retired' or data.get('revision') != previous['revision']):
                raise ValueError('部门已撤掉或版本变化')
            item = self.validate(data)
            if not previous and item['status'] != 'retired' and sum(x['status'] != 'retired' for x in records) >= 8:
                raise ValueError('D8：最多 8 个部门')
            item['revision'] = previous['revision'] + 1 if previous else 1
            records = [item if x['id'] == item['id'] else x for x in records] if previous else [*records, item]
            self.registry._write('departments.json', {'version': 1, 'departments': records})
            return deepcopy(item)
