"""Local typed declarations use the existing validating Tool contract."""
import json
import pytest
from src.agents.agent_loop import MainAgent
from src.agents.tool import tool_definitions

async def noop(_args):
    return "fixture"


@pytest.mark.parametrize("steps", [[{"step": "Read"}], [{"status": "pending"}], [{"step": "Read", "status": "typo"}]])
async def test_real_plan_rejects_missing_nested_fields_before_changing_state(steps):
    agent = MainAgent([], plan_tool=True)
    result = await agent._run_tool("update_plan", {"steps": steps}, "plan", lambda _text: None)
    assert "[参数拦截]" in result and agent.plan == []


@pytest.mark.parametrize("name,args", [("run_command", {"command": {"not": "text"}}),
                                      ("read_file", {"path": "fixture", "start": True})])
async def test_typed_builtin_fields_block_before_approvals_or_handlers(tmp_path, name, args):
    from src.agents.tools.files import build_read_tools
    from src.agents.tools.shell import build_command_tool
    from src.agents.tool_runtime import ToolRuntime, ToolExecutionContext
    calls = []
    async def confirm(*_args):
        calls.append("approval")
        return True
    async def handler(_args):
        calls.append("handler")
        return "should not execute"
    tools = {tool.name: tool for tool in build_read_tools(str(tmp_path)) + build_command_tool(str(tmp_path), confirm)}
    tools[name]._implementation = handler
    runtime = ToolRuntime(lambda: tools, ToolExecutionContext(on_escalate=confirm))
    assert (await runtime.execute(name, args, "plan")).status == "blocked"
    assert calls == []

def prompt_schemas(prompt):
    return [json.loads(line.strip().removeprefix("参数合同："))
            for line in prompt.splitlines() if line.strip().startswith("参数合同：")]

def test_builtin_contracts_advertise_executable_types_and_only_existing_required_fields(tmp_path):
    from src.agents.tools.files import build_read_tools, build_write_tools, build_confirmed_write_tools
    from src.agents.tools.shell import build_command_tool, build_pr_tool
    from src.agents.tools.web import build_web_tools, build_screenshot_tool
    from src.agents.tools.memory import build_memory_tools
    from src.agents.tools.cron import build_cron_tools
    from src.agents.tools.team import build_team_tools
    from src.agents.tools.skills import build_skill_tools
    from src.agents.delegation import build_research_tools

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
    member = schemas["team_propose"]["properties"]["members"]["items"]
    # Either a registered member reference or a temporary name/role; the service
    # validates these exclusive forms before asking for approval.
    assert member.get("required", []) == []
    assert member["properties"]["member"]["type"] == "string"
    assert "reason" in schemas["team_propose"]["required"]
    assert member["properties"]["engine"]["enum"] == ["vortocode", "opencode", "deepseek-harness"]
    limits = schemas["team_propose"]["properties"]["limits"]
    assert set(limits["properties"]) == {"parallel"}
    assert all(spec["type"] == "integer" for spec in limits["properties"].values())
    assert limits["properties"]["parallel"]["maximum"] == 4

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
        plan = schemas["update_plan"]
        assert schemas["read_file"]["properties"]["start"]["type"] == "integer"
    else:
        plan = next(schema for schema in schemas["plan"] if "steps" in schema["properties"])
    assert plan["required"] == ["steps"]
    assert plan["properties"]["steps"]["type"] == "array"
    assert plan["properties"]["steps"]["items"]["properties"]["status"]["enum"] == ["pending", "in_progress", "completed"]
