"""Prompt and authorization contracts; no provider calls."""
import json
from src.agents.agent_loop import MainAgent, _tool_catalog
from src.agents.tool import Tool, function_schemas


async def test_missing_tools_policy_reaches_the_actual_model_request():
    requests = []
    class Script:
        async def chat(self, messages, **_kwargs):
            requests.append(messages)
            return {"content": "当前入口缺少排班工具，尚未安排。"}
    agent = MainAgent([], llm=Script(), native=False)
    await agent.run_turn("帮我安排一个定时任务")
    policy = requests[0][0]["content"]
    assert "缺工具时不要把用户推去用 crontab、GitHub Actions、IFTTT 这类外部服务" in policy

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
    assert "Values" not in _tool_catalog([tool])

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
