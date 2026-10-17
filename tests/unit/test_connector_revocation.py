"""Exercise revoke -> agent/registry removal -> actual transport guard with fake I/O."""
import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.agents.agent_loop import MainAgent
from src.agents.mcp_session import connect_agent, snapshot
from src.tools.mcp_client import MCPTool, MCPToolResult, MCPTransport
from src.workflow.workbench import connectors


class Client:
    transport = MCPTransport.HTTP
    def __init__(self, name):
        self.server_name, self.connected, self.calls, self.closed = name, False, [], 0
        self.connect_wait = self.call_wait = self.close_wait = None
        self.close_error = False
    async def connect(self):
        if self.connect_wait:
            await self.connect_wait()
        self.connected = True
        return True
    async def disconnect(self):
        if self.close_wait:
            await self.close_wait()
        if self.close_error:
            raise ValueError("synthetic-private-credential")
        self.connected = False
        self.closed += 1
    async def list_tools(self):
        return [MCPTool(name="read", server_name=self.server_name, annotations={"readOnlyHint": True})]
    async def call_tool(self, call):
        self.calls.append(call.arguments)
        if self.call_wait:
            await self.call_wait()
        return MCPToolResult(success=True, output="synthetic-remote-result")


@pytest_asyncio.fixture
async def setup(tmp_path, monkeypatch):
    from src.web.routers import assistant, realtime
    monkeypatch.setattr(connectors.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/mcp.yaml").write_text(
        "servers:\n  - name: personal\n    transport: http\n    url: https://private.example.test/mcp\n"
        "    credential: personal\n    read_only: true\n    oauth:\n      provider: github\n      client_id: Iv23liExampleClient1\n"
        "  - name: public\n    transport: http\n    url: https://public.example.test/mcp\n    credentialed: false\n"
        "permissions:\n  rules:\n    - id: read\n      action: allow\n      scope: global\n      tool_names: [read]\n      priority: 100\n")
    connectors._PENDING.clear()
    connectors._AUTH_ATTEMPTS.clear()
    connectors._save_token("personal", "synthetic-private-credential", None)
    agents, clients = [], []
    def create(name, *args, **kwargs):
        client = Client(name)
        clients.append(client)
        return client
    monkeypatch.setattr("src.tools.manager.create_mcp_client", create)
    sessions = {}
    monkeypatch.setattr(realtime, "_SESSIONS", sessions)
    async def make_agent():
        agent = MainAgent([])
        from src.agents.gate import make_confirm_gate
        agent._confirm_gate = make_confirm_gate(auto_approve=True)
        agent._capabilities = SimpleNamespace(profile="external")
        agents.append(agent)
        sessions[str(len(agents))] = {"agent": agent}
        await connect_agent(agent, str(tmp_path), workspace_scope="general")
        return agent
    context = SimpleNamespace(root=str(tmp_path), agents=agents, clients=clients, make_agent=make_agent,
                              sessions=sessions, route=assistant.connector_disconnect)
    yield context
    tasks = [row["task"] for row in connectors._PENDING.values() if row.get("task")]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    connectors._PENDING.clear()
    connectors._AUTH_ATTEMPTS.clear()
    for client in clients:
        client.close_wait = None
        client.close_error = False
    for agent in agents:
        if getattr(agent, "_mcp_mgr", None):
            await agent._mcp_mgr.shutdown()


async def test_revoke_removes_tools_from_all_sessions_and_keeps_unrelated_service(setup):
    c = setup
    agents = [await c.make_agent(), await c.make_agent()]
    captured = [a.tools["mcp__personal__read"] for a in agents]
    assert await captured[0].handler({}) == "synthetic-remote-result"
    receipt = await c.route("personal")
    assert receipt["calls_blocked"] and receipt["sessions_updated"] == 2
    assert receipt["connections_disabled"] == 2 and receipt["cleanup_status"] == "confirmed"
    assert receipt["credential_cleanup_confirmed"] and not receipt["remote_requests_verified"]
    assert not receipt["provider_authorization_revoked"]
    for agent, stale in zip(agents, captured):
        assert "mcp__personal__read" not in agent.tools
        assert not any(t.name == "mcp__personal__read" for t in agent._tool_list)
        assert (await stale.handler({}))["ok"] is False
        assert await agent.tools["mcp__public__read"].handler({}) == "synthetic-remote-result"
        assert snapshot(agent)["status"] == "partial" and snapshot(agent)["tool_count"] == 1
    assert "synthetic-private-credential" not in json.dumps(receipt)
    assert c.root not in json.dumps(receipt)


