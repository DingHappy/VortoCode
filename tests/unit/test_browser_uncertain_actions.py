"""Failure/cancel/restart fences through the real tools; no browser or model IO."""
import asyncio
import json
import os

import pytest

from src.agents.confirmation_context import start_confirmation_turn
from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime
from src.agents.tools.browser import build_browser_tools
from src.agents.tools.website_browser import shutdown_browsers
from src.browser.action_state import BrowserActionState
from src.browser.connections import BrowserConnections
from src.browser.cdp import BrowserError, CdpBrowser
from src.browser.page_privacy import SNAPSHOT_JS
from tests.unit.test_browser_control import _FakeBrowser


class Browser(_FakeBrowser):
    fault = ""

    def __init__(self):
        super().__init__()
        self.navigations = []
        self.scrolled = []
        self.enters = 0

    async def navigate(self, url):
        self.navigations.append(url)
        await super().navigate(url)
        if self.fault == "open":
            raise TimeoutError("synthetic navigation receipt lost")

    async def wait_ready(self):
        if self.fault == "timeout":
            raise TimeoutError("synthetic private URL/Cookie MUST NOT ESCAPE")
        if self.fault == "cancel":
            raise asyncio.CancelledError()

    async def evaluate(self, expression):
        if expression == SNAPSHOT_JS:
            if self.fault == "readback":
                raise RuntimeError("synthetic readback failed")
            if self.fault == "auth":
                return {"needs_login": True}
        return await super().evaluate(expression)

    async def scroll(self, amount):
        self.scrolled.append(amount)
        if self.fault == "scroll":
            raise TimeoutError("synthetic scroll response lost")

    async def press_enter(self):
        self.enters += 1


@pytest.fixture
async def case(tmp_path, monkeypatch):
    from src.agents import web_fetch
    monkeypatch.setattr(web_fetch, "refusal_reason", lambda _host: "")
    workspace = tmp_path / "project"
    workspace.mkdir()
    registry = BrowserConnections(str(workspace), state_root=tmp_path / "host")
    row = registry.approve("https://example.com", "synthetic-account", "desktop")
    approvals = []
    allowed = [True]

    async def confirm(message, kind=None):
        approvals.append(message)
        return allowed[0]

    def build(browser):
        return {tool.name: tool for tool in build_browser_tools(
            str(workspace), confirm, browser_factory=lambda: browser, connections=registry)}

    browser = Browser()
    entries = build(browser)
    args = {"url": "https://example.com", "connection_id": row["id"]}
    await entries["browser_open"].handler(args)
    await entries["browser_login"].handler({})
    yield registry, row, browser, entries, build, args, approvals, allowed
    await shutdown_browsers()


def failed(result):
    assert result["ok"] is False
    assert "synthetic private" not in result["text"]
    return result["text"]


@pytest.mark.parametrize("fault", ["timeout", "readback", "auth"])
async def test_unknown_click_blocks_all_mutations_but_allows_readback(case, fault):
    registry, row, browser, entries, build, args, approvals, allowed = case
    browser.fault = fault
    failed(await entries["browser_click"].handler({"ref": 1}))
    browser.fault = ""
    count = len(approvals)
    for name, values in (("browser_click", {"ref": 1}), ("browser_type", {"ref": 1, "text": "hello"}),
                         ("browser_open", args), ("browser_scroll", {})):
        assert "结果不明" in failed(await entries[name].handler(values))
    assert len(browser.clicks) == 1 and not browser.scrolled and not browser.typed
    assert len(approvals) == count  # No new approval loop for blocked mutations.
    assert json.loads(await entries["browser_connections"].handler({}))["state"] == "needs_reconciliation"
    assert entries["browser_click"].resource.observe()["state"] == "needs_reconciliation"
    if fault != "auth":
        assert "正文" in await entries["browser_snapshot"].handler({})
    # Reading current state never clears the marker or declares business success.
    assert BrowserActionState(registry.profile_path(row["id"])).read()


