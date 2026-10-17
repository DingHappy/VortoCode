"""Reproduce extension adapter defects without starting third-party services."""
import pytest
from src.agents.agent_loop import MainAgent
from src.agents.mcp_tools import wrap_mcp_manager
from src.agents.tools.skills import build_skill_tools
from src.tools.manager import ToolManager
from src.tools.mcp_client import MCPTool, MCPToolResult, MCPTransport

SCHEMA = {"type":"object","properties":{"count":{"type":"integer","minimum":1},"mode":{"type":"string","enum":["fast","safe"]}},"required":["count"],"additionalProperties":False}

class FakeClient:
    server_name="fixture"
    transport=MCPTransport.HTTP
    connected=False
    def __init__(self): self.calls=[]
    async def connect(self): self.connected=True;return True
    async def disconnect(self): self.connected=False
    async def list_tools(self): return [MCPTool(name="count",server_name=self.server_name,input_schema=SCHEMA)]
    async def call_tool(self, call): self.calls.append(call.arguments);return MCPToolResult(success=True,output="counted")

@pytest.mark.asyncio
async def test_registered_manager_routes_to_its_connected_client(monkeypatch):
    import src.tools.manager as module
    client=FakeClient();monkeypatch.setattr(module,"create_mcp_client",lambda *a,**k:client)
    from src.agents.gate import make_confirm_gate
    manager=ToolManager(confirm=make_confirm_gate(auto_approve=True))
    manager.config={"servers":[{"name":"fixture","transport":"http","url":"http://fixture.invalid"}],"permissions":{"rules":[{"id":"fixture-only","scope":"global","action":"allow","tool_names":["count"],"priority":100}]}}
    try:
        assert await manager.initialize()
        result=await manager.execute_tool("count",{"count":2})
        assert result.success, result.error
        assert client.calls==[{"count":2}]
    finally:await manager.shutdown()


def test_wrapper_keeps_the_complete_native_schema():
    class Manager:
        def list_tools(self):return [MCPTool(name="count",server_name="fixture",input_schema=SCHEMA)]
    tools=wrap_mcp_manager(Manager())
    assert MainAgent(tools)._tools_schema()[0]["function"]["parameters"]==SCHEMA

@pytest.mark.asyncio
async def test_use_skill_reports_where_relative_resources_live(tmp_path):
    directory=tmp_path/".vortocode/skills/helper";directory.mkdir(parents=True)
    (directory/"SKILL.md").write_text('---\nname: helper\ndescription: fixture\n---\nRead references/guide.md.\n')
    tool=next(t for t in build_skill_tools(str(tmp_path),None) if t.name=="use_skill")
    output=await tool.handler({"name":"helper"})
    assert ".vortocode/skills/helper/SKILL.md" in output
    assert "路径基准：.vortocode/skills/helper" in output

@pytest.mark.asyncio
async def test_mcp_failure_is_a_failed_agent_event():
    class Manager:
        def list_tools(self):return [MCPTool(name="count",server_name="fixture",input_schema=SCHEMA)]
        async def execute_tool(self,*args):return MCPToolResult(success=False,error="fixture rejected")
    events=[];tool=wrap_mcp_manager(Manager())[0]
    agent=MainAgent([tool],on_tool_event=lambda stage,payload:events.append((stage,payload)))
    result=await agent._run_tool(tool.name,{},"build",lambda _:None)
    assert "fixture rejected" in result
    assert any(stage=="finish" and payload["status"]=="failed" for stage,payload in events)


def test_mcp_schema_is_copied_and_changes_recovery_identity():
    source={"type":"object","properties":{"count":{"type":"integer"}},"required":["count"]}
    class Manager:
        def list_tools(self):return [MCPTool(name="count",server_name="fixture",input_schema=source)]
    agent=MainAgent(wrap_mcp_manager(Manager()));before=agent.behavior_fingerprint()
    source["required"].clear()
    assert agent._tools_schema()[0]["function"]["parameters"]["required"]==["count"]
    agent._tool_list[0].input_schema["required"].clear()
    assert agent.behavior_fingerprint()!=before

@pytest.mark.asyncio
async def test_skill_source_cannot_cross_the_project_boundary(tmp_path):
    root=tmp_path/"project";root.mkdir();outside=tmp_path/"outside";outside.mkdir()
    (outside/"SKILL.md").write_text('---\nname: escaped\ndescription: fixture\n---\nOUTSIDE-MARKER\n')
    (root/"skills").symlink_to(outside,target_is_directory=True)
    tool=next(t for t in build_skill_tools(str(root),None) if t.name=="use_skill")
    result=await tool.handler({"name":"escaped"})
    assert result["ok"] is False and "没有名为" in result["error"] and "OUTSIDE-MARKER" not in str(result)

@pytest.mark.asyncio
async def test_skill_save_roundtrips_yaml_punctuation_after_confirmation(tmp_path):
    async def confirm(_):return True
    tools={t.name:t for t in build_skill_tools(str(tmp_path),confirm)}
    description='步骤: 测试 #保留\n第二行'
    await tools["save_skill"].handler({"name":"helper","description":description,"instructions":"fixture instructions"})
    from src.agents.tools.skills import _skill_registry_for
    skill=_skill_registry_for(str(tmp_path)).get("helper")
    assert skill is not None and skill.description==description
    assert "fixture instructions" in await tools["use_skill"].handler({"name":"helper"})

@pytest.mark.asyncio
async def test_failed_mcp_registration_closes_client(monkeypatch):
    import src.tools.manager as module
    client=FakeClient()
    async def fail():client.connected=True;return False
    client.connect=fail
    monkeypatch.setattr(module,"create_mcp_client",lambda *a,**k:client)
    manager=ToolManager();manager.config={"servers":[{"name":"fixture","transport":"http","url":"http://fixture.invalid"}]}
    await manager.initialize()
    assert not client.connected and not manager.mcp_clients and not manager.tool_executor.mcp_clients

@pytest.mark.asyncio
async def test_skill_save_cannot_write_through_an_escaping_directory(tmp_path):
    root=tmp_path/"project";root.mkdir();outside=tmp_path/"outside";outside.mkdir()
    (root/".vortocode").mkdir();(root/".vortocode/skills").symlink_to(outside,target_is_directory=True)
    async def confirm(_):return True
    tool=next(t for t in build_skill_tools(str(root),confirm) if t.name=="save_skill")
    result=await tool.handler({"name":"helper","instructions":"fixture"})
    assert result["ok"] is False and list(outside.iterdir())==[]
