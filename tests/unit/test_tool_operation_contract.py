"""工具操作类别与统一确认入口的行为契约。"""

import pytest

from src.agents.tool import Tool


async def echo(args):
    return "ok"


def test_tool_requires_an_explicit_operation_category():
    with pytest.raises(TypeError):
        Tool("probe", "probe", {}, echo)


def test_assembled_tools_all_declare_an_operation_category(tmp_path):
    from src.agents.main_agent import build_agent_tools, make_confirm_gate
    from src.agents.trust import KINDS

    tools = build_agent_tools(str(tmp_path), confirm=make_confirm_gate(), with_artifacts=True)
    assert tools
    assert not [tool.name for tool in tools if getattr(tool, "operation_kind", None) not in KINDS]


@pytest.fixture(autouse=True)
def isolated_taint():
    from src.agents.taint import reset_taint
    reset_taint()
    yield
    reset_taint()


@pytest.mark.parametrize('kind', ['write', 'execute', 'own_data', 'draft', 'deliver', 'interact', 'irreversible'])
@pytest.mark.parametrize('answer', [False, True])
async def test_runtime_confirms_every_effect_before_running_handler(kind, answer):
    from src.agents.gate import make_confirm_gate
    from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime
    ran, asked = [], []
    async def human(message, actual_kind):
        assert not ran
        asked.append(actual_kind)
        return answer
    async def action(args):
        ran.append(args)
        return 'done'
    tool = Tool('effect', 'effect', {}, action, operation_kind=kind)
    runtime = ToolRuntime(lambda: {'effect': tool}, ToolExecutionContext(
        confirm=make_confirm_gate(human, can_ask_human=True)))
    outcome = await runtime.execute('effect', {'value': 'exact'}, 'build')
    assert asked == [kind]
    assert ran == ([{'value': 'exact'}] if answer else [])
    assert outcome.status == ('succeeded' if answer else 'blocked')


async def test_preparation_is_not_permission_and_cannot_downgrade_category():
    from src.agents.gate import make_confirm_gate
    from src.agents.tool import PreparedToolCall
    from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime
    ran = []
    async def action():
        ran.append(True)
    async def prepare(args):
        return PreparedToolCall(action, 'delete', 'read')
    tool = Tool.prepared('delete', '', {}, prepare, operation_kind='irreversible')
    runtime = ToolRuntime(lambda: {'delete': tool}, ToolExecutionContext(confirm=make_confirm_gate(auto_approve=True)))
    assert (await runtime.execute('delete', {}, 'build')).status == 'blocked'
    assert not ran


async def test_internal_actions_require_the_matching_host_scope():
    from src.agents.tool import InternalToolScope
    from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime
    ran = []
    def check(_name, args):
        if args.get('task') != 'owned':
            raise ValueError('wrong task')
    async def action(args):
        ran.append(args)
        return 'done'
    scope = InternalToolScope('owned', frozenset({'review'}), check)
    tool = Tool('review', '', {}, action, operation_kind='internal', internal_scope=scope)
    runtime = ToolRuntime(lambda: {'review': tool}, ToolExecutionContext())
    assert (await runtime.execute('review', {'task': 'owned'}, 'build')).status == 'blocked'
    runtime.context.internal_scopes = (scope,)
    assert (await runtime.execute('review', {'task': 'other'}, 'build')).status == 'failed'
    assert not ran
    assert (await runtime.execute('review', {'task': 'owned'}, 'build')).status == 'succeeded'
    assert ran == [{'task': 'owned'}]