async def test_pending_survives_new_tool_session_and_blocks_reopening_target(case):
    registry, row, browser, entries, build, args, approvals, allowed = case
    browser.fault = "timeout"
    failed(await entries["browser_click"].handler({"ref": 1}))
    await entries["browser_close"].handler({})
    other = Browser()
    new = build(other)
    assert "结果不明" in failed(await new["browser_open"].handler(args))
    assert other.running and other.navigations == []  # Reopen blank, never replay URL/GET.
    assert "结果不明" in failed(await new["browser_click"].handler({"ref": 1}))
    other.url = "https://example.com/"  # Human checks original task in the visible window.
    start_confirmation_turn()
    assert "主人已确认" in await new["browser_login"].handler({})
    assert "结果不明" in approvals[-1] and "不代表业务成功" in approvals[-1]
    assert not BrowserActionState(registry.profile_path(row["id"])).read()
    assert len(browser.clicks) == 1 and other.clicks == []


async def test_cancel_after_dispatch_keeps_pending_marker(case):
    registry, row, browser, entries, build, args, approvals, allowed = case
    browser.fault = "cancel"
    with pytest.raises(asyncio.CancelledError):
        await entries["browser_click"].handler({"ref": 1})
    browser.fault = ""
    assert "结果不明" in failed(await entries["browser_click"].handler({"ref": 1}))
    assert len(browser.clicks) == 1
    assert BrowserActionState(registry.profile_path(row["id"])).read()


@pytest.mark.parametrize("kind", ["open", "type", "scroll"])
async def test_navigation_input_submit_and_scroll_also_leave_uncertain_fence(case, kind):
    registry, row, browser, entries, build, args, approvals, allowed = case
    browser.element.update(editable=True, tag="input", type="text")
    browser.fault = "timeout" if kind == "type" else kind
    values = args if kind == "open" else {"ref": 1, "text": "ordinary text", "submit": "true"}
    failed(await entries[{"open": "browser_open", "type": "browser_type", "scroll": "browser_scroll"}[kind]].handler(values))
    marker = BrowserActionState(registry.profile_path(row["id"])).read()
    assert marker["action"] == kind
    assert set(marker) == {"version", "action", "dispatch"}
    assert "ordinary text" not in json.dumps(marker)
    if kind == "type":
        assert browser.typed == ["ordinary text"] and browser.enters == 1


async def test_decline_or_failed_recovery_does_not_release_pending(case):
    registry, row, browser, entries, build, args, approvals, allowed = case
    browser.fault = "timeout"
    failed(await entries["browser_click"].handler({"ref": 1}))
    state = BrowserActionState(registry.profile_path(row["id"]))
    original = state.read()
    allowed[0] = False
    start_confirmation_turn()
    assert "真人" in await entries["browser_login"].handler({})
    assert state.read() == original
    allowed[0] = True
    browser.url = "https://other.example/"
    start_confirmation_turn()
    assert "真人" in await entries["browser_login"].handler({})
    assert state.read() == original
    browser.url, browser.fault = "https://example.com/", ""
    start_confirmation_turn()
    assert "主人已确认" in await entries["browser_login"].handler({})
    assert state.read() is None and len(browser.clicks) == 1


async def test_pending_does_not_bypass_same_turn_handoff_budget(case):
    registry, row, browser, entries, build, args, approvals, allowed = case
    start_confirmation_turn()
    await entries["browser_login"].handler({})
    browser.fault = "timeout"
    failed(await entries["browser_click"].handler({"ref": 1}))
    count = len(approvals)
    assert "本轮已请求" in await entries["browser_login"].handler({})
    assert len(approvals) == count
    assert BrowserActionState(registry.profile_path(row["id"])).read()


async def test_declined_action_does_not_create_false_pending(case):
    registry, row, browser, entries, build, args, approvals, allowed = case
    allowed[0] = False
    assert "未同意" in await entries["browser_open"].handler(args)
    assert "没有同意" in await entries["browser_click"].handler({"ref": 1})
    assert not browser.clicks and BrowserActionState(registry.profile_path(row["id"])).read() is None


async def test_original_runtime_reports_failed_and_never_calls_success_observer(case):
    registry, row, browser, entries, build, args, approvals, allowed = case
    events, succeeded = [], []
    runtime = ToolRuntime(lambda: entries, ToolExecutionContext(
        on_event=lambda stage, payload: events.append((stage, payload)),
        on_success=lambda *values: succeeded.append(values)))
    browser.fault = "timeout"
    first = await runtime.execute("browser_click", {"ref": 1}, "build")
    second = await runtime.execute("browser_click", {"ref": 1}, "build")
    assert first.status == second.status == "failed"
    assert not succeeded and len(browser.clicks) == 1
    assert [payload["status"] for stage, payload in events if stage == "finish"] == ["failed", "failed"]


