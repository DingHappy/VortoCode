"""Tool selection contracts at model request boundaries; no provider calls."""
import json

import pytest

from src.agents.agent_loop import MainAgent, _tool_catalog
from src.agents.tool import Tool, function_schemas, tool_definitions


async def noop(_args):
    return "fixture"


def prompt_schemas(prompt):
    return [json.loads(line.strip().removeprefix("参数合同："))
            for line in prompt.splitlines() if line.strip().startswith("参数合同：")]


def test_legacy_parameter_types_required_fields_and_copying_share_one_contract():
    tool = Tool("inspect", "Inspect", {"items": "Values", "count": "Count"}, noop,
                required_args=("items",), argument_schema={
                    "items": {"type": "array", "items": {"type": "boolean"}},
                    "count": {"type": "integer", "minimum": 0},
                })
    native = function_schemas([tool])[0]["function"]["parameters"]
    prompted = prompt_schemas(_tool_catalog([tool]))[0]
    assert prompted == native
    assert prompted["required"] == ["items"]
    assert prompted["properties"]["items"]["items"] == {"type": "boolean"}
    prompted["required"].clear()
    native["properties"]["items"]["items"]["type"] = "string"
    assert tool.required_args == ("items",)
    assert tool.argument_schema["items"]["items"]["type"] == "boolean"
    # Explicit provider contracts retain precedence over legacy metadata.
    tool.input_schema = {"type": "object", "properties": {}, "required": []}
    assert prompt_schemas(_tool_catalog([tool])) == [tool.input_schema]
    assert function_schemas([tool])[0]["function"]["parameters"] == tool.input_schema
    assert "参数：以当前参数合同为准" in _tool_catalog([tool])
    assert "Values" not in _tool_catalog([tool])  # stale legacy descriptions cannot override a full schema


