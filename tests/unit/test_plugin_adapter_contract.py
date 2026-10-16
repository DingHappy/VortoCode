"""Plugin adapter contracts with synthetic fixtures, no external service or model."""
import json
from types import SimpleNamespace

import pytest

from src.agents.agent_loop import MainAgent
from src.agents.mcp_tools import wrap_mcp_manager
from src.agents.tools.skills import _skill_registry_for, build_skill_tools
from src.skills.parser import SkillParser
from src.tools.mcp_client import MCPToolResult, MCPToolCall, StdioMCPClient, HTTPMCPClient, parse_tool_result


def write_skill(root, *, disabled=False, body="Read references/guide.md."):
    path = root / ".vortocode/skills/helper/SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: helper\ndescription: fixture\ndisable-model-invocation: {str(disabled).lower()}\n---\n{body}\n")
    return path


@pytest.mark.parametrize("payload", [None, [], {}, {"content": {}}, {"content": ["raw"]},
                                     {"content": [{"text": "no type"}]},
                                     {"content": [], "isError": 0}, {"content": [], "isError": "false"},
                                     {"content": [], "structuredContent": []},
                                     {"content": [], "structuredContent": None}])
def test_mcp_malformed_result_is_not_silent_success(payload):
    with pytest.raises(ValueError, match="Invalid MCP tool result"):
        parse_tool_result(payload, tool_name="fixture", execution_time=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["stdio", "legacy_http"])
async def test_existing_transports_preserve_structured_data_and_server_failure(monkeypatch, transport):
    client = StdioMCPClient("fixture", command="unused") if transport == "stdio" else HTTPMCPClient("fixture", "http://fixture.invalid")
    client.connected = True
    payload = {"content": [], "structuredContent": {"found": True, "count": 2}, "isError": False}
    calls = []
    async def rpc(method, params):
        calls.append((method, params))
        return {"result": payload}
    if transport == "stdio":
        monkeypatch.setattr(client, "_rpc", rpc)
    else:
        class Response:
            status = 200
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass
            async def json(self): return payload
        class Session:
            def post(self, url, **kwargs): calls.append((url, kwargs)); return Response()
        client.session = Session()
    result = await client.call_tool(MCPToolCall(tool_name="lookup"))
    assert result.success and result.structured_content == {"found": True, "count": 2}
    payload["isError"] = True
    failed = await client.call_tool(MCPToolCall(tool_name="lookup"))
    assert not failed.success and failed.structured_content == result.structured_content
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_wrapper_json_is_roundtrippable_and_external_taint_is_retained():
    content = [{"type": "text", "text": "中文\nquoted: \"value\""},
               {"type": "resource_link", "uri": "https://resource.example.test/item", "name": "fixture"}]
    result = MCPToolResult(success=True, output=content, structured_content={"ok": True, "missing": None})
    class Manager:
        def list_tools(self):
            return [SimpleNamespace(name="lookup", server_name="fixture", description="fixture", metadata={}, input_schema={})]
        async def execute_tool(self, *_): return result
    tool = wrap_mcp_manager(Manager())[0]
    assert tool.untrusted_source and tool.external_content and not tool.read_only
    assert json.loads(await tool.handler({})) == {"content": content, "structuredContent": {"ok": True, "missing": None}}
    result.structured_content = None
    assert json.loads(await tool.handler({})) == content


@pytest.mark.parametrize("text,expected", [
    ("Body\n---\nStill body\n---\nDone", "Body\n---\nStill body\n---\nDone"),
    ('---\nname: helper\ndescription: "a---b"\n---\nBody\n---\nDone', "Body\n---\nDone"),
    ("---\r\nname: helper\r\n---\r\nBody", "Body"),
])
def test_skill_frontmatter_only_uses_leading_whole_line_delimiters(text, expected):
    assert SkillParser.parse_content(text)[1] == expected


@pytest.mark.parametrize("text", ["---\nname: helper", "---\n- list\n---\nBody"])
def test_broken_skill_header_is_rejected(text):
    with pytest.raises(ValueError): SkillParser.parse_content(text)


