"""File-backed staff roles. All writes share the existing task lock and replace atomically."""
from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy

from src.agents.trust import KINDS, INTERNAL, DELIVER, IRREVERSIBLE
from src.utils.state_dir import state_dir
from src.workflow.tasks.tasks import TASK_STATE_LOCK
from src.workflow.staff.roles import builtin_roles, team_role_view

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
    gate = make_confirm_gate()
    tools = (build_read_tools(root) + build_write_tools(root) + [build_test_tool(root)] + build_web_tools()
             + build_memory_tools(root, gate) + build_personal_goal_tools(root, confirm=gate)
             + mail.build_tools() + mail.build_write_tools(gate) + caldav.build_write_tools(gate)
             + calendar_ics.build_tools() + notion.build_tools())
    return {tool.name: tool for tool in tools}


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
            builtins = {x['id']: x for x in builtin_roles()}
            catalog = self.tool_catalog()
            output, ids = [], set()
            for row in records:
                if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] in ids:
                    raise ValueError('岗位重复或格式无效')
                expected = builtins.get(row.get('id'))
                if expected and any(row.get(k) != v for k, v in expected.items() if k not in {'enabled', 'revision'}):
                    raise ValueError('内置岗位被改写')
                item = self.validate_role(row, builtin=expected is not None, catalog=catalog)
                output.append(item)
                ids.add(item['id'])
            if not set(builtins) <= ids:
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
            entries = [item if x['id'] == item['id'] else x for x in entries] if previous else [*entries, item]
            self._write('roles.json', {'version': 1, 'roles': entries})
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
