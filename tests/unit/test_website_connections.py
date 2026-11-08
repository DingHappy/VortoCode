"""Website grants, manual login ownership, privacy and mobile-web transport."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from src.agents.tools.browser import build_browser_tools
from src.agents.tools.website_browser import shutdown_browsers
from src.browser.connections import BrowserConnections, safe_url, site_origin
from src.browser.cdp import CdpBrowser
from src.browser.page_privacy import ELEMENT_JS, SNAPSHOT_JS
from src.gateway.process_lease import ProcessLease
from tests.unit.test_browser_control import _FakeBrowser


@pytest.fixture(autouse=True)
async def cleanup(monkeypatch):
    from src.agents import web_fetch
    monkeypatch.setattr(web_fetch, "refusal_reason", lambda _host: "")
    yield
    await shutdown_browsers()


@pytest.fixture
def registry(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    return BrowserConnections(str(root), state_root=tmp_path / "host")


async def yes(_message, kind=None):
    assert kind == "interact"
    return True


def tools(registry, ask=yes, fake=None):
    fake = fake or _FakeBrowser()
    entries = build_browser_tools(registry.workspace, ask, browser_factory=lambda: fake, connections=registry)
    return {item.name: item for item in entries}, fake


def test_registry_is_private_scoped_and_does_not_import_other_accounts(registry, tmp_path):
    row = registry.approve("https://example.com/a?code=hidden", "工作账号", "mobile")
    assert row["origin"] == "https://example.com"
    assert "code" not in registry.path.read_text()
    assert set(row) == {"id", "origin", "account_label", "device_mode"}
    other = BrowserConnections(str(tmp_path / "other"), state_root=registry.root)
    assert not other.list() and other.get(row["id"]) is None
    assert not other.revoke(row["id"]) and registry.get(row["id"]) == row
    path = registry.profile_path(row["id"])
    assert Path(registry.workspace) not in path.parents
    if os.name != "nt":
        assert registry.path.stat().st_mode & 0o077 == 0
        assert path.stat().st_mode & 0o077 == 0
    assert registry.revoke(row["id"]) and path.exists()  # revoke access, preserve local cookies


@pytest.mark.parametrize("bad", ["http://example.com", "https://user:pass@example.com", "file:///tmp/test", "https://"])
def test_bad_website_identity_is_rejected_before_grant(registry, bad):
    with pytest.raises(ValueError):
        registry.approve(bad, "个人", "desktop")
    assert not registry.path.exists()


@pytest.mark.parametrize("damage", ["json", "duplicate", "symlink", "permissions", "unknown-field"])
def test_invalid_registry_never_guesses_a_cookie_profile(registry, tmp_path, damage):
    row = registry.approve("https://example.com", "个人", "desktop")
    if damage == "json":
        registry.path.write_text("broken")
    elif damage == "duplicate":
        registry.path.write_text('{"version":1,"version":1,"connections":[]}')
    elif damage == "permissions":
        if os.name == "nt":
            pytest.skip("POSIX permissions")
        registry.path.chmod(0o644)
    elif damage == "unknown-field":
        raw = json.loads(registry.path.read_text())
        raw["connections"][0]["cookie"] = "synthetic-not-a-real-cookie"
        registry.path.write_text(json.dumps(raw))
    else:
        target = tmp_path / "copy.json"
        target.write_bytes(registry.path.read_bytes())
        registry.path.unlink(); registry.path.symlink_to(target)
    with pytest.raises(ValueError):
        registry.get(row["id"])


async def test_connect_requires_human_approval_without_collecting_passwords(registry):
    async def no(_message, kind=None):
        return False
    entries, fake = tools(registry, no)
    reply = await entries["browser_connect"].handler({"url": "https://example.com", "account_label": "个人"})
    assert "未同意" in reply and not registry.list() and not fake.running
    entries, _ = tools(registry)
    reply = await entries["browser_connect"].handler({"url": "https://example.com", "account_label": "个人", "device_mode": "mobile"})
    assert "已登记" in reply and registry.list()[0]["device_mode"] == "mobile"


async def test_managed_open_yields_to_human_then_explicitly_resumes(registry):
    row = registry.approve("https://example.com", "个人", "desktop")
    entries, fake = tools(registry)
    assert "真人" in await entries["browser_open"].handler({"url": "https://example.com/", "connection_id": row["id"]})
    assert fake.url == "https://example.com/"
    assert "真人" in await entries["browser_snapshot"].handler({})
    assert "真人" in await entries["browser_screenshot"].handler({})
    assert "真人" in await entries["browser_type"].handler({"ref": 1, "text": "should not type"})
    assert not fake.typed and not fake.clicks
    assert "主人已确认" in await entries["browser_login"].handler({})
    assert "正文" in await entries["browser_snapshot"].handler({})


async def test_sso_stays_with_human_and_agent_never_reads_or_navigates_login_page(registry):
    row = registry.approve("https://example.com", "工作", "desktop")
    entries, fake = tools(registry)
    await entries["browser_open"].handler({"url": "https://example.com", "connection_id": row["id"]})
    fake.url = "https://accounts.google.com/signin?code=synthetic-code"
    assert "真人" in await entries["browser_snapshot"].handler({})
    assert "真人" in await entries["browser_login"].handler({})
    assert "accounts.google.com" in fake.url  # no agent action on SSO, no destructive about:blank
    fake.url = "https://example.com/finished?code=synthetic-code"
    reply = await entries["browser_login"].handler({})
    assert "主人已确认" in reply and "synthetic-code" not in reply


async def test_revocation_during_confirmation_prevents_the_click(registry):
    row = registry.approve("https://example.com", "个人", "desktop")
    async def ask(message, kind=None):
        if "点击" in message:
            registry.revoke(row["id"])
        return True
    entries, fake = tools(registry, ask)
    await entries["browser_open"].handler({"url": "https://example.com", "connection_id": row["id"]})
    await entries["browser_login"].handler({})
    reply = await entries["browser_click"].handler({"ref": 1})
    assert "撤销" in reply and not fake.clicks


async def test_profile_has_one_owner_and_close_releases_it_after_browser_exit(registry):
    row = registry.approve("https://example.com", "个人", "desktop")
    first, fake = tools(registry)
    await first["browser_open"].handler({"url": "https://example.com", "connection_id": row["id"]})
    with pytest.raises(RuntimeError):
        ProcessLease(registry.profile_path(row["id"]) / ".vortocode-owner.lock").acquire()
    second, other = tools(registry)
    reply = await second["browser_open"].handler({"url": "https://example.com", "connection_id": row["id"]})
    assert reply["ok"] is False and "无法核实" in reply["text"] and not other.running
    await first["browser_close"].handler({})
    assert not fake.running
    await second["browser_open"].handler({"url": "https://example.com", "connection_id": row["id"]})
    assert other.running


async def test_cleanup_failure_keeps_profile_lease_until_explicit_retry(registry):
    row = registry.approve("https://example.com", "个人", "desktop")
    class Closing(_FakeBrowser):
        fail = True
        async def close(self):
            if self.fail:
                raise RuntimeError("synthetic cleanup failure")
            await super().close()
    fake = Closing(); entries, _ = tools(registry, fake=fake)
    await entries["browser_open"].handler({"url": "https://example.com", "connection_id": row["id"]})
    try:
        reply = await entries["browser_close"].handler({})
        assert reply["ok"] is False and "无法核实" in reply["text"]
        with pytest.raises(RuntimeError):
            ProcessLease(registry.profile_path(row["id"]) / ".vortocode-owner.lock").acquire()
    finally:
        fake.fail = False
        await entries["browser_close"].handler({})
    lease = ProcessLease(registry.profile_path(row["id"]) / ".vortocode-owner.lock").acquire(); lease.release()


async def test_mobile_web_dispatches_metrics_touch_user_agent_tap_and_scroll():
    browser = CdpBrowser("unused", device_mode="mobile")
    calls = []
    async def send(method, params=None, **kwargs):
        calls.append((method, params))
        return {"product": "Chrome/150.1.2.3"} if method == "Browser.getVersion" else {}
    browser.send = send
    await browser._mobile_mode(); await browser.click_at(20, 40); await browser.scroll(300)
    assert calls[1] == ("Emulation.setDeviceMetricsOverride", {"width":390,"height":844,"deviceScaleFactor":3,"mobile":True})
    assert "Mobile" in calls[3][1]["userAgent"]
    assert calls[4][0] == "Input.dispatchTouchEvent" and calls[5][1]["type"] == "touchEnd"
    assert calls[-1][1]["gestureSourceType"] == "touch" and calls[-1][1]["yDistance"] == -300


async def test_failed_browser_spawn_closes_all_pipe_fds_and_strips_host_secrets(tmp_path, monkeypatch):
    descriptors = []
    real_pipe = os.pipe
    def pipe():
        pair = real_pipe()
        descriptors.extend(pair)
        return pair
    async def spawn(*args, **kwargs):
        assert "VORTOONE_PROVIDER_TEST_KEY" not in kwargs["env"]
        raise OSError("synthetic launch failure")
    monkeypatch.setenv("VORTOONE_PROVIDER_TEST_KEY", "synthetic-not-a-real-key")
    monkeypatch.setattr(os, "pipe", pipe)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    browser = CdpBrowser("unused", tmp_path / "profile")
    with pytest.raises(OSError):
        await browser.start()
    assert len(descriptors) == 4 and not browser.running and not browser._fds
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


async def test_kill_waits_for_browser_exit_before_releasing_transport():
    killed, exited = asyncio.Event(), asyncio.Event()
    class Process:
        returncode = None
        attempts = 0
        def terminate(self):
            pass
        def kill(self):
            killed.set()
        async def wait(self):
            self.attempts += 1
            if self.attempts == 1:
                raise asyncio.TimeoutError()
            await exited.wait()
            self.returncode = -9
    class Transport:
        closed = False
        def close(self):
            self.closed = True
    browser = CdpBrowser("unused")
    process, transport = Process(), Transport()
    browser._proc, browser._transport = process, transport
    async def failed_send(*args, **kwargs):
        raise RuntimeError("synthetic unresponsive Chrome")
    browser.send = failed_send
    closing = asyncio.create_task(browser.close())
    await killed.wait()
    try:
        assert not closing.done() and not transport.closed and browser._proc is process
    finally:
        exited.set()
        await closing
    assert transport.closed and not browser.running and process.attempts == 2


@pytest.mark.parametrize("kind", ["password", "otp", "current-password", "plain"])
def test_actual_dom_projection_never_reads_input_values_or_login_body(kind):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required to execute production page JavaScript")
    attrs = {"type": "text"}
    if kind == "password": attrs["type"] = "password"
    if kind == "otp": attrs["name"] = "verification_code"
    if kind == "current-password": attrs["autocomplete"] = "current-password"
    secret = kind != "plain"
    source = r'''
const attrs = ATTRS;
const el = {tagName:'INPUT',getAttribute:name=>attrs[name]||'', setAttribute:()=>{},
  getBoundingClientRect:()=>({x:0,y:0,left:0,top:0,width:10,height:10}), scrollIntoView:()=>{},
  innerText:'',get value(){throw Error('input values must never be read')},isContentEditable:false};
globalThis.getComputedStyle=()=>({visibility:'visible',display:'block'});
globalThis.location={href:'https://example.com/callback?code=synthetic-secret#access_token=synthetic-secret'};
globalThis.document={querySelectorAll:s=>s.includes('iframe')?[]:[el],querySelector:()=>el,
  get title(){if(SECRET)throw Error('login title must not be read');return 'Public'},
  body:{get innerText(){if(SECRET)throw Error('login body must not be read');return 'Public body'}}};
console.log(JSON.stringify([eval(SNAPSHOT),eval(ELEMENT)]));
'''.replace("ATTRS", json.dumps(attrs)).replace("SECRET", "true" if secret else "false")
    source = source.replace("SNAPSHOT", json.dumps(SNAPSHOT_JS)).replace("ELEMENT", json.dumps(ELEMENT_JS % 1))
    result = subprocess.run([node, "-e", source], text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert "synthetic-secret" not in result.stdout
    snapshot, element = json.loads(result.stdout)
    if secret:
        assert snapshot == {"needs_login": True} and element == {"sensitive": True}
    else:
        assert snapshot["url"] == "https://example.com/" and element["editable"]


def test_display_urls_remove_callback_paths_queries_fragments_and_userinfo():
    assert safe_url("https://u:p@example.com/reset/private-token?code=secret#token") == "https://example.com/"
    assert site_origin("https://EXAMPLE.com:443/a") == "https://example.com"


async def test_shutdown_during_start_keeps_lease_and_never_navigates_after_shutdown(registry):
    row = registry.approve("https://example.com", "个人", "desktop")
    started, release = asyncio.Event(), asyncio.Event()
    class SlowStart(_FakeBrowser):
        async def start(self):
            self.running = True
            started.set()
            await release.wait()
    fake = SlowStart(); entries, _ = tools(registry, fake=fake)
    opening = asyncio.create_task(entries["browser_open"].handler({"url": "https://example.com", "connection_id": row["id"]}))
    await started.wait()
    closing = asyncio.create_task(shutdown_browsers())
    try:
        for _ in range(3):
            await asyncio.sleep(0)
        assert not closing.done()
        with pytest.raises(RuntimeError):
            ProcessLease(registry.profile_path(row["id"]) / ".vortocode-owner.lock").acquire()
    finally:
        release.set()
        await opening; await closing
    assert fake.url == "about:blank" and not fake.running


async def test_known_auth_form_stops_reads_and_screenshots_without_emitting_values(registry):
    class Protected(_FakeBrowser):
        protected = False
        async def evaluate(self, expression):
            from src.browser.page_privacy import AUTH_STATE_JS
            if expression == AUTH_STATE_JS:
                return {"protected": self.protected, "frames": False}
            return await super().evaluate(expression)
        async def screenshot(self):
            pytest.fail("login page screenshot must not be captured")
    fake = Protected(); entries, _ = tools(registry, fake=fake)
    await entries["browser_open"].handler({"url": "https://example.com"})
    fake.protected = True
    assert "真人" in await entries["browser_snapshot"].handler({})
    assert "真人" in await entries["browser_screenshot"].handler({})
    assert "真人" in await entries["browser_login"].handler({})


def test_full_registry_rejects_new_grants_without_evicting_existing(registry, monkeypatch):
    from src.browser import connections
    monkeypatch.setattr(connections, "MAX_CONNECTIONS", 1)
    row = registry.approve("https://example.com", "个人", "desktop")
    with pytest.raises(ValueError, match="已满"):
        registry.approve("https://another.example", "工作", "desktop")
    assert registry.list() == [row]
