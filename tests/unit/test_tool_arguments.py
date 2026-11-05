"""Deterministic argument checks through the shared runtime and both protocols."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from src.agents.capabilities import SessionCapabilities
from src.agents.tool import Tool, function_schemas, tool_definitions
from src.agents.tool_arguments import ToolArgumentError, validate_arguments
from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime


@pytest.mark.parametrize("schema,value", [
    ({"type": "integer"}, True),
    ({"type": "integer"}, "2"),
    ({"type": "integer"}, 2.0),
    ({"type": "number"}, False),
    ({"type": "boolean"}, "false"),
    ({"type": "array"}, "[]"),
    ({"type": "object"}, []),
    ({"type": "string"}, None),
    ({"enum": [1]}, True),
    ({"enum": [[1]]}, [True]),
    ({"enum": [{"ok": True}]}, {"ok": 1}),
    ({"type": "integer", "minimum": 1}, 0),
    ({"type": "number", "maximum": 3}, 4),
    ({"exclusiveMinimum": 1}, 1),
    ({"exclusiveMaximum": 3}, 3),
    ({"minLength": 1}, ""),
    ({"maxLength": 2}, "中文🙂"),
    ({"minItems": 1}, []),
    ({"maxItems": 1}, [1, 2]),
    ({"type": "array", "items": {"type": "string"}}, ["ok", 2]),
    ({"type": "object", "properties": {"round": {"type": "integer"}}}, {"round": True}),
])
def test_explicit_constraints_reject_without_coercion(schema, value):
    with pytest.raises(ToolArgumentError, match=r"\$\.field"):
        validate_arguments({"field": value}, {"field": schema})


@pytest.mark.parametrize("schema,value", [
    ({"type": "integer", "minimum": 1, "maximum": 3}, 1),
    ({"type": "number", "exclusiveMinimum": 1, "exclusiveMaximum": 3}, 2.5),
    ({"type": ["boolean", "null"]}, None),
    ({"type": "boolean"}, False),
    ({"type": "string", "maxLength": 3}, "中文🙂"),
    ({"type": "array", "items": {"type": "string"}, "maxItems": 2}, ["", "🙂"]),
    ({"enum": [{"ok": True}]}, {"ok": True}),
    ({"type": "number", "enum": [1]}, 1.0),
    ({"type": "object", "properties": {"round": {"type": "integer"}}}, {"round": 2, "extra": "legacy"}),
])
def test_valid_values_and_whitespace_are_unchanged(schema, value):
    args = {"field": value, "legacy": {"untouched": [True, "  \n"]}}
    before = deepcopy(args)
    validate_arguments(args, {"field": schema})
    assert args == before


def test_legacy_optional_and_open_fields_do_not_gain_inferred_types_or_defaults():
    args = {"undeclared": [1, True]}
    validate_arguments(args, {"optional": {"type": "integer", "default": 2}})
    assert args == {"undeclared": [1, True]}
    with pytest.raises(ToolArgumentError, match="缺少必填参数"):
        validate_arguments({}, {"text": {"type": "string"}}, ("text",))
    validate_arguments({"text": ""}, {"text": {"type": "string"}}, ("text",))


@pytest.mark.parametrize("args", [None, [], "{}", {1: "x"}, {"nested": {1: "x"}},
    {"field": float("nan")}, {"nested": [float("inf")]}, {"field": object()}])
def test_non_json_values_do_not_reach_handlers(args):
    with pytest.raises(ToolArgumentError):
        validate_arguments(args, {})


@pytest.mark.parametrize("schema", [
    {"type": "typo"}, {"type": []}, {"enum": []}, {"minLength": True},
    {"minItems": -1}, {"minimum": float("inf")}, {"items": []},
    {"$ref": "https://example.invalid/schema"}, {"pattern": "secret-value"},
    {"properties": []}, {"properties": {"round": {"type": "unknown"}}},
])
def test_bad_or_unsupported_local_contracts_fail_closed_even_for_absent_fields(schema):
    with pytest.raises(ToolArgumentError):
        validate_arguments({}, {"field": schema})


def test_cycles_deep_trees_and_large_comparisons_are_bounded():
    cycle = []; cycle.append(cycle)
    with pytest.raises(ToolArgumentError, match="过于复杂"):
        validate_arguments({"field": cycle}, {})
    nested = {"type": "array"}; nested["items"] = nested
    with pytest.raises(ToolArgumentError, match="过于复杂"):
        validate_arguments({}, {"field": nested})
    with pytest.raises(ToolArgumentError, match="过于复杂"):
        validate_arguments({"field": [0] * 6000}, {"field": {
            "enum": [[0] * 6000], "items": {"type": "integer"}}})


@pytest.mark.parametrize("bound", [False, True])
async def test_invalid_parameters_are_blocked_before_approval_hooks_and_side_effects(bound):
    calls, events = [], []

    async def handler(args):
        calls.append("handler")
        return "should not run"

    async def approve(*_args):
        calls.append("approve")
        return True

    async def hook(*_args, **_kwargs):
        calls.append("hook")

    tool = Tool("write", "", {"round": "Round"}, handler, read_only=False,
                argument_schema={"round": {"type": "integer", "minimum": 1}})
    ctx = ToolExecutionContext(on_escalate=approve, on_hook=hook,
        on_result=lambda *_args: calls.append("audit"),
        on_success=lambda *_args: calls.append("success"),
        on_event=lambda stage, data: events.append((stage, data)))
    runtime = ToolRuntime(lambda: {tool.name: tool}, ctx)
    result = await (runtime.execute_bound(tool, {"round": True}, "plan") if bound
                    else runtime.execute("write", {"round": True}, "plan"))
    assert result.status == "blocked" and "[参数拦截]" in result.text
    assert calls == [] and not ctx.state.escalated
    assert [stage for stage, _ in events] == ["start", "finish"]
    assert events[0][1]["id"] == events[1][1]["id"] == result.call_id
    assert events[-1][1]["status"] == "blocked"


async def test_contracts_are_live_and_full_provider_schema_keeps_precedence():
    received = []

    async def handler(args):
        received.append(args)
        return "ok"

    tool = Tool("read", "", {"count": "Count"}, handler,
                argument_schema={"count": {"type": "integer", "maximum": 2}})
    runtime = ToolRuntime(lambda: {tool.name: tool}, ToolExecutionContext())
    assert (await runtime.execute("read", {"count": 3})).status == "blocked"
    tool.argument_schema["count"]["maximum"] = 4
    args = {"count": 3}
    assert (await runtime.execute("read", args)).status == "succeeded"
    assert received == [args] and received[0] is args
    # Full external schemas are exported verbatim, not partially interpreted by
    # the deliberately smaller local validator. The adapter/handler owns them.
    tool.input_schema = {"$ref": "#/$defs/value", "$defs": {"value": {"type": "object"}}}
    assert tool_definitions([tool])[0]["input_schema"] == tool.input_schema
    assert (await runtime.execute("read", {"count": "provider handled"})).status == "succeeded"


async def test_real_team_contracts_validate_nested_members_and_evidence_without_starting_work(tmp_path):
    from src.agents.tools.team import build_team_tools
    from src.agents.team_coordinator import build_tools

    asked = []

    async def confirm(message):
        asked.append(message)
        return True

    tools = build_team_tools(str(tmp_path), confirm=confirm, owner="owner")
    tools += build_tools(str(tmp_path), "unused-root", kick=lambda _root: pytest.fail("must not schedule"))
    registry = {tool.name: tool for tool in tools}
    runtime = ToolRuntime(lambda: registry, ToolExecutionContext(
        capabilities=SessionCapabilities.for_profile("local", str(tmp_path))))
    for tool in tools:
        validate_arguments({}, tool.argument_schema)  # Inspect optional/required field constraints without supplying a call.
    members = {"objective": "Review evidence", "acceptance": ["Evidence"], "members": [{"name": "researcher", "role": "reviewer", "engine": "vortocode"}]}
    evidence = {"evidence": [{"criterion": 1, "evidence": "file.txt:1"}]}
    validate_arguments(members, registry["team_propose"].argument_schema)
    validate_arguments(evidence, registry["team_submit"].argument_schema)
    members["members"][0]["engine"] = "not-a-real-engine"
    evidence["evidence"][0]["criterion"] = True
    for name, args, path in [("team_propose", members, "$.members[0].engine"),
                             ("team_submit", evidence, "$.evidence[0].criterion")]:
        result = await runtime.execute(name, args, "build")
        assert result.status == "blocked" and path in result.text
    assert asked == []


@pytest.mark.parametrize("isolated", [False, True])
@pytest.mark.parametrize("name,args", [
    ("write_file", {"path": "note.txt"}),
    ("write_file", {"path": "note.txt", "content": None}),
    ("write_file", {"path": "note.txt", "content": {"secret": "do not echo"}}),
    ("edit_file", {"path": "note.txt", "old": "before"}),
    ("edit_file", {"path": "note.txt", "old": "", "new": "after"}),
    ("edit_file", {"path": "note.txt", "old": "before", "new": False}),
])
async def test_real_file_tools_preserve_file_and_never_ask_for_invalid_calls(tmp_path, isolated, name, args):
    from src.agents.tools.files import build_write_tools, build_confirmed_write_tools

    asked = []

    async def confirm(message):
        asked.append(message)
        return True

    path = tmp_path / "note.txt"; path.write_text("before")
    tools = build_write_tools(str(tmp_path)) if isolated else build_confirmed_write_tools(str(tmp_path), confirm)
    runtime = ToolRuntime(lambda: {t.name: t for t in tools}, ToolExecutionContext(
        capabilities=SessionCapabilities.for_profile("local", str(tmp_path))))
    result = await runtime.execute(name, args, "build")
    assert result.status == "blocked" and "[参数拦截]" in result.text
    assert "do not echo" not in result.text
    assert path.read_text() == "before" and asked == []


@pytest.mark.parametrize("isolated", [False, True])
async def test_real_file_tools_allow_explicit_empty_replacement_and_file_content(tmp_path, isolated):
    from src.agents.tools.files import build_write_tools, build_confirmed_write_tools

    async def confirm(_message):
        return True

    tools = build_write_tools(str(tmp_path)) if isolated else build_confirmed_write_tools(str(tmp_path), confirm)
    runtime = ToolRuntime(lambda: {t.name: t for t in tools}, ToolExecutionContext(
        capabilities=SessionCapabilities.for_profile("local", str(tmp_path))))
    path = tmp_path / "note.txt"; path.write_text("before")
    assert (await runtime.execute("edit_file", {"path": "note.txt", "old": "before", "new": ""}, "build")).status == "succeeded"
    assert path.read_text() == ""
    assert (await runtime.execute("write_file", {"path": "note.txt", "content": ""}, "build")).status == "succeeded"
    assert path.read_text() == ""
    for tool in tools:
        schema = function_schemas([tool])[0]["function"]["parameters"]
        assert schema["required"] == list(tool.required_args)
        schema["required"].clear()
        assert tool.required_args


@pytest.mark.parametrize("native", [False, True])
async def test_main_agent_can_correct_invalid_arguments_using_the_same_contract(native):
    from src.agents.agent_loop import MainAgent

    ran, asked, events = [], [], []

    async def handler(args):
        ran.append(deepcopy(args))
        return "completed"

    async def escalate(*_args):
        asked.append(True)
        return True

    class Script:
        def __init__(self):
            self.requests = []

        async def chat(self, messages, **kwargs):
            self.requests.append(deepcopy(messages))
            step = len(self.requests)
            if step == 3:
                return {"content": "已核对结果。"}
            args = {"round": True if step == 1 else 2, "verdict": "accept"}
            if native:
                schema = kwargs["tools"][0]["function"]["parameters"]
                assert schema["properties"]["round"]["type"] == "integer"
                assert schema["required"] == ["round", "verdict"]
                return {"content": "", "tool_calls": [{"name": "review", "arguments": json.dumps(args)}]}
            assert '"round"' in messages[0]["content"] and '"minimum": 1' in messages[0]["content"]
            return {"content": json.dumps({"tool": "review", "args": args})}

    tool = Tool("review", "", {"round": "Round", "verdict": "Decision"}, handler, read_only=False,
        argument_schema={"round": {"type": "integer", "minimum": 1, "maximum": 3},
                         "verdict": {"type": "string", "enum": ["accept", "rework"]}},
        required_args=("round", "verdict"))
    llm = Script()
    agent = MainAgent([tool], llm=llm, native=native, on_escalate=escalate,
        on_tool_event=lambda stage, data: events.append((stage, data)), max_steps=4)
    assert await agent.run_turn("核对第二轮结果", mode="plan") == "已核对结果。"
    assert ran == [{"round": 2, "verdict": "accept"}] and asked == [True]
    assert [data["status"] for stage, data in events if stage == "finish"] == ["blocked", "succeeded"]
    assert any("[参数拦截]" in str(message.get("content")) for message in llm.requests[1])
    assert len({request[0]["content"] for request in llm.requests}) == 1