async def test_revocation_during_recovery_readback_cannot_clear_marker(case):
    registry, row, browser, entries, build, args, approvals, allowed = case
    browser.fault = "timeout"
    failed(await entries["browser_click"].handler({"ref": 1}))
    browser.fault = ""
    state = BrowserActionState(registry.profile_path(row["id"]))
    marker = state.read()
    original = browser.evaluate
    async def revoked(expression):
        page = await original(expression)
        if expression == SNAPSHOT_JS:
            registry.revoke(row["id"])
        return page
    browser.evaluate = revoked
    start_confirmation_turn()
    assert "撤销" in await entries["browser_login"].handler({})
    assert state.read() == marker and len(browser.clicks) == 1


async def test_shutdown_during_inflight_action_preserves_marker(case):
    registry, row, browser, entries, build, args, approvals, allowed = case
    started, release = asyncio.Event(), asyncio.Event()
    async def wait():
        started.set()
        await release.wait()
    browser.wait_ready = wait
    action = asyncio.create_task(entries["browser_click"].handler({"ref": 1}))
    await asyncio.wait_for(started.wait(), 1)
    state = BrowserActionState(registry.profile_path(row["id"]))
    marker = state.read()
    try:
        await shutdown_browsers()
    finally:
        release.set()
    failed(await action)
    assert state.read() == marker and len(browser.clicks) == 1


async def test_pending_write_failure_never_dispatches_the_action(case, monkeypatch):
    registry, row, browser, entries, build, args, approvals, allowed = case
    def cannot_sync(self):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(BrowserActionState, "_sync", cannot_sync)
    failed(await entries["browser_click"].handler({"ref": 1}))
    assert not browser.clicks
    assert BrowserActionState(registry.profile_path(row["id"])).read()


@pytest.mark.parametrize("state", ["loading", "script_error"])
async def test_real_cdp_readiness_deadline_raises_instead_of_reporting_success(tmp_path, state):
    driver = CdpBrowser("/synthetic/browser-not-launched", tmp_path / "unused")
    async def evaluate(_expression):
        if state == "script_error":
            raise BrowserError("synthetic navigation in progress")
        return state
    driver.evaluate = evaluate
    with pytest.raises(BrowserError, match="超时"):
        await driver.wait_ready(timeout=0.001)
    assert not driver.running


@pytest.mark.parametrize("damage", ["json", "unknown", "symlink", "permissions"])
async def test_damaged_pending_never_allows_navigation_or_auto_clears(case, damage):
    registry, row, browser, entries, build, args, approvals, allowed = case
    state = BrowserActionState(registry.profile_path(row["id"]))
    state.begin("click")
    if damage == "json":
        state.path.write_text("broken")
    elif damage == "unknown":
        state.path.write_text('{"version":2}')
    elif damage == "permissions":
        if os.name == "nt":
            pytest.skip("POSIX permissions")
        state.path.chmod(0o644)
    else:
        target = state.path.with_name("synthetic-target.json")
        target.write_bytes(state.path.read_bytes())
        state.path.unlink()
        state.path.symlink_to(target)
    before = list(browser.navigations)
    failed(await entries["browser_open"].handler(args))
    failed(await entries["browser_click"].handler({"ref": 1}))
    assert browser.navigations == before and not browser.clicks
    assert state.path.exists()


async def test_anonymous_close_does_not_reset_current_session_fence(case):
    registry, row, browser, entries, build, args, approvals, allowed = case
    await entries["browser_close"].handler({})
    await entries["browser_open"].handler({"url": "https://example.com"})
    browser.fault = "timeout"
    failed(await entries["browser_click"].handler({"ref": 1}))
    browser.fault = ""
    await entries["browser_close"].handler({})
    before = list(browser.navigations)
    failed(await entries["browser_open"].handler({"url": "https://other.example/"}))
    assert browser.navigations == before
    browser.url = "https://other.example/"
    start_confirmation_turn()
    assert "真人" in await entries["browser_login"].handler({})
    browser.url = "https://example.com/"
    start_confirmation_turn()
    assert "主人已确认" in await entries["browser_login"].handler({})
    assert len(browser.clicks) == 1
