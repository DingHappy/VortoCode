"""File-backed staff roles. All writes share the existing task lock and replace atomically."""
from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy

from src.agents.trust import KINDS, INTERNAL, DELIVER, IRREVERSIBLE
from src.utils.state_dir import state_dir
from src.workflow.tasks.tasks import TASK_STATE_LOCK
from src.workflow.staff.roles import builtin_roles, canonical_roles, team_role_view

_ID = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')
_ENGINES = {'vortocode', 'opencode', 'deepseek-harness'}
_FIELDS = {'id', 'title', 'brief', 'grants', 'engine', 'max_steps', 'timeout_seconds', 'builtin', 'revision', 'enabled'}
_FORBIDDEN_TOOLS = {'staff_assign', 'staff_propose', 'routine_propose', 'task', 'research_parallel',
    'team_propose', 'team_assign', 'task_followup', 'task_release', 'cron_add', 'cron_run', 'cron_toggle',
    'cron_set_web', 'cron_set_prompt', 'send_file', 'send_image', 'send_document', 'open_pr', 'pr_fix',
    'publish_artifact', 'delete_artifact', 'run_command', 'stop_command', 'review_memory_proposal'}
_FILE_TOOLS = {'read_file', 'list_files', 'glob', 'grep', 'find_definition', 'find_references',
               'document_symbols', 'git_status', 'list_branches', 'show_diff', 'edit_file', 'write_file', 'run_tests'}
_SOURCE_TOOLS = {'memory': {'recall_memory', 'save_memory', 'list_memory_proposals', 'review_memory_proposal', 'remember_repo'},
                 'conversation': {'search_conversations', 'read_conversation_excerpt'},
                 'goals': {'personal_goals', 'personal_goal_create', 'personal_goal_update'}}


def tool_catalog(root: str) -> dict:
    """Use real tool declarations; never trust a role's claimed category or remote hints."""
    from src.agents.gate import make_confirm_gate
    from src.agents.tools.files import build_read_tools, build_write_tools, build_test_tool
    from src.agents.tools.web import build_web_tools
    from src.agents.tools.memory import build_memory_tools
    from src.agents.tools.personal_goals import build_personal_goal_tools
    from src.plugins import mail, caldav, calendar_ics, notion
    from src.agents.tools.deliveries import build_delivery_reader
    from src.scheduling.assistant_watch import build_status_tools
    gate = make_confirm_gate()
    tools = (build_read_tools(root) + build_write_tools(root) + [build_test_tool(root)] + build_web_tools()
             + build_memory_tools(root, gate) + build_personal_goal_tools(root, confirm=gate)
             + mail.build_tools() + mail.build_write_tools(gate) + caldav.build_write_tools(gate)
             + calendar_ics.build_tools() + notion.build_tools() + [build_delivery_reader()] + build_status_tools(root))
    catalog = {tool.name: tool for tool in tools}
    if 'list_dispatches' not in catalog:
        from src.agents.tool import Tool
        async def unavailable(_args):
            return '没有登记可读的项目任务单；未读取项目文件或保留的旧任务单。'
        catalog['list_dispatches'] = Tool('list_dispatches', '读取已登记项目的任务单状态；未登记时没有可读内容。',
            {}, unavailable, operation_kind='read', untrusted_source=True)
    return catalog


def _strings(value, label, *, empty=True):
    if not isinstance(value, list) or (not empty and not value) or any(not isinstance(x, str) or not x for x in value):
        raise ValueError(f'{label} 必须是字符串列表')
    if len(value) != len(set(value)):
        raise ValueError(f'{label} 不能重复')
    return list(value)


