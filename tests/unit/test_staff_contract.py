"""Staff design section 10: role grants (1/2); dispatch contracts added in S2."""
from copy import deepcopy
import json

import pytest


def role(**grants):
    return {'id': 'reader', 'title': '阅读员', 'brief': '整理交给它的材料。',
            'grants': {'operations': ['read'], 'tools': [], 'sees': [], 'web': False, 'project': False, **grants},
            'engine': ['vortocode'], 'max_steps': 12, 'timeout_seconds': 600}


def test_builtin_roles_are_persisted_and_obey_the_operation_contract(tmp_path):
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(tmp_path))
    entries = registry.roles()
    assert {item['id'] for item in entries} == {'web-researcher', 'reviewer', 'developer'}
    catalog = registry.tool_catalog()
    for item in entries:
        assert item['builtin'] and item['revision'] == 1
        registry.validate_role(item, builtin=True)
        for name in item['grants']['tools']:
            tool = catalog[name]
            assert {tool.operation_kind, *tool.possible_operations} <= set(item['grants']['operations'])
    path = tmp_path / '.vortoone' / 'staff' / 'roles.json'
    assert json.loads(path.read_text())['roles'] == entries


@pytest.mark.parametrize('grants,rule', [
    ({'operations': ['read', 'execute'], 'sees': ['plugin:mail']}, '5.2.1'),
    ({'web': True, 'sees': ['memory']}, '5.2.2'),
    ({'operations': ['read', 'write'], 'sees': ['plugin:notion']}, '5.2.3'),
])
def test_bad_combinations_cannot_be_registered(tmp_path, grants, rule):
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(tmp_path))
    original = registry.roles()
    with pytest.raises(ValueError, match=rule):
        registry.register_role(role(**grants))
    assert registry.roles() == original


@pytest.mark.parametrize('operations', [['deliver'], ['irreversible'], ['payment'], ['transfer'], ['delete'], ['security']])
def test_owner_only_actions_cannot_be_granted(tmp_path, operations):
    from src.workflow.staff.registry import StaffRegistry
    with pytest.raises(ValueError):
        StaffRegistry(str(tmp_path)).register_role(role(operations=operations))


@pytest.mark.parametrize('name', ['staff_assign', 'staff_propose', 'routine_propose', 'cron_add',
                                  'task', 'send_file', 'open_pr', 'delete_artifact', 'run_command', 'browser_click'])
def test_roles_cannot_acquire_dispatch_or_owner_only_tools(tmp_path, name):
    from src.workflow.staff.registry import StaffRegistry
    with pytest.raises(ValueError):
        StaffRegistry(str(tmp_path)).register_role(role(tools=[name]))


def test_tool_categories_and_private_sources_are_checked(tmp_path):
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(tmp_path))
    with pytest.raises(ValueError, match='类别'):
        registry.register_role(role(tools=['mail_draft'], sees=['plugin:mail']))
    with pytest.raises(ValueError, match='来源'):
        registry.register_role(role(tools=['mail_read']))
    accepted = registry.register_role(role(operations=['read', 'draft'], tools=['mail_read', 'mail_draft'], sees=['plugin:mail']))
    assert accepted['grants']['operations'] == ['read', 'draft']


def test_builtin_definitions_are_immutable_and_custom_updates_are_revisioned(tmp_path):
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(tmp_path))
    builtin = next(item for item in registry.roles() if item['id'] == 'reviewer')
    changed = deepcopy(builtin)
    changed['grants']['web'] = True
    with pytest.raises(ValueError, match='内置'):
        registry.register_role(changed)
    item = registry.register_role(role())
    item['brief'] = '逐条列出证据。'
    assert registry.register_role(item)['revision'] == 2
    assert registry.set_role_enabled('reviewer', False)['enabled'] is False
    assert 'reviewer' not in registry.team_roles()


def test_failed_atomic_save_and_corrupt_file_do_not_restore_default_permissions(tmp_path, monkeypatch):
    from src.workflow.staff.registry import StaffRegistry
    from pathlib import Path
    registry = StaffRegistry(str(tmp_path))
    registry.roles()
    path = tmp_path / '.vortoone' / 'staff' / 'roles.json'
    before = path.read_bytes()
    original = Path.replace
    def fail(source, target):
        if Path(target) == path:
            raise OSError('synthetic failure')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'replace', fail)
        with pytest.raises(OSError):
            registry.register_role(role())
    assert path.read_bytes() == before
    path.write_text('{broken')
    with pytest.raises(ValueError):
        registry.roles()


def test_calendar_reading_is_available_but_combined_writing_waits_for_later_design(tmp_path):
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(tmp_path))
    calendar = role(tools=['calendar_events'], sees=['plugin:calendar'])
    assert registry.register_role(calendar)['grants']['operations'] == ['read']
    for operations in [['read', 'write'], ['read', 'write', 'interact']]:
        with pytest.raises(ValueError, match='5.2'):
            registry.register_role({**role(operations=operations, tools=['calendar_events', 'calendar_add'], sees=['plugin:calendar']), 'id': 'calendar-writer'})


def test_team_compatibility_view_keeps_existing_faces_and_execution_choices(tmp_path):
    from src.workflow.staff.registry import StaffRegistry
    from src.workflow.tasks.team import ROLES, TeamService
    expected = {
        'web-researcher': {'title': '研究员', 'tools': 'web_read', 'web': True, 'max_steps': 12, 'timeout_seconds': 600},
        'reviewer': {'title': '审查员', 'tools': 'none', 'web': False, 'max_steps': 2, 'timeout_seconds': 600},
        'developer': {'title': '开发', 'tools': 'code_write', 'web': False, 'max_steps': 40, 'timeout_seconds': 1800, 'engines': ('opencode',)},
    }
    assert ROLES == StaffRegistry(str(tmp_path)).team_roles() == expected
    service = TeamService(str(tmp_path))
    team = service.create('owner', objective='研究', acceptance=['来源'], members=[{'name': '研究员', 'role': 'web-researcher', 'engine': 'opencode'}])
    assert team.team['members'][0]['engine'] == 'opencode'
    StaffRegistry(str(tmp_path)).set_role_enabled('reviewer', False)
    from src.workflow.tasks.errors import CollaborationConflict
    with pytest.raises(CollaborationConflict):
        service.create('owner', objective='审查', acceptance=['证据'], members=[{'name': '审查', 'role': 'reviewer'}])