def test_builtin_contracts_advertise_executable_types_and_only_existing_required_fields(tmp_path):
    from src.agents.tools.files import build_read_tools, build_write_tools, build_confirmed_write_tools
    from src.agents.tools.shell import build_command_tool, build_pr_tool
    from src.agents.tools.web import build_web_tools, build_screenshot_tool
    from src.agents.tools.memory import build_memory_tools
    from src.agents.tools.cron import build_cron_tools
    from src.agents.tools.team import build_team_tools
    from src.agents.tools.skills import build_skill_tools
    from src.agents.delegation import build_research_tools
    from src.workflow.tasks.team import MAX_LIMITS

    root = str(tmp_path)
    tools = (build_read_tools(root) + build_write_tools(root)
             + build_confirmed_write_tools(root, noop) + build_command_tool(root, noop)
             + build_pr_tool(root, noop) + build_web_tools() + build_screenshot_tool(root)
             + build_memory_tools(root, noop) + build_cron_tools(root, noop) + build_skill_tools(root, noop)
             + build_team_tools(root, confirm=noop, owner="fixture") + build_research_tools(root))
    declared = tool_definitions(tools)
    for declaration in declared:
        schema = declaration["input_schema"]
        assert set(schema.get("required", [])) <= set(schema.get("properties", {})), declaration["name"]
    schemas = {row["name"]: row["input_schema"] for row in declared}
    assert schemas["recall_memory"]["required"] == []  # empty query is a supported operation
    assert schemas["search_conversations"]["required"] == ["query"]
    assert schemas["write_file"]["required"] == ["path", "content"]
    assert schemas["use_skill"]["required"] == ["name"]
    for name, field, kind in [("read_file", "start", "integer"), ("grep", "context", "integer"),
                              ("edit_file", "replace_all", "boolean"), ("run_command", "background", "boolean"),
                              ("screenshot_page", "full_page", "boolean"), ("cron_toggle", "enabled", "boolean"),
                              ("team_decide", "accept", "boolean"), ("research_parallel", "tasks", "array")]:
        assert schemas[name]["properties"][field]["type"] == kind
    assert schemas["read_conversation_excerpt"]["properties"]["offset"]["minimum"] == 0
    assert schemas["cron_add"]["properties"]["budget"]["minimum"] == 0
    limits = schemas["team_propose"]["properties"]["limits"]
    assert set(limits["properties"]) == set(MAX_LIMITS)
    assert all(spec["type"] == "integer" for spec in limits["properties"].values())
    assert {key: spec["maximum"] for key, spec in limits["properties"].items()} == MAX_LIMITS


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [True, False])
async def test_typed_plan_and_line_reads_reach_real_handlers_in_both_protocols(tmp_path, native):
    from src.agents.tools.files import build_read_tools
    (tmp_path / "fixture.txt").write_text("alpha\nselected\nomega\n")
    requests, plans = [], []
    calls = [("update_plan", {"steps": [{"step": "读取第二行", "status": "in_progress"}]}),
             ("read_file", {"path": "fixture.txt", "start": 2, "end": 2}),
             ("update_plan", {"steps": [{"step": "读取第二行", "status": "completed"}]})]

    class Script:
        async def chat(self, messages, tools=None, **kwargs):
            requests.append((messages, tools))
            if len(requests) > len(calls):
                return {"content": "已读取第二行 selected。"}
            name, args = calls[len(requests) - 1]
            if native:
                return {"content": "", "tool_calls": [{"id": str(len(requests)), "name": name,
                                                          "arguments": json.dumps(args)}]}
            return {"content": json.dumps({"tool": name, "args": args})}

    agent = MainAgent(build_read_tools(str(tmp_path)), llm=Script(), native=native,
                      plan_tool=True, env_context=False, on_plan=lambda plan: plans.append(plan))
    fingerprint = agent.behavior_fingerprint()
    answer = await agent.run_turn("读取 fixture.txt 第二行并更新计划")
    assert answer == "已读取第二行 selected。"
    assert plans == [[{"step": "读取第二行", "status": "in_progress"}],
                     [{"step": "读取第二行", "status": "completed"}]]
    assert agent.plan == plans[-1]
    assert agent.behavior_fingerprint() == fingerprint
    prefixes = [messages[0]["content"] for messages, _ in requests]
    assert len(set(prefixes)) == 1
    last = requests[-1][0]
    results = [message["content"] for message in last
               if message["role"] == "tool" or str(message.get("content", "")).startswith("[工具 ")]
    assert any("selected" in result and "alpha" not in result and "omega" not in result for result in results)
    schemas = ({row["function"]["name"]: row["function"]["parameters"] for row in requests[0][1]}
               if native else {"plan": prompt_schemas(prefixes[0])})
    if native:
        assert "参数合同：" not in prefixes[0]  # structured schemas are already attached
        plan = schemas["update_plan"]
        assert schemas["read_file"]["properties"]["start"]["type"] == "integer"
    else:
        plan = next(schema for schema in schemas["plan"] if "steps" in schema["properties"])
    assert plan["required"] == ["steps"]
    assert plan["properties"]["steps"]["type"] == "array"
    assert plan["properties"]["steps"]["items"]["properties"]["status"]["enum"] == ["pending", "in_progress", "completed"]


def test_effective_guidance_and_recovery_identity_follow_the_live_contract(monkeypatch):
    import src.agents.agent_loop as loop
    tool = Tool("read_file", "Read", {"path": "Path"}, noop)
    agent = MainAgent([tool], plan_tool=True)
    before = agent.behavior_fingerprint()
    # A rendered mode or runtime plan must not cause a false capability update.
    agent._system("build")
    assert agent.behavior_fingerprint() == before
    assert "【网页证据】" not in agent._system("plan")
    monkeypatch.setitem(loop._ACTION_GUIDANCE, "web_fetch", "unavailable guidance changed")
    assert agent.behavior_fingerprint() == before
    monkeypatch.setitem(loop._ACTION_GUIDANCE, "read_file", "available guidance changed")
    assert agent.behavior_fingerprint() != before
    for mutate in [lambda: setattr(tool, "required_args", ("path",)),
                   lambda: tool.argument_schema.update(path={"type": "string", "minLength": 1}),
                   lambda: setattr(tool, "serial", True),
                   lambda: setattr(tool, "required_capabilities", ("host_process",))]:
        current = agent.behavior_fingerprint()
        mutate()
        assert agent.behavior_fingerprint() != current
    assert "须串行" in agent._system("plan")
    current = agent.behavior_fingerprint()
    monkeypatch.setattr(agent, "_orchestration_hint", lambda: "updated orchestration")
    assert agent.behavior_fingerprint() != current
    agent.add_tools([Tool("web_fetch", "Fetch", {}, noop)])
    assert "unavailable guidance changed" in agent._system("plan")
    agent.remove_tools(["web_fetch"])
    assert "unavailable guidance changed" not in agent._system("plan")
