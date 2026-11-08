"""Offline Android attachment, policy, identity and uncertain-dispatch recovery."""
import asyncio
from dataclasses import replace
import json
import os

import pytest

from src.agents.gate import make_confirm_gate
from src.agents.tools.android import android_control_enabled, build_android_tools, shutdown_android_sessions
from src.agents.trust import FULL
from src.devices.android import AndroidBinding, AndroidError, AppiumAndroid, load_binding
from src.devices.android_state import AndroidState
from src.devices.android_ui import project
from src.gateway.tool_summary import tool_summary


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    await shutdown_android_sessions()


@pytest.fixture
def binding():
    return AndroidBinding("http://127.0.0.1:4723", "synthetic-session", "emulator-5554", "com.example.demo", "g1", "测试模拟器")


def xml(*, text="Demo", secret=False, editor=False, foreign=False):
    source = ('<hierarchy><node package="com.example.demo" class="android.widget.TextView" '
              f'text="{text}" clickable="true" enabled="true" bounds="[10,20][100,80]" password="{str(secret).lower()}"/>'
              + ('<node package="com.example.demo" class="android.widget.EditText" text="synthetic-hidden-value" bounds="[0,100][100,120]"/>' if editor else "")
              + ('<node package="com.android.permissioncontroller" text="Allow" clickable="true"/>' if foreign else "")
              + '</hierarchy>')
    return source


class FakeAppium:
    def __init__(self, binding):
        self.binding = binding
        self.caps = {"platformName": "Android", "automationName": "UiAutomator2", "udid": binding.udid, "appPackage": binding.package}
        self.foreground = binding.package
        self.context = "NATIVE_APP"
        self.source = xml()
        self.calls = []
        self.post_error = None
        self.post_wait = None
        self.post_started = asyncio.Event()
        self.client = AppiumAndroid(binding, requester=self.request)

    async def request(self, method, url, payload):
        suffix = url.split("/session/" + self.binding.session_id)[1]
        self.calls.append((method, suffix, payload))
        if method == "POST":
            self.post_started.set()
            if self.post_wait:
                await self.post_wait.wait()
            if self.post_error:
                raise self.post_error
            return None
        return {"": self.caps, "/context": self.context, "/appium/device/current_package": self.foreground, "/source": self.source}[suffix]

    @property
    def posts(self):
        return [call for call in self.calls if call[0] == "POST"]


async def yes(message, kind=None):
    assert kind == "interact"
    return True


def setup(tmp_path, binding, confirm=yes, fake=None, loader=None):
    fake = fake or FakeAppium(binding)
    entries = build_android_tools(str(tmp_path / "workspace"), confirm,
        binding_loader=loader or (lambda: binding), driver_factory=lambda _binding: fake.client,
        state_root=tmp_path / "host" / "android-control")
    return {tool.name: tool for tool in entries}, fake


async def frame(entries):
    await entries["android_resume"].handler({})
    return json.loads(await entries["android_snapshot"].handler({}))


def test_gate_requires_explicit_desktop_local_project_with_human(monkeypatch):
    args = dict(profile="local", can_ask_human=True, workspace_scope="project")
    assert not android_control_enabled("web", **args)
    monkeypatch.setenv("VORTOONE_ENABLE_ANDROID_CONTROL", "1")
    assert not android_control_enabled("web", **args)
    monkeypatch.setenv("VORTOONE_DESKTOP_SIDECAR", "1")
    assert android_control_enabled("web", **args)
    for kind in ("im", "cli", "unattended"):
        assert not android_control_enabled(kind, **args)
    for changes in ({"profile": "external"}, {"profile": "unattended"}, {"can_ask_human": False}, {"workspace_scope": "general"}, {"workspace_scope": "scratch"}):
        assert not android_control_enabled("web", **{**args, **changes})


@pytest.mark.parametrize("changes", [
    {"endpoint": "http://localhost:4723"}, {"endpoint": "https://remote.example:443"},
    {"endpoint": "http://127.0.0.1:4723/evil"}, {"endpoint": "http://user:secret@127.0.0.1:4723"},
    {"endpoint": "http://127.0.0.1:4723?token=secret"}, {"session_id": "../other"},
    {"package": "../evil"}, {"udid": "-shell"}, {"generation": ""},
])
def test_target_rejects_remote_endpoints_and_route_injection(binding, changes):
    with pytest.raises(AndroidError):
        replace(binding, **changes)


def test_host_config_is_private_strict_scoped_and_not_a_project_plugin(tmp_path, binding):
    host = tmp_path / "host"; host.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"; workspace.mkdir()
    path = host / "android.json"
    raw = {"version": 1, "workspace": str(workspace), **binding.__dict__}
    path.write_text(json.dumps(raw)); path.chmod(0o600)
    assert load_binding(str(workspace), path) == binding
    with pytest.raises(AndroidError):
        load_binding(str(tmp_path / "other"), path)
    project_config = workspace / "config.json"; project_config.write_bytes(path.read_bytes())
    with pytest.raises(AndroidError):
        load_binding(str(workspace), project_config)
    link = host / "linked.json"; link.symlink_to(path)
    with pytest.raises(AndroidError):
        load_binding(str(workspace), link)
    if os.name != "nt":
        path.chmod(0o644)
        with pytest.raises(AndroidError):
            load_binding(str(workspace), path)
        path.chmod(0o600)
    path.write_text(json.dumps({**raw, "capabilities": {"appium:app": "not-allowed"}}))
    with pytest.raises(AndroidError):
        load_binding(str(workspace), path)
    path.write_text('{"version":1,"version":1}')
    with pytest.raises(AndroidError):
        load_binding(str(workspace), path)