@pytest.mark.asyncio
async def test_disabled_project_skill_is_not_in_catalog_or_loadable(tmp_path):
    write_skill(tmp_path, disabled=True, body="DISABLED_BODY_MARKER")
    registry = _skill_registry_for(str(tmp_path))
    assert not registry.catalog()
    tool = build_skill_tools(str(tmp_path), None, registry=registry)[0]
    result = await tool.handler({"name": "helper"})
    assert result["ok"] is False and "DISABLED_BODY_MARKER" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["edit", "delete", "symlink", "oversize"])
async def test_project_skill_never_loads_stale_or_escaping_content(tmp_path, change):
    root = tmp_path / "project"; root.mkdir()
    path = write_skill(root, body="ORIGINAL_BODY_MARKER")
    registry = _skill_registry_for(str(root))
    tool = build_skill_tools(str(root), None, registry=registry)[0]
    if change == "edit": path.write_text("REPLACEMENT_BODY_MARKER")
    elif change == "delete": path.unlink()
    elif change == "oversize": path.write_text("x" * (256 * 1024 + 1))
    else:
        outside = tmp_path / "outside.md"; outside.write_text("OUTSIDE_BODY_MARKER")
        path.unlink(); path.symlink_to(outside)
    result = await tool.handler({"name": "helper"})
    assert result["ok"] is False
    assert not any(marker in str(result) for marker in ("ORIGINAL_BODY_MARKER", "REPLACEMENT_BODY_MARKER", "OUTSIDE_BODY_MARKER"))
    if change == "edit":
        registry.load()  # Explicit in-place reload makes this exact tool closure current.
        assert "REPLACEMENT_BODY_MARKER" in await tool.handler({"name": "helper"})


@pytest.mark.asyncio
async def test_project_skill_load_taints_parent_batch_and_missing_skill_is_failed_event(tmp_path):
    from src.agents.taint import reset_taint, is_tainted
    write_skill(tmp_path)
    events = []
    tool = build_skill_tools(str(tmp_path), None)[0]
    agent = MainAgent([tool], on_tool_event=lambda stage, payload: events.append((stage, payload)))
    reset_taint()
    try:
        output = await agent._run_tools([("use_skill", {"name": "helper"})], "plan", lambda _: None)
        assert "路径基准" in output[0][1] and is_tainted()
        await agent._run_tools([("use_skill", {"name": "missing"})], "plan", lambda _: None)
        assert any(stage == "finish" and data["status"] == "failed" for stage, data in events)
    finally:
        reset_taint()


@pytest.mark.asyncio
async def test_tui_uses_shared_skill_boundary_save_and_reload(tmp_path, monkeypatch):
    pytest.importorskip("textual")
    from src.tui.app import VortoCodeTUI
    path = write_skill(tmp_path)
    app = VortoCodeTUI(repo_root=str(tmp_path))
    prompts = []
    async def confirm(message, *, scope): prompts.append((message, scope)); return True
    monkeypatch.setattr(app, "_confirm_always", confirm)
    agent = app._build_main_agent()
    tool = agent.tools["use_skill"]
    original = await tool.handler({"name": "helper"})
    assert "来源：.vortocode/skills/helper/SKILL.md" in original and "版本摘要" in original
    path.write_text("changed instructions")
    assert (await tool.handler({"name": "helper"}))["ok"] is False
    registry = app._skill_registry()
    assert app._skill_registry(reload=True) is registry
    assert "changed instructions" in await tool.handler({"name": "helper"})
    description = "步骤: 描述 #保留\n第二行"
    await agent.tools["save_skill"].handler({"name": "new-skill", "description": description, "instructions": "new body"})
    assert prompts[-1][1] == "skill" and registry.get("new-skill").description == description
    assert "new body" in await tool.handler({"name": "new-skill"})


def test_tui_registry_rejects_outside_sources(tmp_path):
    pytest.importorskip("textual")
    from src.tui.app import VortoCodeTUI
    root = tmp_path / "project"; root.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: escaped\n---\nOUTSIDE_MARKER")
    (root / "skills").symlink_to(outside, target_is_directory=True)
    assert VortoCodeTUI(repo_root=str(root))._skill_registry().get("escaped") is None
