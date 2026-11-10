"""Malformed model calls must not turn into default-argument side effects."""

from src.agents.gate import make_confirm_gate
import json

import pytest

from src.agents.agent_loop import MainAgent, _to_native_messages
from src.agents.tool import Tool
from src.agents.tool_calls import ToolCallError, parse_native_tool_calls, parse_tool_call, parse_tool_calls
from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime


@pytest.mark.parametrize("args", [None, [], "{}", False, 0])
def test_prompted_arguments_are_rejected_without_empty_object_coercion(args):
    body = json.dumps({"tool": "write", "args": args})
    assert parse_tool_call(body) is None
    assert parse_tool_calls(body) is None
    with pytest.raises(ToolCallError, match="invalid_arguments"):
        parse_tool_calls(body, strict=True)


@pytest.mark.parametrize("arguments", ["{", "", "[]", "null", '"{}"', "false", 0, None,
                                       '{"x":1,"x":2}', '{"x":NaN}'])
def test_native_bad_arguments_fail_closed(arguments):
    with pytest.raises(ToolCallError):
        parse_native_tool_calls([{"name": "write", "arguments": arguments}])


@pytest.mark.parametrize("body", [
    '[{"tool":"write","args":{}},42]',
    '[{"tool":"write","args":{}},{"args":{}}]',
    '[{"tool":"write","args":{}},{"tool":"read","args":[]}]',
    '[{"tool":"write","args":{}},broken]',
    '[{"tool":"write","args":{}},',
    '[{"tool":"write","args":{}},{"tool":"read","args":{"nested":{"x":1,"x":2}}}]',
])
def test_partial_or_mixed_batches_never_salvage_the_valid_prefix(body):
    assert parse_tool_calls(body) is None
    with pytest.raises(ToolCallError):
        parse_tool_calls(body, strict=True)


def test_diagnostics_only_identify_positions_that_are_known():
    with pytest.raises(ToolCallError) as native:
        parse_native_tool_calls([{"name": "status"}, {"name": "write", "arguments": "["}])
    assert native.value.index == 2 and "第 2 项" in str(native.value)
    with pytest.raises(ToolCallError) as prompted:
        parse_tool_calls('[{"tool":"status"},{"tool":"write","args":{"x":1,"x":2}}]', strict=True)
    assert prompted.value.index is None and "调用批次" in str(prompted.value)


def test_ordinary_data_and_valid_zero_argument_calls_remain_compatible():
    assert parse_tool_calls('[{"path":"README.md"},42]') is None
    assert parse_tool_call('[{"tool":"read"}]') is None  # a single-call parser cannot pluck array members
    assert parse_tool_call('说明：{"tool":"status"}') == ("status", {})
    assert parse_tool_calls('```json\n[{"tool":"status"},{"tool":"read","args":{"nested":[true,2]}}]\n```') == [
        ("status", {}), ("read", {"nested": [True, 2]})]
    assert parse_native_tool_calls([{"name": "status"}, {"name": "read", "arguments": '{"nested":[true,2]}'}]) == [
        ("status", {}), ("read", {"nested": [True, 2]})]
    # Historic malformed calls remain ordinary archived text, never a native tool request.
    archived = [{"role": "assistant", "content": '{"tool":"write","args":[]}'},
                {"role": "user", "content": "[工具 write 结果]\nlegacy receipt"}]
    assert _to_native_messages(archived) == archived


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False])
async def test_rejected_batch_has_no_prefix_side_effect_or_approval(tmp_path, native):
    path = tmp_path / "active.txt"
    path.write_text("keep")
    ran, asked, events, requests = [], [], [], []

    async def write(args):
        ran.append(args)
        path.write_text(args.get("content", ""))
        return "written"

    async def approve(*_args):
        asked.append(True)
        return True

    class Script:
        async def chat(self, messages, **kwargs):
            requests.append(messages)
            if len(requests) > 1:
                return {"content": "本批没有执行。"}
            if native:
                return {"content": "", "tool_calls": [
                    {"name": "write", "arguments": '{"content":"would overwrite"}'},
                    {"name": "write", "arguments": '{"content":"DO_NOT_ECHO"'},
                ]}
            return {"content": '[{"tool":"write","args":{"content":"would overwrite"}},'
                               '{"tool":"write","args":"DO_NOT_ECHO"}]'}

    agent = MainAgent([Tool("write", "Write active file", {"content": "Text"}, write, read_only=False, operation_kind='write', confirm=make_confirm_gate(auto_approve=True))],
                      llm=Script(), native=native, on_escalate=approve, env_context=False,
                      on_tool_event=lambda *args: events.append(args))
    assert await agent.run_turn("更新当前文档", mode="plan") == "本批没有执行。"
    assert path.read_text() == "keep" and ran == asked == events == []
    assert len(requests) == 2
    history = str(agent.history)
    assert "本批工具调用全部未执行" in history and "DO_NOT_ECHO" not in history
    assert "would overwrite" not in history
    assert all(not message.get("tool_calls") for message in requests[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False])