@pytest.mark.parametrize("kind,profile,scope,human,role,expected", [
    ("web", "local", "project", True, "standard", True),
    ("web", "external", "project", True, "standard", False),
    ("web", "unattended", "project", True, "standard", False),
    ("web", "local", "general", True, "standard", False),
    ("web", "local", "project", False, "standard", False),
    ("im", "local", "project", True, "standard", False),
    ("cli", "local", "project", True, "standard", False),
    ("web", "local", "project", True, "research_read", False),
])
def test_production_session_assembly_preserves_entrypoint_boundaries(tmp_path, monkeypatch, kind, profile, scope, human, role, expected):
    from src.gateway.agent_session import build_session
    monkeypatch.setenv("VORTOONE_ENABLE_ANDROID_CONTROL", "1")
    monkeypatch.setenv("VORTOONE_DESKTOP_SIDECAR", "1")
    agent = build_session(str(tmp_path), kind=kind, confirm=yes, can_ask_human=human,
                          capability_profile=profile, workspace_scope=scope, session_role=role)
    names = {name for name in agent.tools if name.startswith("android_")}
    assert bool(names) is expected
    if expected:
        assert len(names) == 5 and all("_" not in tool_summary(name, {}) for name in names)


async def test_no_network_before_explicit_human_resume_and_no_raw_editor_values(tmp_path, binding):
    entries, fake = setup(tmp_path, binding)
    assert "暂停" in str(await entries["android_snapshot"].handler({}))
    status = await entries["android_status"].handler({})
    assert "synthetic-session" not in status and not fake.calls
    fake.source = xml(editor=True)
    page = await frame(entries)
    assert "synthetic-hidden-value" not in json.dumps(page)
    assert page["items"][0]["tap"] and not page["items"][1]["tap"]
    assert all(tool.serial and tool.external_content and tool.untrusted_source for tool in entries.values())
    assert all("_" not in tool_summary(name, {}) for name in entries)


async def test_tap_always_confirms_at_full_trust_and_deny_sends_nothing(tmp_path, binding):
    asked = []
    async def ask(message):
        asked.append(message)
        return "点按：" not in message
    gate = make_confirm_gate(ask, can_ask_human=True, trust_level=FULL, capability_profile="local")
    entries, fake = setup(tmp_path, binding, gate)
    page = await frame(entries)
    reply = await entries["android_tap"].handler({"frame": page["frame"], "ref": 1})
    assert "未同意" in reply and any("点按：" in value for value in asked) and not fake.posts


async def test_successful_tap_is_single_use_and_uses_bounded_w3c_touch(tmp_path, binding):
    entries, fake = setup(tmp_path, binding)
    page = await frame(entries)
    args = {"frame": page["frame"], "ref": 1, "x": 999, "module": "/tmp/not-loaded"}
    assert "请求已确认" in await entries["android_tap"].handler(args)
    assert "失效" in await entries["android_tap"].handler(args) and len(fake.posts) == 1
    pointer = fake.posts[0][2]["actions"][0]
    assert pointer["parameters"] == {"pointerType": "touch"}
    assert pointer["actions"][0]["x"] == 55 and pointer["actions"][0]["y"] == 50
    assert not list((tmp_path / "host").rglob("pending.json"))


@pytest.mark.parametrize("change", ["config", "device", "foreground", "frame"])
async def test_change_while_awaiting_confirmation_prevents_dispatch(tmp_path, binding, change):
    current = [binding]
    fake = FakeAppium(binding)
    async def ask(message, kind=None):
        if "点按：" in message:
            if change == "config": current[0] = replace(binding, generation="g2")
            if change == "device": fake.caps["udid"] = "different-device"
            if change == "foreground": fake.foreground = "com.android.settings"
            if change == "frame": fake.source = xml(text="changed")
        return True
    entries, _ = setup(tmp_path, binding, ask, fake, lambda: current[0])
    page = await frame(entries)
    reply = await entries["android_tap"].handler({"frame": page["frame"], "ref": 1})
    assert "已确认" not in reply and not fake.posts


@pytest.mark.parametrize("change", ["udid", "context", "password", "foreign", "webview"])
async def test_identity_auth_and_foreign_ui_pause_projection(tmp_path, binding, change):
    entries, fake = setup(tmp_path, binding)
    await entries["android_resume"].handler({})
    if change == "udid": fake.caps["udid"] = "wrong"
    if change == "context": fake.context = "WEBVIEW_com.example.demo"
    if change == "password": fake.source = xml(text="synthetic-secret", secret=True)
    if change == "foreign": fake.source = xml(foreign=True)
    if change == "webview": fake.source = xml().replace("TextView", "WebView")
    reply = await entries["android_snapshot"].handler({})
    assert "synthetic-secret" not in str(reply) and "已暂停" in str(reply)
    before = len(fake.calls)
    await entries["android_snapshot"].handler({})
    assert len(fake.calls) == before