class StaffRegistry:
    def __init__(self, root: str):
        self.root = str(root)
        self.directory = state_dir(root) / 'staff'

    def tool_catalog(self) -> dict:
        return tool_catalog(self.root)

    def _read(self, name: str, default: dict) -> dict:
        path = self.directory / name
        if self.directory.is_symlink():
            raise ValueError('专员登记目录不能是符号链接')
        if not path.exists():
            return deepcopy(default)
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            raise ValueError('专员登记文件无效或过大')
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (UnicodeError, ValueError) as error:
            raise ValueError('专员登记文件损坏，未恢复默认权限') from error
        if not isinstance(value, dict) or value.get('version') != 1:
            raise ValueError('专员登记文件版本无效')
        return value

    def _write(self, name: str, value: dict) -> None:
        if self.directory.is_symlink():
            raise ValueError('专员登记目录不能是符号链接')
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / name
        if path.is_symlink():
            raise ValueError('专员登记文件不能是符号链接')
        temporary = self.directory / f'.{name}.{uuid.uuid4().hex}.tmp'
        try:
            encoded = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
            if len(encoded.encode('utf-8')) > 1024 * 1024:
                raise ValueError('专员登记文件过大，未写入')
            temporary.write_text(encoded, encoding='utf-8')
            temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def validate_role(self, data: dict, *, builtin=False, catalog=None) -> dict:
        if not isinstance(data, dict) or set(data) - _FIELDS:
            raise ValueError('岗位包含未知字段')
        item = deepcopy(data)
        if not isinstance(item.get('id'), str) or not _ID.fullmatch(item['id']):
            raise ValueError('岗位 id 无效')
        for name in ('title', 'brief'):
            if not isinstance(item.get(name), str) or not item[name].strip():
                raise ValueError(f'岗位 {name} 不能为空')
        for name in ('max_steps', 'timeout_seconds'):
            if type(item.get(name)) is not int or item[name] <= 0:
                raise ValueError(f'{name} 必须是正整数')
        engines = _strings(item.get('engine'), 'engine', empty=False)
        if not set(engines) <= _ENGINES:
            raise ValueError('未知执行框架')
        if item.get('builtin', False) and not builtin:
            raise ValueError('不能改写或伪造内置岗位')
        if not builtin and item['id'] in {x['id'] for x in canonical_roles()}:
            raise ValueError('不能占用内置岗位的保留名称')
        grants = item.get('grants')
        if not isinstance(grants, dict) or set(grants) != {'operations', 'tools', 'sees', 'web', 'project'}:
            raise ValueError('grants 必须显式声明全部权限字段')
        operations = _strings(grants['operations'], 'operations', empty=False)
        tools = _strings(grants['tools'], 'tools')
        sees = _strings(grants['sees'], 'sees')
        if not set(operations) <= set(KINDS) or set(operations) & {INTERNAL, DELIVER, IRREVERSIBLE}:
            raise ValueError('5.5：禁止专员获得主人保留动作或内部框架权限')
        if type(grants['web']) is not bool or type(grants['project']) is not bool:
            raise ValueError('web/project 必须是布尔值')
        if any(x not in {'memory', 'goals', 'conversation', 'plugin:mail', 'plugin:notion', 'plugin:calendar', 'plugin:caldav'}
               and not (x.startswith('files:') and _ID.fullmatch(x[6:])) for x in sees):
            raise ValueError('未知私人来源')
        private = bool(sees)
        external = grants['web'] or bool(set(sees) & {'plugin:mail', 'plugin:notion', 'plugin:calendar', 'plugin:caldav'})
        consequential = bool(set(operations) & {'deliver', 'interact', 'execute', 'irreversible'})
        if private and external and consequential:
            raise ValueError('5.2.1：私人资料、外部内容和外部影响不能同时授权')
        if grants['web'] and private:
            raise ValueError('5.2.2：联网岗位不能看私人资料')
        if private and external and not set(operations) <= {'read', 'draft', 'own_data'}:
            raise ValueError('5.2.3：此组合只能读、起草或改主人自己的数据')
        declared = catalog if catalog is not None else self.tool_catalog()
        for name in tools:
            if name in _FORBIDDEN_TOOLS or name.startswith(('browser_', 'desktop_', 'android_', 'mcp__')):
                raise ValueError(f'5.5：禁止岗位使用 {name}')
            if name not in declared:
                raise ValueError(f'未知岗位工具：{name}')
            tool = declared[name]
            private_status = {'recent_notices': {'memory', 'conversation', 'goals'}, 'today_activity': {'conversation'},
                              'schedule_list': {'conversation', 'goals'}, 'schedule_history': {'conversation'}, 'list_dispatches': {'conversation'}}
            if name in private_status and not private_status[name] <= set(sees):
                raise ValueError(f'工具 {name} 缺少私人状态来源授权')
            if not {tool.operation_kind, *tool.possible_operations} <= set(operations):
                raise ValueError(f'工具 {name} 的操作类别超出岗位')
            needed = next((f'plugin:{plugin}' for plugin, prefix in [('mail', 'mail_'), ('notion', 'notion_'),
                          ('calendar', 'calendar_')] if name.startswith(prefix)), None)
            if needed and needed not in sees and not (needed == 'plugin:calendar' and 'plugin:caldav' in sees):
                raise ValueError(f'工具 {name} 缺少私人来源授权')
            source = next((key for key, names in _SOURCE_TOOLS.items() if name in names), None)
            if source and source not in sees:
                raise ValueError(f'工具 {name} 缺少私人来源授权')
            if name in _FILE_TOOLS and not (grants['project'] or any(x.startswith('files:') for x in sees)):
                raise ValueError(f'工具 {name} 缺少登记文件来源')
            if name.startswith('web_') and not grants['web']:
                raise ValueError('联网工具缺少 web 授权')
        item.update(builtin=builtin, enabled=item.get('enabled', True), revision=item.get('revision', 1))
        if type(item['enabled']) is not bool or type(item['revision']) is not int or item['revision'] <= 0:
            raise ValueError('岗位状态或版本无效')
        return item

    def roles(self) -> list[dict]:
        with TASK_STATE_LOCK:
            missing = not (self.directory / 'roles.json').exists()
            payload = self._read('roles.json', {'version': 1, 'roles': builtin_roles()})
            records = payload.get('roles')
            if not isinstance(records, list):
                raise ValueError('岗位登记格式无效')
            builtins = {x['id']: x for x in canonical_roles()}
            catalog = self.tool_catalog()
            output, ids = [], set()
            for row in records:
                if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] in ids:
                    raise ValueError('岗位重复或格式无效')
                expected = builtins.get(row.get('id'))
                legacy = next((x for x in builtin_roles(legacy=True) if x['id'] == row['id']), None)
                matches = lambda target: target is not None and all(row.get(k) == v for k, v in target.items() if k not in {'enabled', 'revision'})
                if expected and not (matches(expected) or matches(legacy)):
                    raise ValueError('内置岗位被改写')
                item = self.validate_role(row, builtin=expected is not None, catalog=catalog)
                output.append(item)
                ids.add(item['id'])
            if not {x['id'] for x in builtin_roles()} <= ids:
                raise ValueError('内置岗位缺失，未自动恢复权限')
            if missing:
                self._write('roles.json', {'version': 1, 'roles': output})
            return output

    def role(self, role_id: str) -> dict:
        return next((x for x in self.roles() if x['id'] == role_id), None) or self._missing('岗位不存在')

    @staticmethod
    def _missing(message):
        raise ValueError(message)

    def register_role(self, data: dict) -> dict:
        if not isinstance(data, dict):
            raise ValueError('岗位必须是对象')
        with TASK_STATE_LOCK:
            entries = self.roles()
            previous = next((x for x in entries if x['id'] == data.get('id')), None)
            if previous and previous['builtin']:
                raise ValueError('内置岗位不能改写，只能停用')
            if previous and data.get('revision') != previous['revision']:
                raise ValueError('岗位版本已变化，请重新核对')
            item = self.validate_role(data)
            item['revision'] = previous['revision'] + 1 if previous else 1
            from src.workflow.staff.departments import Departments
            from src.workflow.staff.notes import StaffNotes
            departments = Departments(self)
            for department in departments.list():
                if department['status'] != 'retired':
                    departments.check_notes(department, StaffNotes(self.root).snapshot(department['id']), replacement=item)
            entries = [item if x['id'] == item['id'] else x for x in entries] if previous else [*entries, item]
            self._write('roles.json', {'version': 1, 'roles': entries})
            return deepcopy(item)

    def upgrade_builtin(self, data):
        """Only the explicit owner-approved proposal path calls this shipped-definition upgrade."""
        with TASK_STATE_LOCK:
            entries = self.roles()
            previous = self.role(data['id'])
            canonical = next((x for x in canonical_roles() if x['id'] == data['id']), None)
            target = {**(canonical or {}), 'enabled': previous['enabled'], 'revision': previous['revision']}
            if not previous['builtin'] or data != target:
                raise ValueError('内置岗位升级只能使用当前发布的完整定义和原版本')
            item = {**target, 'revision': previous['revision'] + 1}
            self._write('roles.json', {'version': 1, 'roles': [item if x['id'] == item['id'] else x for x in entries]})
            return deepcopy(item)

    def set_role_enabled(self, role_id: str, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise ValueError('enabled 必须是布尔值')
        with TASK_STATE_LOCK:
            entries = self.roles()
            item = next((x for x in entries if x['id'] == role_id), None)
            if item is None:
                raise ValueError('岗位不存在')
            item.update(enabled=enabled, revision=item['revision'] + 1)
            self._write('roles.json', {'version': 1, 'roles': entries})
            return deepcopy(item)

    def team_roles(self) -> dict:
        return team_role_view(self.roles())

    def validate_member(self, data: dict) -> dict:
        from datetime import datetime, timezone
        fields = {'id', 'name', 'role', 'engine', 'model', 'department', 'status', 'limits',
                  'created_by', 'approved_at', 'revision'}
        if not isinstance(data, dict) or set(data) - fields:
            raise ValueError('专员包含未知字段或权限字段')
        item = deepcopy(data)
        if not isinstance(item.get('id'), str) or not _ID.fullmatch(item['id']):
            raise ValueError('专员 id 无效')
        if not isinstance(item.get('name'), str) or not item['name'].strip() or len(item['name']) > 80:
            raise ValueError('专员名称无效')
        assigned = self.role(item.get('role'))
        if not assigned['enabled']:
            raise ValueError('岗位已停用')
        if item.get('engine') not in assigned['engine']:
            raise ValueError('执行框架不在岗位允许范围内')
        # D10 applies to new staff; S1 only preserves the old team executor choices.
        if item['engine'] != 'vortocode' and assigned['id'] != 'developer':
            raise ValueError('D10：新专员只有开发岗允许外部框架')
        if not isinstance(item.get('model', ''), str) or len(item.get('model', '')) > 160:
            raise ValueError('模型名称无效')
        from src.workflow.staff.departments import Departments
        department = Departments(self).membership(item['id'])
        if item.get('department', '') not in {'', department}:
            raise ValueError('部门成员由已批准的部门登记管理，请提议调整部门')
        limits = item.get('limits', {'parallel': 1, 'daily_tokens': 0})
        if (not isinstance(limits, dict) or set(limits) != {'parallel', 'daily_tokens'}
                or type(limits['parallel']) is not int or limits['parallel'] < 1
                or type(limits['daily_tokens']) is not int or limits['daily_tokens'] < 0):
            raise ValueError('专员 parallel 必须为正整数；每日额度为非负整数')
        if item.get('status', 'active') not in {'active', 'paused', 'retired'}:
            raise ValueError('专员状态无效')
        item.update(model=item.get('model', ''), department=department, status=item.get('status', 'active'),
                    limits=limits, revision=item.get('revision', 1), created_by='owner',
                    approved_at=item.get('approved_at') or datetime.now(timezone.utc).isoformat())
        if type(item['revision']) is not int or item['revision'] < 1:
            raise ValueError('专员版本无效')
        return item

    def members(self) -> list[dict]:
        with TASK_STATE_LOCK:
            payload = self._read('members.json', {'version': 1, 'members': []})
            records = payload.get('members')
            if not isinstance(records, list) or any(not isinstance(x, dict) for x in records):
                raise ValueError('专员登记格式无效')
            ids = [x.get('id') for x in records]
            if any(not isinstance(x, str) or not _ID.fullmatch(x) for x in ids) or len(ids) != len(set(ids)):
                raise ValueError('专员登记重复或无效')
            from src.workflow.staff.departments import Departments
            result = deepcopy(records)
            for item in result:
                item['department'] = Departments(self).membership(item['id'])
            return result

    def departments(self):
        from src.workflow.staff.departments import Departments
        return Departments(self).list()

    def department(self, department_id):
        from src.workflow.staff.departments import Departments
        return Departments(self).get(department_id)

    def register_department(self, data):
        from src.workflow.staff.departments import Departments
        return Departments(self).register(data)

    def member(self, member_id: str) -> dict:
        return next((x for x in self.members() if x['id'] == member_id), None) or self._missing('专员不存在')

    def register_member(self, data: dict) -> dict:
        """Trusted owner-approved registration; model tools only submit proposals (S3)."""
        with TASK_STATE_LOCK:
            entries = self.members()
            previous = next((x for x in entries if x['id'] == data.get('id')), None)
            if previous and (previous['status'] == 'retired' or data.get('revision') != previous['revision']):
                raise ValueError('专员已撤掉或版本变化')
            item = self.validate_member(data)
            item['revision'] = previous['revision'] + 1 if previous else 1
            if previous and previous['department'] and item['role'] != previous['role']:
                raise ValueError('先在部门提议中移出专员，再批准更换岗位；不能绕过笔记资料边界')
            entries = [item if x['id'] == item['id'] else x for x in entries] if previous else [*entries, item]
            self._write('members.json', {'version': 1, 'members': entries})
            return deepcopy(item)

    def set_member_status(self, member_id: str, status: str, revision: int) -> dict:
        if status not in {'active', 'paused', 'retired'} or type(revision) is not int:
            raise ValueError('专员状态或版本无效')
        with TASK_STATE_LOCK:
            entries = self.members()
            item = next((x for x in entries if x['id'] == member_id), None)
            if item is None or item['revision'] != revision or item['status'] == 'retired':
                raise ValueError('专员已撤掉或版本变化，请刷新')
            if status == 'active' and not self.role(item['role'])['enabled']:
                raise ValueError('岗位已停用，不能恢复专员')
            item.update(status=status, revision=revision + 1)
            self._write('members.json', {'version': 1, 'members': entries})
            return deepcopy(item)