async def test_task_grant_is_limited_to_its_disposable_workspace(tmp_path):
    from src.agents.gate import make_confirm_gate
    from src.agents.tool import ApprovedTaskScope
    from src.agents.tools.files import build_write_tools
    from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime
    from src.agents.capabilities import SessionCapabilities, LOCAL_PROFILE
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    outside = tmp_path / 'owner.txt'
    outside.write_text('owner')
    (scratch / 'escape').symlink_to(tmp_path, target_is_directory=True)
    scope = ApprovedTaskScope('approved-task', str(scratch))
    tools = {t.name: t for t in build_write_tools(str(scratch), task_scope=scope)}
    runtime = ToolRuntime(lambda: tools, ToolExecutionContext(confirm=make_confirm_gate(), task_scopes=(scope,),
        capabilities=SessionCapabilities.for_profile(LOCAL_PROFILE, str(scratch))))
    assert (await runtime.execute('write_file', {'path': 'result.txt', 'content': 'result'}, 'build')).status == 'succeeded'
    assert (scratch / 'result.txt').read_text() == 'result'
    for path in ['../owner.txt', 'escape/owner.txt', str(outside)]:
        assert (await runtime.execute('write_file', {'path': path, 'content': 'bad'}, 'build')).status == 'blocked'
    assert outside.read_text() == 'owner'
    from src.agents.taint import mark_tainted
    mark_tainted()
    assert (await runtime.execute('write_file', {'path': 'result.txt', 'content': 'bad'}, 'build')).status == 'blocked'
    assert (scratch / 'result.txt').read_text() == 'result'


@pytest.mark.parametrize('tainted,can_ask,answer,expected', [
    (False, True, False, True), (True, True, False, False),
    (True, True, True, True), (False, False, True, False),
])
async def test_protective_reduction_keeps_taint_and_unattended_rules(tainted, can_ask, answer, expected):
    from src.agents.gate import make_confirm_gate
    from src.agents.taint import mark_tainted
    from src.agents.tool import PreparedToolCall
    from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime
    asked, ran = [], []
    async def human(message, kind):
        asked.append(kind)
        return answer
    async def apply():
        ran.append(True)
        return 'stopped'
    async def prepare(args):
        return PreparedToolCall(apply, '停用已指定的作业', 'write', protective=True)
    tool = Tool.prepared('cron_toggle', '', {}, prepare, operation_kind='write', protective=True)
    runtime = ToolRuntime(lambda: {'cron_toggle': tool}, ToolExecutionContext(
        confirm=make_confirm_gate(human, can_ask_human=can_ask,
                                 capability_profile='external' if can_ask else 'unattended')))
    if tainted:
        mark_tainted()
    result = await runtime.execute('cron_toggle', {'name': 'existing', 'enabled': False}, 'build')
    assert bool(ran) == expected
    assert (result.status == 'succeeded') == expected
    assert asked == (['write'] if tainted and can_ask else [])


async def test_protective_marker_cannot_authorize_enabling_or_extra_fields():
    from src.agents.gate import make_confirm_gate
    from src.agents.tool import PreparedToolCall
    from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime
    ran = []
    async def apply():
        ran.append(True)
    async def prepare(args):
        return PreparedToolCall(apply, '不得扩大', 'write', protective=True)
    tool = Tool.prepared('cron_toggle', '', {}, prepare, operation_kind='write', protective=True)
    runtime = ToolRuntime(lambda: {'cron_toggle': tool}, ToolExecutionContext(confirm=make_confirm_gate()))
    for args in [{'name': 'existing', 'enabled': True},
                 {'name': 'existing', 'enabled': False, 'prompt': 'replacement'}]:
        assert (await runtime.execute('cron_toggle', args, 'build')).status == 'blocked'
    assert not ran


async def test_approval_binds_arguments_even_if_the_callers_dictionary_changes():
    from src.agents.gate import make_confirm_gate
    args = {'path': 'approved', 'nested': {'content': 'original'}}
    observed = []
    async def human(message, kind):
        assert 'approved' in message and 'original' in message
        args['path'] = 'different'
        args['nested']['content'] = 'changed'
        return True
    async def write(bound):
        observed.append(bound)
    tool = Tool('write', '', {}, write, operation_kind='write',
                confirm=make_confirm_gate(human, can_ask_human=True))
    await tool.handler(args)
    assert observed == [{'path': 'approved', 'nested': {'content': 'original'}}]


def test_workspace_grant_cannot_be_rebound_to_a_different_root(tmp_path):
    from src.agents.tool import ApprovedTaskScope
    from src.agents.tools.files import build_write_tools
    first, second = tmp_path / 'one', tmp_path / 'two'
    first.mkdir()
    second.mkdir()
    grant = ApprovedTaskScope('approved', str(first))
    tool = next(t for t in build_write_tools(str(second), task_scope=grant) if t.name == 'write_file')
    assert not grant.permits(tool, {'path': 'a', 'content': 'x'}, 'write')
