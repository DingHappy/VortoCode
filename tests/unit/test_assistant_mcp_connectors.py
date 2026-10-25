"""Manual assistant connections use the real policy/registry with a fake transport."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from src.agents.agent_loop import MainAgent
from src.agents.mcp_session import connect_agent, snapshot
from src.tools.mcp_client import MCPTool, MCPTransport
from src.web.deps import HTTPException


class Client:
    server_name = "fixture"
    transport = MCPTransport.HTTP
    connected = False

    async def connect(self):
        self.connected = True
        return True

    async def disconnect(self):
        self.connected = False

    async def list_tools(self):
        return [MCPTool(name=name, server_name=self.server_name, annotations={"readOnlyHint": readonly})
                for name, readonly in (("read", True), ("write", False), ("unknown", None))]

    async def call_tool(self, *args):
        pytest.fail("Connecting a catalogue must not execute any tool")


@pytest.fixture
def configured(tmp_path, monkeypatch):
    from src.agents import mcp_tools
    from src.web.routers import extensions, realtime
    root = tmp_path / "managed"
    (root / "config").mkdir(parents=True)
    (root / "config/mcp.yaml").write_text(
        "servers:\n  - name: fixture\n    transport: http\n"
        "    url: https://connector.example.test/mcp\n    credential: fixture\n    read_only: true\n")
    home = tmp_path / "home"
    directory = home / ".vortocode/connectors"
    directory.mkdir(parents=True)
    secret = directory / "fixture.token"
    secret.write_text("synthetic-private-credential\n")
    secret.chmod(0o600)
    monkeypatch.setattr(mcp_tools.Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(root)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    agent = MainAgent([])
    agent._capabilities = SimpleNamespace(profile="external")
    record = {"agent": agent}
    actors = SimpleNamespace(busy=lambda _: False)
    monkeypatch.setattr(realtime, "_SESSIONS", {"sid-fixture": record})
    monkeypatch.setattr(extensions, "_mcp_session", lambda _: ("sid-fixture", record, agent, actors))
    clients, requests = [], []
    def create(*args, **kwargs):
        requests.append(kwargs)
        client = Client()
        clients.append(client)
        return client
    monkeypatch.setattr("src.tools.manager.create_mcp_client", create)
    return SimpleNamespace(root=root, agent=agent, record=record, actors=actors, clients=clients,
                           requests=requests, secret=secret, route=extensions.connect_session_mcp)


async def test_manual_general_connection_loads_named_credential_and_keeps_readonly_filter(configured):
    c = configured
    result = await c.route("fixture")
    assert result["status"] == "connected" and result["tool_count"] == 1
    assert c.requests[0]["headers"] == {"Authorization": "Bearer synthetic-private-credential", "X-MCP-Readonly": "true"}
    assert set(c.agent.tools) == {"mcp__fixture__read"}
    tool = c.agent.tools["mcp__fixture__read"]
    assert tool.read_only and tool.untrusted_source and tool.external_content
    assert "synthetic-private-credential" not in json.dumps(result)
    assert "connector.example.test" not in json.dumps(result)
    await c.route("fixture")
    assert not c.clients[0].connected and c.clients[1].connected
    assert [t.name for t in c.agent._tool_list] == ["mcp__fixture__read"]
    await c.agent.mcp.manager.shutdown()


@pytest.mark.parametrize("scope", ["project", "scratch", "invalid"])
async def test_other_scopes_never_load_connector_secrets(configured, monkeypatch, scope):
    from src.agents import mcp_tools
    c = configured
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", scope)
    monkeypatch.setattr(mcp_tools, "load_connector_secret", lambda _: pytest.fail("Secret read outside General"))
    with pytest.raises(HTTPException) as caught:
        await c.route("fixture")
    assert caught.value.status_code == 409
    assert not c.clients and not c.agent.tools


async def test_unscoped_shared_connector_does_not_inherit_general_environment(configured):
    c = configured
    with pytest.raises(RuntimeError):
        await connect_agent(c.agent, str(c.root))
    assert not c.clients and not c.agent.tools


async def test_local_profile_remains_blocked_even_in_general(configured):
    c = configured
    c.agent._capabilities.profile = "local"
    with pytest.raises(HTTPException) as caught:
        await c.route("fixture")
    assert caught.value.status_code == 403 and not c.clients


@pytest.mark.parametrize("invalid", ["missing", "public"])
async def test_bad_secret_retains_prior_connection_without_leaking_details(configured, invalid):
    c = configured
    await c.route("fixture")
    previous = c.agent.mcp.manager
    observed = snapshot(c.agent)["observed_at"]
    if invalid == "missing":
        c.secret.unlink()
    else:
        c.secret.chmod(0o644)
    with pytest.raises(HTTPException) as caught:
        await c.route("fixture")
    assert caught.value.status_code == 409
    assert c.agent.mcp.manager is previous and c.clients[0].connected
    state = snapshot(c.agent)
    assert state["observed_at"] == observed and state["attempted_at"]
    assert "synthetic-private-credential" not in json.dumps(state)
    assert str(c.root) not in json.dumps(state)
    await previous.shutdown()


async def test_session_replacement_during_connection_closes_candidate(configured, monkeypatch):
    c = configured
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(client):
        entered.set()
        await release.wait()
        client.connected = True
        return True
    monkeypatch.setattr(Client, "connect", delayed)
    pending = asyncio.create_task(c.route("fixture"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        c.record["agent"] = object()
        release.set()
        with pytest.raises(HTTPException) as caught:
            await pending
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    assert caught.value.status_code == 409
    assert not c.clients[0].connected and not c.agent.tools
    assert not c.agent.mcp.connecting


async def test_busy_session_does_not_connect_or_read_secrets(configured, monkeypatch):
    from src.agents import mcp_tools
    c = configured
    c.actors.busy = lambda _: True
    monkeypatch.setattr(mcp_tools, "load_connector_secret", lambda _: pytest.fail("Busy session read a secret"))
    with pytest.raises(HTTPException) as caught:
        await c.route("fixture")
    assert caught.value.status_code == 409 and not c.clients
