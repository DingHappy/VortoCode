"""Swappable browser transports keep the original website policy and owner."""
from dataclasses import FrozenInstanceError
import json

import pytest

from src.agents.gate import make_confirm_gate
from src.agents.tools.browser import build_browser_tools
from src.agents.tools.website_browser import shutdown_browsers
from src.agents.trust import FULL
from src.browser.backend import BrowserBackend, BrowserLaunch, chromium_backend
from src.browser.connections import BrowserConnections
from tests.unit.test_browser_control import _FakeBrowser


@pytest.fixture(autouse=True)
async def cleanup(monkeypatch):
    from src.agents import web_fetch
    monkeypatch.setattr(web_fetch, "refusal_reason", lambda host: "")
    yield
    await shutdown_browsers()


@pytest.fixture
def registry(tmp_path):
    return BrowserConnections(str(tmp_path / "project"), state_root=tmp_path / "host")


def backend(name="test_driver", modes=frozenset({"desktop", "mobile"})):
    created = []
    def create(launch):
        driver = _FakeBrowser()
        created.append((launch, driver))
        return driver
    return BrowserBackend(name, 1, modes, create), created


def tools(registry, provider, confirm):
    return {tool.name: tool for tool in build_browser_tools(
        registry.workspace, confirm, connections=registry, backend=provider)}


async def yes(message, kind=None):
    return True


@pytest.mark.parametrize("changes", [
    {"backend_id": "../../other"}, {"backend_id": ""}, {"backend_id": "Test"},
    {"api_version": 2}, {"api_version": True}, {"device_modes": {"native_ios"}},
    {"device_modes": set()}, {"create": None},
])
def test_unknown_identity_version_and_modes_are_rejected_without_loading_code(changes):
    values = dict(backend_id="test_driver", api_version=1,
                  device_modes={"desktop"}, create=lambda launch: None)
    with pytest.raises(ValueError):
        BrowserBackend(**{**values, **changes})


def test_host_declaration_snapshots_modes_and_does_not_start_browser(monkeypatch, tmp_path):
    from src.browser import cdp
    modes = {"desktop"}
    provider, created = backend(modes=modes)
    modes.add("mobile")
    assert not provider.supports("mobile") and not created
    with pytest.raises(FrozenInstanceError):
        provider.backend_id = "different"
    monkeypatch.setattr(cdp, "find_browser", lambda: "/synthetic/chromium")
    builtin = chromium_backend()
    driver = builtin.create(BrowserLaunch(tmp_path / "profile", "mobile"))
    assert isinstance(driver, cdp.CdpBrowser) and not driver.running
    assert driver.profile_dir == tmp_path / "profile" and driver.device_mode == "mobile"
    assert not (tmp_path / "profile").exists()


async def test_alternative_backend_receives_private_launch_and_keeps_full_trust_confirmation(registry):
    provider, created = backend()
    asked = []
    async def ask(message):
        asked.append(message)
        return "点击" not in message
    gate = make_confirm_gate(ask, can_ask_human=True, trust_level=FULL, capability_profile="local")
    entries = tools(registry, provider, gate)
    row = registry.approve("https://example.com", "个人", "mobile")
    assert not created
    args = {"url": "https://example.com", "connection_id": row["id"]}
    assert "真人" in await entries["browser_open"].handler(args)
    launch, driver = created[0]
    assert launch.device_mode == "mobile"
    assert launch.profile_dir == registry.profile_path(row["id"], backend_id="test_driver")
    assert launch.profile_dir != registry.profile_path(row["id"])
    assert "真人" in await entries["browser_snapshot"].handler({})
    assert "主人已确认" in await entries["browser_login"].handler({})
    reply = await entries["browser_click"].handler({"ref": 1})
    assert "没有同意" in reply and not driver.clicks and any("点击" in text for text in asked)
    assert all(tool.serial and tool.external_content and tool.untrusted_source for tool in entries.values())
    state = json.loads(await entries["browser_connections"].handler({}))
    assert state["backend"] == {"id": "test_driver", "api_version": 1, "device_modes": ["desktop", "mobile"]}