async def test_captured_tool_cannot_resume_after_confirmation_on_revoked_connector(setup):
    c = setup
    agent = await c.make_agent()
    entered, release = asyncio.Event(), asyncio.Event()
    async def confirm(_):
        entered.set()
        await release.wait()
        return True
    manager = agent._mcp_mgr
    manager.tool_executor.confirm = confirm
    manager.permission_manager.check_permission = lambda *a: SimpleNamespace(requires_confirmation=True, allowed=False, reason="fixture")
    call = asyncio.create_task(agent.tools["mcp__personal__read"].handler({}))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await c.route("personal")
        release.set()
        assert (await call)["ok"] is False
        assert not next(client for client in c.clients if client.server_name == "personal").calls
    finally:
        release.set()
        if not call.done(): call.cancel()
        await asyncio.gather(call, return_exceptions=True)


async def test_inflight_result_is_discarded_and_not_claimed_remotely_cancelled(setup):
    c = setup
    agent = await c.make_agent()
    entered, release = asyncio.Event(), asyncio.Event()
    async def wait():
        entered.set(); await release.wait()
    client = next(client for client in c.clients if client.server_name == "personal")
    client.call_wait = wait
    call = asyncio.create_task(agent.tools["mcp__personal__read"].handler({}))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        receipt = await c.route("personal")
        release.set()
        result = await call
        assert result["ok"] is False and "synthetic-remote-result" not in json.dumps(result)
        assert receipt["remote_requests_verified"] is False
    finally:
        release.set()
        if not call.done(): call.cancel()
        await asyncio.gather(call, return_exceptions=True)


@pytest.mark.parametrize("change", ["delete", "replace", "permissions"])
async def test_external_credential_changes_invalidate_already_captured_handler(setup, change):
    c = setup
    agent = await c.make_agent()
    handler = agent.tools["mcp__personal__read"].handler
    token = connectors._token_path("personal")
    if change == "delete": token.unlink()
    elif change == "permissions": token.chmod(0o644)
    else: connectors._save_token("personal", "synthetic-private-credential", None)
    assert (await handler({}))["ok"] is False
    assert not next(client for client in c.clients if client.server_name == "personal").calls


async def test_new_credentials_allow_new_connection_but_never_revive_old_access(setup):
    c = setup
    old = await c.make_agent()
    handler = old.tools["mcp__personal__read"].handler
    await c.route("personal")
    connectors._save_token("personal", "synthetic-private-credential", None)
    new = await c.make_agent()
    assert (await handler({}))["ok"] is False
    assert await new.tools["mcp__personal__read"].handler({}) == "synthetic-remote-result"


async def test_cleanup_failure_is_redacted_and_never_restores_access(setup):
    c = setup
    agent = await c.make_agent()
    captured = agent.tools["mcp__personal__read"].handler
    next(client for client in c.clients if client.server_name == "personal").close_error = True
    receipt = await c.route("personal")
    assert receipt["cleanup_status"] == "unconfirmed" and receipt["calls_blocked"]
    assert snapshot(agent)["cleanup_status"] == "unconfirmed"
    assert (await captured({}))["ok"] is False
    assert "synthetic-private-credential" not in json.dumps(receipt)


async def test_cleanup_timeout_has_no_remote_stop_claim(setup, monkeypatch):
    from src.tools import connector_access
    c = setup
    agent = await c.make_agent()
    monkeypatch.setattr(connector_access, "CLEANUP_TIMEOUT", 0.01)
    async def wait(): await asyncio.Event().wait()
    next(client for client in c.clients if client.server_name == "personal").close_wait = wait
    receipt = await c.route("personal")
    assert receipt["calls_blocked"] and receipt["cleanup_status"] == "unconfirmed"
    assert "mcp__personal__read" not in agent.tools


async def test_revoke_during_connection_cannot_attach_or_leave_late_resources(setup, monkeypatch):
    c = setup
    entered, release = asyncio.Event(), asyncio.Event()
    original = Client.connect
    async def delayed(client):
        if client.server_name == "personal":
            entered.set(); await release.wait()
        return await original(client)
    monkeypatch.setattr(Client, "connect", delayed)
    task = asyncio.create_task(c.make_agent())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        receipt = await c.route("personal")
        assert receipt["cleanup_status"] == "unconfirmed"
        release.set()
        with pytest.raises(RuntimeError): await task
        agent = c.agents[0]
        assert "mcp__personal__read" not in agent.tools and not agent._mcp_connecting
        assert snapshot(agent)["status"] != "connected"
        assert not next(client for client in c.clients if client.server_name == "personal").connected
    finally:
        release.set()
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_alias_servers_using_same_credential_are_revoked_together(setup):
    from pathlib import Path
    c = setup
    path = Path(c.root) / "config/mcp.yaml"
    import yaml
    cfg = yaml.safe_load(path.read_text())
    cfg["servers"].append({"name": "alias", "transport": "http", "url": "https://alias.example.test/mcp", "credential": "personal"})
    path.write_text(yaml.safe_dump(cfg))
    agent = await c.make_agent()
    receipt = await c.route("personal")
    assert receipt["connections_disabled"] == 2
    assert set(agent.tools) == {"mcp__public__read"}


