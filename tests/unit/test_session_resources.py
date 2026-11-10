"""Current-session display metadata never connects, invokes or authorizes."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from src.agents.tool import Tool, ToolResource, tool_definitions
from src.gateway.session_resources import snapshot

STAMP = "2026-11-01T12:00:00+00:00"


async def forbidden(_args):
    pytest.fail("resource reads must not invoke tool handlers")


def agent(tools=(), *, profile="external", observation=None):
    return SimpleNamespace(tools={t.name: t for t in tools}, _capabilities=SimpleNamespace(profile=profile),
                           mcp=SimpleNamespace(observation=observation, manager=None))


def read(host):
    return snapshot(host, session="sid-owner", nonce="request-1", clock=lambda: STAMP)


def test_observers_are_shared_bounded_and_not_exported_to_model():
    called = []
    resource = ToolResource("browser", "browser", "网站浏览器", lambda: called.append(1) or {
        "state": "needs_human", "approval": "each_action", "account": "工作", "target": "https://example.com",
        "cookie": "synthetic-secret", "path": "/synthetic/private", "detail": "等待真人"})
    tools = [Tool(f"tool{i}", "test", {}, forbidden, resource=resource, operation_kind='read') for i in range(3)]
    result = read(agent(tools))
    assert called == [1]
    assert result["observed_at"] == STAMP
    assert result["items"][0]["account"] == "工作"
    assert "synthetic-secret" not in str(result) and "/synthetic/private" not in str(result)
    assert all(set(row) == {"name", "description", "input_schema"} for row in tool_definitions(tools))


def test_unloaded_session_does_not_imply_resources_are_absent():
    assert read(None)["status"] == "not_loaded" and read(None)["items"] == []
    result = read(agent())
    assert {r["id"]: r["state"] for r in result["items"]} == {
        "browser": "not_provided", "android": "not_provided", "mcp": "disconnected"}
    assert read(SimpleNamespace(tools=None))["truncated"] is True


def test_inventory_exports_only_current_tool_declarations_without_probing_or_credentials():
    tool = Tool("web_search", "synthetic-secret description", {"key": "synthetic-secret"}, forbidden,
                read_only=True, required_capabilities=("web_read",), operation_kind='read')
    result = read(agent([tool]))
    assert result["tool_inventory"] == {"version": 1, "truncated": False, "items": [
        {"name": "web_search", "read_only": True, "required_capabilities": ["web_read"]}]}
    assert "synthetic-secret" not in json.dumps(result)
    assert "tool_inventory" not in read(None)


def test_invalid_or_partial_declarations_remain_unknown_without_boolean_coercion(monkeypatch):
    import src.gateway.session_resources as module
    monkeypatch.setattr(module, "MAX_TOOL_INVENTORY", 1)
    tool = Tool("unknown", "", {}, forbidden, read_only="false", required_capabilities=("bad value",), operation_kind='read')
    result = read(agent([tool, Tool("other", "", {}, forbidden, operation_kind='read')]))["tool_inventory"]
    assert result["truncated"] is True
    assert result["items"] == [{"name": "unknown", "read_only": None, "required_capabilities": None}]
    assert read(SimpleNamespace(tools={"invalid\nname": tool}))["tool_inventory"]["truncated"] is True


def test_observer_failure_and_ambiguous_identity_never_expose_errors_or_grant_access():
    def broken():
        raise RuntimeError("synthetic-secret exception")
    first = ToolResource("browser", "browser", "first", lambda: {"state": "active", "approval": "each_action", "account": "private"})
    duplicate = ToolResource("browser", "browser", "second", broken)
    other = ToolResource("service:failed", "service", "failed", broken)
    result = read(agent([Tool("a", "", {}, forbidden, resource=first, operation_kind='read'),
                         Tool("b", "", {}, forbidden, resource=duplicate, operation_kind='read'), Tool("c", "", {}, forbidden, resource=other, operation_kind='read')]))
    row = result["items"][0]
    assert row["state"] == row["approval"] == "unknown" and row["account"] == ""
    assert "synthetic-secret" not in str(result)


def test_partial_inventory_does_not_claim_unseen_capabilities_are_unavailable(monkeypatch):
    import src.gateway.session_resources as module
    monkeypatch.setattr(module, "MAX_TOOLS", 1)
    result = read(agent([Tool("first", "", {}, forbidden, operation_kind='read'), Tool("second", "", {}, forbidden, operation_kind='read')]))
    assert result["truncated"] and all(r["state"] != "not_provided" for r in result["items"])


def test_resource_count_limit_and_reserved_identity(monkeypatch):
    import src.gateway.session_resources as module
    monkeypatch.setattr(module, "MAX_RESOURCES", 5)
    tools = [Tool(f"t{i}", "", {}, forbidden, resource=ToolResource(f"service:{i}", "service", "service",
            lambda: {"state": "loaded"}), operation_kind='read') for i in range(10)]
    result = read(agent(tools))
    assert result["truncated"] and len(result["items"]) <= 5
    spoof = Tool("spoof", "", {}, forbidden, resource=ToolResource("mcp", "service", "spoof", lambda: pytest.fail("reserved id")), operation_kind='read')
    assert read(agent([spoof]))["truncated"]


def test_mcp_uses_original_connection_observation_time_and_scope():
    observed = {"status": "connected", "tool_count": 1, "detail": "目录已读取，不代表业务验收", "observed_at": STAMP}
    result = read(agent(observation=observed))
    assert result["items"][-1]["checked_at"] == STAMP
    assert read(agent(profile="local", observation=observed))["items"][-1]["state"] == "blocked"


def test_native_observers_do_not_find_launch_or_load_device_configuration(tmp_path, monkeypatch):
    from src.agents.tools.browser import build_browser_tools
    from src.agents.tools.android import build_android_tools
    from src.browser.connections import BrowserConnections
    from src.browser import cdp
    monkeypatch.setattr(cdp, "find_browser", lambda: pytest.fail("must not detect browser"))
    registry = BrowserConnections(str(tmp_path / "project"), state_root=tmp_path / "metadata")
    tools = build_browser_tools(registry.workspace, None, connections=registry)
    tools += build_android_tools(registry.workspace, None,
        binding_loader=lambda: pytest.fail("must not load binding for unattached view"),
        driver_factory=lambda _: pytest.fail("must not create driver"))
    rows = {r["id"]: r for r in read(agent(tools))["items"]}
    assert rows["browser"]["state"] == "idle" and rows["android"]["state"] == "not_attached"
    assert not registry.root.exists()


def test_service_view_does_not_read_credentials_or_probe_account(monkeypatch):
    from src.plugins import Plugin, build_plugin_tools
    from src.agents import mcp_tools
    monkeypatch.setattr(mcp_tools, "load_connector_secret", lambda *a: pytest.fail("must not read secret"))
    plugin = Plugin("demo", "Demo", "", lambda: [Tool("demo_read", "", {}, forbidden, operation_kind='read')],
                    check=lambda: pytest.fail("must not check service"))
    tools = build_plugin_tools([plugin])
    row = read(agent(tools))["items"][0]
    assert row["id"] == "service:demo" and row["state"] == "loaded"
    assert row["account"] == "" and row["approval"] == "tool_policy"


async def test_browser_observation_is_available_during_handoff_without_reading_the_page(tmp_path, monkeypatch):
    from src.agents import web_fetch
    from src.agents.confirmation_context import start_confirmation_turn, website_confirmation
    from src.agents.tools.browser import build_browser_tools
    from src.agents.tools.website_browser import shutdown_browsers
    from src.browser.connections import BrowserConnections
    from tests.unit.test_browser_handoff import Window
    monkeypatch.setattr(web_fetch, "refusal_reason", lambda _host: "")
    registry = BrowserConnections(str(tmp_path / "project"), state_root=tmp_path / "metadata")
    row = registry.approve("https://example.com", "工作", "desktop")
    entered, release = asyncio.Event(), asyncio.Event()
    browser = Window()

    async def approve(_text, _kind=None):
        if website_confirmation()["action"] == "login":
            entered.set()
            await release.wait()
        return True

    tools = build_browser_tools(registry.workspace, approve, browser_factory=lambda: browser, connections=registry)
    host = agent(tools)
    start_confirmation_turn()
    opening = asyncio.create_task(host.tools["browser_open"].handler({"url": "https://example.com", "connection_id": row["id"]}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        view = read(host)["items"][0]
        assert view["state"] == "needs_human" and view["account"] == "工作"
        assert not browser.reads  # Observation does not wait on the login lock or read credentials.
        release.set()
        await opening
        before = list(browser.reads)
        assert read(host)["items"][0]["state"] == "active" and browser.reads == before
        registry.revoke(row["id"])
        assert "撤销" in await host.tools["browser_click"].handler({"ref": 1})
        assert not browser.clicks  # A display observation is never a grant.
    finally:
        opening.cancel()
        await asyncio.gather(opening, return_exceptions=True)
        await shutdown_browsers()


async def test_android_pending_marker_is_projected_without_an_extra_device_request(tmp_path):
    from src.agents.tools.android import shutdown_android_sessions
    from src.devices.android import AndroidBinding
    from tests.unit.test_android_plugin import setup
    binding = AndroidBinding("http://127.0.0.1:4723", "synthetic-session", "emulator-5554", "com.example.demo", "g1", "测试设备")
    entries, fake = setup(tmp_path, binding)
    try:
        await entries["android_resume"].handler({})
        page = json.loads(await entries["android_snapshot"].handler({}))
        fake.post_error = TimeoutError("synthetic uncertain dispatch")
        await entries["android_tap"].handler({"frame": page["frame"], "ref": 1})
        before = list(fake.calls)
        row = next(r for r in read(agent(entries.values()))["items"] if r["id"] == "android")
        assert row["state"] == "needs_reconciliation" and row["device"] == "测试设备"
        assert fake.calls == before and "emulator-5554" not in str(row) and "synthetic-session" not in str(row)
    finally:
        await shutdown_android_sessions()


@pytest.mark.asyncio
async def test_ws_resource_reads_are_correlated_and_cannot_create_or_cross_sessions(tmp_path, monkeypatch):
    from src.gateway import im_service
    from src.web.routers import realtime
    from tests.unit.test_browser_handoff import Client
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(im_service, "is_managed_session", lambda *_: False)
    monkeypatch.setattr(realtime, "_get_session", lambda *_: pytest.fail("must not create session"))
    async def cursor(_key):
        return 0
    monkeypatch.setattr(realtime, "_session_event_cursor", cursor)
    monkeypatch.setattr(realtime, "_SESSIONS", {"sid-owner": {"repo_root": str(tmp_path), "agent": agent()}})
    monkeypatch.setattr(realtime, "_PENDING_CONFIRMS", {"foreign": {"id": "foreign", "session": "sid-other",
                        "website": {"action": "login"}, "created": "", "text": "private"}})
    owner = Client("owner")
    await realtime.handle_websocket_message(owner, {"type": "get_status", "resources": "n1", "session": "other"})
    view = owner.events[-1]["resources"]
    assert view["nonce"] == "n1" and view["session"] == "sid-owner" and "attention_id" not in view
    other = Client("other")
    await realtime.handle_websocket_message(other, {"type": "get_status", "resources": "n2"})
    assert other.events[-1]["resources"]["status"] == "not_loaded"
    realtime._SESSIONS["sid-owner"]["repo_root"] = str(tmp_path / "another-workspace")
    await realtime.handle_websocket_message(owner, {"type": "get_status", "resources": "n3"})
    assert owner.events[-1]["resources"]["status"] == "not_loaded"
    for invalid in (None, True, "", "bad nonce", "x" * 129):
        await realtime.handle_websocket_message(owner, {"type": "get_status", "resources": invalid})
        assert "resources" not in owner.events[-1]