async def test_corrected_call_runs_once_through_the_existing_mode_gate(tmp_path, native):
    path = tmp_path / "active.txt"
    path.write_text("keep")
    ran, asked, requests, events = [], [], [], []

    async def write(args):
        ran.append(args)
        path.write_text(args.get("content", ""))
        return "written corrected"

    async def approve(*_args):
        asked.append(True)
        return True

    class Script:
        async def chat(self, messages, **kwargs):
            requests.append(messages)
            if len(requests) == 3:
                return {"content": "已执行有效的修正调用。"}
            args = [] if len(requests) == 1 else {"content": "corrected"}
            if native:
                return {"content": "", "tool_calls": [{"name": "write", "arguments": json.dumps(args)}]}
            return {"content": json.dumps({"tool": "write", "args": args})}

    agent = MainAgent([Tool("write", "Write active file", {"content": "Text"}, write, read_only=False, operation_kind='write', confirm=make_confirm_gate(auto_approve=True))],
                      llm=Script(), native=native, on_escalate=approve, env_context=False,
                      on_tool_event=lambda stage, payload: events.append((stage, payload)))
    assert await agent.run_turn("更新当前文档", mode="plan") == "已执行有效的修正调用。"
    assert ran == [{"content": "corrected"}] and asked == [True]
    assert path.read_text() == "corrected"
    assert [stage for stage, _ in events] == ["start", "finish"]
    assert events[-1][1]["status"] == "succeeded"
    assert len(requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False])
@pytest.mark.parametrize("mode", ["plan", "build"])
@pytest.mark.parametrize("steps,expected_requests", [(1, 1), (8, 2)])
async def test_repeated_protocol_errors_stop_without_auto_continue_or_budget_escalation(native, mode, steps, expected_requests):
    requested, executed, asked, emitted = [], [], [], []

    async def handler(args):
        executed.append(args)
        return "unexpected"

    async def approve(*_args):
        asked.append(True)
        return True

    class Broken:
        async def chat(self, messages, **kwargs):
            requested.append(messages)
            if native:
                return {"content": "", "tool_calls": [{"name": "write", "arguments": "null"}]}
            return {"content": '{"tool":"write","args":null}'}

    agent = MainAgent([Tool("write", "Write", {}, handler, read_only=False, operation_kind='write', confirm=make_confirm_gate(auto_approve=True))], llm=Broken(),
                      native=native, max_steps=steps, plan_tool=True, on_escalate=approve, env_context=False)
    assert await agent.run_turn("处理", mode=mode, emit=emitted.append) == ""
    assert len(requested) == expected_requests and executed == asked == []
    assert len(emitted) == 1 and "本次尝试已停止" in emitted[0]
    assert all("单段预算已用完" not in str(message) for message in agent.history)


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [None, [], "private-fixture", False, 0, {1: "private-fixture"}])
async def test_shared_runtime_rejects_invalid_argument_objects_before_approval_and_hooks(args):
    ran, asked, hooks, events = [], [], [], []

    async def handler(value):
        ran.append(value)
        return "unexpected"

    async def approve(*_args):
        asked.append(True)
        return True

    async def hook(*args, **kwargs):
        hooks.append(True)

    tool = Tool("write", "Write", {}, handler, read_only=False, operation_kind='write', confirm=make_confirm_gate(auto_approve=True))
    runtime = ToolRuntime(lambda: {"write": tool}, ToolExecutionContext(
        on_escalate=approve, on_hook=hook, on_event=lambda stage, payload: events.append((stage, payload))))
    outcome = await runtime.execute_bound(tool, args, "plan")
    assert outcome.status == "blocked" and "invalid_arguments" in outcome.text
    assert ran == asked == hooks == []
    assert [stage for stage, _ in events] == ["start", "finish"]
    assert events[0][1]["id"] == events[1][1]["id"] == outcome.call_id
    assert "private-fixture" not in str(events) + outcome.text


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [None, [], {}, "", " "])
async def test_shared_runtime_rejects_invalid_names_without_registry_lookup_errors(name):
    runtime = ToolRuntime(lambda: {}, ToolExecutionContext())
    outcome = await runtime.execute(name, {})
    assert outcome.status == "blocked" and "invalid_name" in outcome.text


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False])
@pytest.mark.parametrize("second", ["", '{"tool":"write","args":', '<tool_call><function=write></function></tool_call>'])
async def test_format_correction_cannot_escape_into_auto_continuation_by_changing_format(native, second):
    requests, asked, emitted = [], [], []

    async def must_not_execute(_args):
        pytest.fail("invalid calls cannot execute")

    async def approve(*_args):
        asked.append(True)
        return True

    class Script:
        async def chat(self, messages, **kwargs):
            requests.append(messages)
            if len(requests) > 1:
                return {"content": second}
            if native:
                return {"content": "", "tool_calls": [{"name": "write", "arguments": "null"}]}
            return {"content": '{"tool":"write","args":null}'}

    agent = MainAgent([Tool("write", "Write", {}, must_not_execute, read_only=False, operation_kind='write', confirm=make_confirm_gate(auto_approve=True))],
                      llm=Script(), native=native, max_steps=8, plan_tool=True,
                      on_escalate=approve, env_context=False)
    assert await agent.run_turn("处理", mode="build", emit=emitted.append) == ""
    assert len(requests) == 2 and asked == []
    assert len(emitted) == 1 and "本次尝试已停止" in emitted[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False])
async def test_malformed_call_in_forced_synthesis_cannot_request_execution_permission(native):
    asked, emitted = [], []

    async def must_not_execute(_args):
        pytest.fail("forced synthesis cannot execute tools")

    async def approve(*_args):
        asked.append(True)
        return True

    class Broken:
        async def chat(self, messages, **kwargs):
            return {"content": '{"tool":"write","args":"DO_NOT_ECHO"}'}

    agent = MainAgent([Tool("write", "Write", {}, must_not_execute, read_only=False, operation_kind='write', confirm=make_confirm_gate(auto_approve=True))],
                      llm=Broken(), native=native, plan_tool=True, on_escalate=approve, env_context=False)
    assert await agent._force_finish("plan", lambda _: None, None, emitted.append) == ""
    assert asked == [] and len(emitted) == 1 and "本次尝试已停止" in emitted[0]
    assert "DO_NOT_ECHO" not in str(agent.history) + str(emitted)