async def test_uncertain_dispatch_survives_detach_and_new_session_until_human_reconciles(tmp_path, binding):
    entries, fake = setup(tmp_path, binding)
    page = await frame(entries)
    fake.post_error = TimeoutError("synthetic unknown result")
    assert "已暂停" in str(await entries["android_tap"].handler({"frame": page["frame"], "ref": 1}))
    marker = next((tmp_path / "host").rglob("pending.json"))
    assert "synthetic-session" not in marker.read_text() and json.loads(marker.read_text())["application"] == binding.package
    await entries["android_detach"].handler({})
    rejected = []
    async def no(message, kind=None):
        rejected.append(message)
        return False
    later, later_fake = setup(tmp_path, binding, no)
    assert "needs_reconciliation" in await later["android_status"].handler({})
    await later["android_resume"].handler({})
    assert "上次点按结果不明" in rejected[0] and not later_fake.calls and marker.exists()
    await shutdown_android_sessions()
    recovered, recovered_fake = setup(tmp_path, binding)
    await recovered["android_resume"].handler({})
    assert not marker.exists() and not recovered_fake.posts and len(fake.posts) == 1


async def test_cancelled_action_retains_pending_and_releases_only_after_drain(tmp_path, binding):
    entries, fake = setup(tmp_path, binding)
    page = await frame(entries)
    fake.post_wait = asyncio.Event()
    task = asyncio.create_task(entries["android_tap"].handler({"frame": page["frame"], "ref": 1}))
    await fake.post_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list((tmp_path / "host").rglob("pending.json"))
    assert "暂停" in await entries["android_tap"].handler({"frame": page["frame"], "ref": 1})
    assert len(fake.posts) == 1


async def test_two_endpoints_and_apps_cannot_share_a_device_owner(tmp_path, binding):
    first, _ = setup(tmp_path, binding)
    await first["android_resume"].handler({})
    other_binding = replace(binding, endpoint="http://127.0.0.1:4725", session_id="other-session", package="com.example.other")
    second, fake = setup(tmp_path, other_binding)
    assert "已暂停" in str(await second["android_resume"].handler({})) and not fake.calls
    await first["android_detach"].handler({})
    assert "主人已确认" in await second["android_resume"].handler({})


async def test_shutdown_waits_for_active_read_and_prevents_its_confirmation_from_resuming(tmp_path, binding):
    entries, fake = setup(tmp_path, binding)
    started, release = asyncio.Event(), asyncio.Event()
    async def slow_verify():
        started.set()
        await release.wait()
    fake.client.verify = slow_verify
    opening = asyncio.create_task(entries["android_resume"].handler({}))
    await started.wait()
    closing = asyncio.create_task(shutdown_android_sessions())
    await asyncio.sleep(0)
    try:
        assert not closing.done()
        state = AndroidState(str(tmp_path / "workspace"), binding, root=tmp_path / "host" / "android-control")
        with pytest.raises(RuntimeError):
            state.acquire()
    finally:
        release.set()
        await opening; await closing
    assert "关闭" in await entries["android_status"].handler({})


@pytest.mark.parametrize("bad", ["<!DOCTYPE x><hierarchy/>", "<broken", "<hierarchy/>", "x" * (256 * 1024 + 1)])
def test_invalid_native_hierarchy_is_rejected(bad):
    with pytest.raises(AndroidError):
        project(bad, "com.example.demo")


def test_native_editor_children_cannot_echo_typed_values():
    raw = ('<hierarchy><node package="com.example.demo" class="android.widget.EditText" text="synthetic-hidden">'
           '<node package="com.example.demo" class="android.widget.TextView" text="synthetic-hidden"/>'
           '</node></hierarchy>')
    _digest, rows = project(raw, "com.example.demo")
    assert "synthetic-hidden" not in json.dumps(rows) and len(rows) == 1 and not rows[0]["tap"]


async def test_pending_corruption_is_not_cleared_by_resume(tmp_path, binding):
    state = AndroidState(str(tmp_path / "workspace"), binding, root=tmp_path / "host" / "android-control")
    state.acquire(); state.begin(); state.pending.write_text("broken"); state.release()
    entries, fake = setup(tmp_path, binding)
    assert "已暂停" in str(await entries["android_resume"].handler({}))
    assert state.pending.read_text() == "broken" and not fake.calls


async def test_runtime_marks_transport_failure_failed_without_exposing_raw_errors(tmp_path, binding):
    from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime
    entries, fake = setup(tmp_path, binding)
    await frame(entries)
    fake.caps["udid"] = "wrong-device"
    runtime = ToolRuntime(lambda: entries, ToolExecutionContext())
    outcome = await runtime.execute("android_snapshot", {}, mode="plan")
    assert outcome.status == "failed" and "已暂停" in outcome.text and "wrong-device" not in outcome.text