async def test_failed_file_deletion_cannot_reenable_old_credentials(setup, monkeypatch):
    from pathlib import Path
    c = setup
    agent = await c.make_agent()
    token = connectors._token_path("personal")
    original = Path.unlink
    def blocked(path, *args, **kwargs):
        if path == token: raise PermissionError("synthetic-private-credential")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", blocked)
    receipt = await c.route("personal")
    assert receipt["calls_blocked"] and not receipt["credential_cleanup_confirmed"]
    assert "mcp__personal__read" not in agent.tools
    assert connectors.status(c.root)[0]["locally_revoked"]
    with pytest.raises(PermissionError):
        await c.make_agent()
    assert "synthetic-private-credential" not in json.dumps(receipt)


async def test_removing_config_does_not_prevent_revoking_existing_connections(setup):
    from pathlib import Path
    c = setup
    agent = await c.make_agent()
    (Path(c.root) / "config/mcp.yaml").unlink()
    receipt = await c.route("personal")
    assert receipt["calls_blocked"] and "mcp__personal__read" not in agent.tools
    assert receipt["cleanup_status"] == "confirmed"


async def test_cancelling_cleanup_leaves_calls_disabled_and_observation_unconfirmed(setup):
    c = setup
    agent = await c.make_agent()
    entered = asyncio.Event()
    async def wait(): entered.set(); await asyncio.Event().wait()
    next(client for client in c.clients if client.server_name == "personal").close_wait = wait
    task = asyncio.create_task(c.route("personal"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert "mcp__personal__read" not in agent.tools
        assert snapshot(agent)["cleanup_status"] == "unconfirmed"
    finally:
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_web_optin_uses_same_attachment_and_revocation(setup, monkeypatch):
    from src.web.routers import realtime
    c = setup
    monkeypatch.setenv("VORTOCODE_WEB_MCP", "1")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    agent = MainAgent([])
    agent._capabilities = SimpleNamespace(profile="external")
    c.agents.append(agent); c.sessions["opt-in"] = {"agent": agent}
    await realtime._ensure_mcp(agent, lambda _: None)
    stale = agent.tools["mcp__personal__read"]
    await c.route("personal")
    assert "mcp__personal__read" not in agent.tools and (await stale.handler({}))["ok"] is False


async def test_late_cleanup_cannot_overwrite_a_new_authorized_connection(setup):
    c = setup
    agent = await c.make_agent()
    entered, release = asyncio.Event(), asyncio.Event()
    async def close(): entered.set(); await release.wait()
    next(client for client in c.clients if client.server_name == "personal").close_wait = close
    task = asyncio.create_task(c.route("personal"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        connectors._save_token("personal", "synthetic-new-credential", None)
        await connect_agent(agent, c.root, workspace_scope="general")
        observed = snapshot(agent)["observed_at"]
        release.set(); await task
        state = snapshot(agent)
        assert state["status"] == "connected" and state["observed_at"] == observed
        assert "cleanup_status" not in state
        assert await agent.tools["mcp__personal__read"].handler({}) == "synthetic-remote-result"
    finally:
        release.set()
        if not task.done(): task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_direct_captured_client_also_checks_file_identity(setup):
    from src.tools.mcp_client import MCPToolCall
    c = setup
    agent = await c.make_agent()
    client = agent._mcp_mgr.mcp_clients["personal"]
    connectors._token_path("personal").unlink()
    with pytest.raises(PermissionError): await client.call_tool(MCPToolCall(tool_name="read", arguments={}))
    assert not next(raw for raw in c.clients if raw.server_name == "personal").calls


async def test_rotation_between_header_read_and_connection_is_rejected(setup, monkeypatch):
    from src.agents import mcp_tools
    c = setup
    original = mcp_tools.load_connector_secret
    def rotate(name):
        value = original(name)
        connectors._save_token(name, "synthetic-replacement", None)
        return value
    monkeypatch.setattr(mcp_tools, "load_connector_secret", rotate)
    with pytest.raises(PermissionError): await c.make_agent()
    assert not c.clients


@pytest.mark.parametrize("name", [None, "bad__source", "a" * 57])
async def test_invalid_server_identity_is_rejected_before_authenticated_requests(setup, name):
    import yaml
    from pathlib import Path
    c = setup
    path = Path(c.root) / "config/mcp.yaml"
    cfg = yaml.safe_load(path.read_text()); cfg["servers"][0]["name"] = name
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError): await c.make_agent()
    assert not c.clients


async def test_duplicate_identity_cannot_bind_credentials_to_another_service(setup):
    import yaml
    from pathlib import Path
    c = setup
    path = Path(c.root) / "config/mcp.yaml"
    cfg = yaml.safe_load(path.read_text())
    cfg["servers"][1]["name"] = "personal"
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError): await c.make_agent()
    assert not c.clients
