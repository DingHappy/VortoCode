"""Opt-in MCP permission probes using the real manager and a synthetic transport."""
import pytest


@pytest.fixture
def mcp_permission_probe(tmp_path, monkeypatch):
    import yaml
    import src.tools.manager as module
    from src.tools.mcp_client import MCPTool, MCPToolResult, MCPTransport
    from src.agents.mcp_tools import wrap_mcp_manager

    class Client:
        server_name = 'probe'
        transport = MCPTransport.HTTP
        connected = False
        calls = 0
        async def connect(self): self.connected = True; return True
        async def disconnect(self): self.connected = False
        async def list_tools(self): return [MCPTool(name='act', server_name='probe')]
        async def call_tool(self, call): self.calls += 1; return MCPToolResult(success=True, output='probe executed')
    client = Client()
    monkeypatch.setattr(module, 'create_mcp_client', lambda *a, **k: client)
    async def connect(confirm=None, action='allow'):
        path = tmp_path / 'mcp-probe.yaml'
        path.write_text(yaml.safe_dump({'servers': [{'name': 'probe', 'transport': 'http', 'url': 'https://fixture.example.test/mcp'}],
            'permissions': {'rules': [{'id': 'probe', 'action': action, 'tool_names': ['act'], 'priority': 999}]}}))
        manager = module.ToolManager(str(path), confirm=confirm)
        assert await manager.initialize()
        return manager, wrap_mcp_manager(manager), client
    return connect