async def test_unsupported_mode_never_creates_registers_or_falls_back(registry):
    provider, created = backend(modes={"desktop"})
    async def unexpected(*args, **kwargs):
        pytest.fail("unsupported mode must be rejected before confirmation")
    entries = tools(registry, provider, unexpected)
    reply = await entries["browser_connect"].handler({"url": "https://example.com", "account_label": "个人", "device_mode": "mobile"})
    assert "不支持" in reply and not registry.list() and not created
    row = registry.approve("https://example.com", "个人", "mobile")
    reply = await entries["browser_open"].handler({"url": "https://example.com", "connection_id": row["id"]})
    assert "不支持" in reply and not created and not (registry.root / "profiles").exists()


async def test_switching_backend_uses_new_cookies_but_cannot_steal_the_connection_lease(registry):
    row = registry.approve("https://example.com", "个人", "desktop")
    first, a = backend("driver_a")
    second, b = backend("driver_b")
    entries_a, entries_b = tools(registry, first, yes), tools(registry, second, yes)
    args = {"url": "https://example.com", "connection_id": row["id"]}
    await entries_a["browser_open"].handler(args)
    cookie_marker = a[0][0].profile_dir / "Cookies"
    cookie_marker.write_text("synthetic-profile-marker")
    assert "无法核实" in await entries_b["browser_open"].handler(args)
    assert not b
    await entries_a["browser_close"].handler({})
    assert "真人" in await entries_b["browser_open"].handler(args)
    assert a[0][0].profile_dir != b[0][0].profile_dir
    assert not (b[0][0].profile_dir / "Cookies").exists() and cookie_marker.exists()


async def test_driver_failure_does_not_fall_back_or_silently_release_its_owner(registry):
    row = registry.approve("https://example.com", "个人", "desktop")
    class Failing(_FakeBrowser):
        cleanup_fails = True
        async def start(self):
            self.running = True
            raise RuntimeError("synthetic driver failed")
        async def close(self):
            if self.cleanup_fails:
                raise RuntimeError("synthetic cleanup failed")
            await super().close()
    fake = Failing()
    provider = BrowserBackend("failing", 1, {"desktop"}, lambda launch: fake)
    entries = tools(registry, provider, yes)
    args = {"url": "https://example.com", "connection_id": row["id"]}
    try:
        assert "无法核实" in await entries["browser_open"].handler(args)
        state = json.loads(await entries["browser_connections"].handler({}))
        assert state["state"] == "cleanup_pending"
        other, created = backend("another")
        assert "无法核实" in await tools(registry, other, yes)["browser_open"].handler(args)
        assert not created
    finally:
        fake.cleanup_fails = False
        await entries["browser_close"].handler({})


async def test_model_arguments_do_not_select_or_load_a_driver(registry):
    provider, created = backend("chosen_by_host")
    entries = tools(registry, provider, yes)
    reply = await entries["browser_open"].handler({"url": "https://example.com", "backend_id": "another", "module": "/tmp/untrusted.py"})
    assert "正文" in reply and len(created) == 1
    assert json.loads(await entries["browser_connections"].handler({}))["backend"]["id"] == "chosen_by_host"


def test_profile_namespace_rejects_traversal_and_non_browser_manifests(registry):
    row = registry.approve("https://example.com", "个人", "desktop")
    with pytest.raises(ValueError):
        registry.profile_path(row["id"], backend_id="../../other")
    with pytest.raises(ValueError):
        build_browser_tools(registry.workspace, yes, connections=registry, backend={"module": "untrusted"})
    provider, _ = backend()
    with pytest.raises(ValueError):
        build_browser_tools(registry.workspace, yes, connections=registry, backend=provider, browser_factory=_FakeBrowser)
