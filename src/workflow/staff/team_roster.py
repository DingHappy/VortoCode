"""Registered and temporary team identities, frozen at the original owner decision."""
from copy import deepcopy

from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.notes import StaffNotes
from src.workflow.tasks.errors import CollaborationConflict

REASONS = {'separate_permissions', 'independent_review', 'parallel'}


def normalize(root, members, mode, department='', reason=''):
    from src.workflow.tasks.tasks import TASK_STATE_LOCK
    with TASK_STATE_LOCK:
        return _normalize(root, members, mode, department, reason)


def _normalize(root, members, mode, department, reason):
    if not isinstance(reason, str) or reason not in REASONS:
        raise CollaborationConflict('开团队须说明：权限隔离、独立审查或明确并行；单人工作请用 staff_assign')
    if not isinstance(members, list) or not 1 <= len(members) <= 6:
        raise CollaborationConflict('团队成员需要 2～6 人')
    registry, output, names, identities = StaffRegistry(root), [], set(), set()
    for value in members:
        value = {'member': value} if isinstance(value, str) else value
        if not isinstance(value, dict):
            raise CollaborationConflict('成员格式无效')
        if 'member' in value:
            if set(value) != {'member'}:
                raise CollaborationConflict('引用专员不能覆盖岗位、执行框架或附带权限字段')
            member = registry.validate_member(registry.member(value['member']))
            if member['status'] != 'active':
                raise CollaborationConflict('专员已暂停或撤掉')
            role = registry.role(member['role'])
            entry = {'member': member['id'], 'member_snapshot': member, 'name': member['name'],
                     'engine': member['engine'], 'model': member['model'], 'department': member['department']}
            if member['id'] in identities:
                raise CollaborationConflict('同一个专员不能重复编入团队')
            identities.add(member['id'])
        else:
            if set(value) - {'name', 'role', 'engine', 'model'}:
                raise CollaborationConflict('临时成员包含未知字段或权限字段')
            role = registry.role(value.get('role'))
            if not role['builtin']:
                raise CollaborationConflict('临时成员只能引用内置岗位；自定义岗位先登记专员')
            name = value.get('name')
            if not isinstance(name, str) or not name.strip() or len(name) > 80:
                raise CollaborationConflict('临时成员名称无效')
            engine, model = value.get('engine', 'vortocode'), value.get('model', '')
            if engine not in role['engine'] or engine != 'vortocode' and role['id'] != 'developer':
                raise CollaborationConflict('D10：新团队只有开发岗允许外部框架')
            if not isinstance(model, str) or len(model) > 160:
                raise CollaborationConflict('模型名称无效')
            entry = {'member': '', 'member_snapshot': None, 'name': name.strip(), 'engine': engine,
                     'model': model, 'department': department}
        if not role['enabled'] or entry['name'] in names:
            raise CollaborationConflict('岗位已停用或成员名重复')
        if (role['grants']['sees'] or role['grants']['project']) and mode != 'private':
            raise CollaborationConflict('有私人来源或项目的团队须为 private')
        if role['grants']['web'] and mode == 'private':
            raise CollaborationConflict('私人资料与联网成员不能同队')
        if any(x.startswith('files:') for x in role['grants']['sees']):
            raise CollaborationConflict('登记目录来源尚未接入，不能用主工作区代替')
        if role['grants']['project'] and role['id'] != 'developer':
            raise CollaborationConflict('本步只复用内置开发岗的既有项目副本流程')
        names.add(entry['name'])
        notes = None
        if entry['department']:
            assigned = registry.department(entry['department'])
            if assigned['status'] != 'active':
                raise CollaborationConflict('成员所属部门已暂停')
            notes = StaffNotes(root).snapshot(assigned['id'])
            if set(notes['sources']) - set(role['grants']['sees']):
                raise CollaborationConflict('部门笔记超出成员岗位来源')
        output.append({**entry, 'role': role['id'], 'role_snapshot': role, 'notes': notes})
    if len(output) < 2:
        raise CollaborationConflict('团队至少需要两个成员；单人工作请用 staff_assign')
    coordinator, notes = {'kind': 'main_agent', 'model': ''}, None
    if department:
        assigned = registry.department(department)
        if assigned['status'] != 'active':
            raise CollaborationConflict('部门已暂停')
        if any(x['member'] and x['department'] != department for x in output):
            raise CollaborationConflict('部门团队的常设成员须属于该部门；跨部门团队不附属单一部门')
        notes = StaffNotes(root).snapshot(department)
        lead = assigned['lead']
        if lead:
            member = registry.member(lead)
            role = registry.role(member['role'])
            if member['status'] != 'active' or not role['enabled']:
                raise CollaborationConflict('部门协调人已暂停')
            coordinator = {'kind': 'staff_lead', 'member': lead, 'member_snapshot': member,
                           'role_snapshot': role, 'model': member['model']}
    return {'members': output, 'department': department, 'department_snapshot': registry.department(department) if department else None,
            'coordinator': coordinator, 'notes': notes, 'reason': reason}


def identity(root, entry):
    member = deepcopy(entry['member_snapshot']) if entry['member'] else {
        'id': root.id + '-member-' + str(root.team['members'].index(entry)), 'name': entry['name'],
        'role': entry['role'], 'engine': entry['engine'], 'model': entry['model'], 'department': entry['department'],
        'status': 'active', 'revision': 1, 'limits': {'parallel': 1, 'daily_tokens': 0}}
    member['team_task'] = root.id
    return member, deepcopy(entry['role_snapshot'])


def check(root_path, root, member, role):
    if root.status not in {'active', 'waiting'} or not root.team.get('staff_roster'):
        raise CollaborationConflict('团队已停止或不在执行阶段')
    entry = next((x for x in root.team['members'] if x['name'] == member['name']), None)
    if entry is None or identity(root, entry) != (member, role):
        raise CollaborationConflict('任务成员快照不匹配')
    registry = StaffRegistry(root_path)
    if registry.role(entry['role']) != role or not role['enabled']:
        raise CollaborationConflict('岗位版本已变化，请重新批准组队')
    if entry['member'] and registry.member(entry['member']) != entry['member_snapshot']:
        raise CollaborationConflict('专员版本或部门已变化，请重新批准组队')
    if entry['department'] and registry.department(entry['department'])['status'] != 'active':
        raise CollaborationConflict('部门已暂停')


def is_managed(task):
    dispatch = task.collaboration.get('dispatch', {})
    return dispatch.get('source') == 'staff' or (dispatch.get('source') == 'team' and dispatch.get('staff_roster') is True
        and task.owner_session == 'team-' + str(dispatch.get('root', '')))
