"""Connection status is event-driven; stale/global state never grants control."""
import json
from pathlib import Path
import runpy
import shutil
import stat
import subprocess
import time

import pytest

desktop = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/resident-desktop.py"))


def control(tmp_path):
    tmp_path.chmod(0o700)
    value = desktop["Control"](tmp_path, tmp_path)
    value.start()
    value.heartbeat({}, ready=True)
    desktop["prepare_observer_assets"](tmp_path)
    value.observer_start("fixture")
    return value


def test_snapshot_exposes_only_counts_mode_and_freshness_without_altering_authority(tmp_path):
    value = control(tmp_path)
    with value.locked():
        before = value.read()
        before["lease_hash"] = "private-lease"
        before["frame"] = {"id": "private-frame", "time": time.time()}
        value.save(before)
    value.observer_heartbeat("fixture", ready=True)
    target = tmp_path / "observer-web/status.json"
    snapshot = json.loads(target.read_text())
    assert set(snapshot) == {"schema_version", "updated_at", "desktop_ready", "observer_ready", "mode", "controlling", "observing"}
    assert snapshot["desktop_ready"] and snapshot["observer_ready"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert "private-" not in target.read_text() and before["epoch"] not in target.read_text()
    after = value.read()
    assert (after["epoch"], after["lease_hash"], after["frame"]) == (before["epoch"], before["lease_hash"], before["frame"])
    # A crash-left temporary file is preserved, but does not poison publication.
    pending = tmp_path / "observer-web/status.json.tmp"
    pending.write_text("do not delete")
    value.observer_heartbeat("fixture", ready=False)
    assert json.loads(target.read_text())["observer_ready"] is False
    assert pending.read_text() == "do not delete"


def test_optional_feedback_failure_does_not_stop_observer_or_overwrite_symlink(tmp_path):
    value = control(tmp_path)
    other = tmp_path / "other"
    other.write_text("keep")
    (tmp_path / "observer-web/status.json").symlink_to(other)
    value.observer_heartbeat("fixture", ready=True)
    assert other.read_text() == "keep"
    assert value.observer_available(value.read())


def test_js_rejects_stale_malformed_states_and_distinguishes_other_control_windows(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node required for browser feedback contract")
    script = tmp_path / "viewer-state.mjs"
    script.write_text(desktop["VIEWER_STATE_JS"])
    driver = """
import { readRuntime, runtimeNotice } from MODULE;
const now = 100000;
const raw = { schema_version: 1, updated_at: 100, desktop_ready: true, observer_ready: true,
  mode: 'human', controlling: 2, observing: 3, lease: 'never-project' };
const live = readRuntime(raw, now);
console.log(JSON.stringify({
  live, observer: runtimeNotice(live, 'observe', true), controller: runtimeNotice(live, 'control', true),
  stale: readRuntime(raw, now + 6001), future: readRuntime(raw, now - 3001),
  malformed: readRuntime({ ...raw, controlling: true }, now),
  unknown: runtimeNotice(null, 'control', true),
  released: runtimeNotice({ ...live, controlling: 0 }, 'observe', true),
  agent: runtimeNotice({ ...live, controlling: 0, mode: 'agent' }, 'observe', true),
  syncing: runtimeNotice({ ...live, controlling: 0 }, 'control', true)
}));
""".replace("MODULE", json.dumps(script.as_uri()))
    result = subprocess.run([node, "--input-type=module", "-e", driver], capture_output=True, text=True, check=True, timeout=10)
    values = json.loads(result.stdout)
    assert "lease" not in values["live"]
    assert "远端仍记录 2 个接管连接" in values["observer"]
    assert "远端记录 2 个接管连接（包含本窗口）" in values["controller"]
    assert values["stale"] is None and values["future"] is None and values["malformed"] is None
    assert "暂未确认" in values["unknown"]
    assert "重新授权" in values["released"]
    assert "不会打断" in values["agent"]
    assert "正在同步" in values["syncing"]


def test_browser_controller_lifecycle_viewport_and_shortcuts_preserve_authority(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node required for browser lifecycle contract")
    (tmp_path / "package.json").write_text('{"type":"module"}')
    (tmp_path / "viewer-state.js").write_text(desktop["VIEWER_STATE_JS"])
    (tmp_path / "viewer.js").write_text(desktop["VIEWER_JS"])
    core = tmp_path / "novnc/core"
    core.mkdir(parents=True)
    (core / "rfb.js").write_text("""
export default class RFB extends EventTarget {
  constructor(target, url) { super(); this.url = url; this.sent = []; target.items.push('canvas'); globalThis.clients.push(this); }
  disconnect() { this.closed = true; }
  emit(type, detail = {}) { this.dispatchEvent(new CustomEvent(type, { detail })); }
  sendKey(key, code, down) { this.sent.push([key, code, down]); if (this.failLetter && code === 'KeyL') throw Error(); }
  focus() { this.focused = true; }
}
""")
    driver = """
import assert from 'node:assert/strict';
globalThis.clients = [];
const elements = new Map();
for (const id of ['screen','state','runtime','reconnect','observe','control','disconnect','scale','fullscreen','tools-message','key-address','key-tab','key-escape','takeover-dialog','takeover-cancel','takeover-accept']) {
  elements.set(id, { items: [], handlers: {}, textContent: '', disabled: false, value: 'fit',
    replaceChildren(...items) { this.items = items; }, setAttribute() {},
    addEventListener(type, fn) { this.handlers[type] = fn; },
    showModal() { this.open = true; }, close() { this.open = false; } });
}
const element = id => elements.get(id);
const documentEvents = {};
globalThis.document = { hidden: false, getElementById: element, fullscreenEnabled: true,
  addEventListener: (name, fn) => { documentEvents[name] = fn; } };
document.documentElement = { requestFullscreen: async () => {
  document.fullscreenElement = document.documentElement; documentEvents.fullscreenchange(); } };
document.exitFullscreen = async () => { document.fullscreenElement = null; documentEvents.fullscreenchange(); };
globalThis.window = { location: { href: 'http://127.0.0.1:16088/', protocol: 'http:' },
  confirm: () => { throw Error('native confirm must not be used'); }, addEventListener: () => {} };
globalThis.fetch = async () => ({ ok: true, json: async () => null });
const timers = new Map(); let serial = 0;
globalThis.setTimeout = (fn, ms) => { const id = ++serial; timers.set(id, { fn, ms }); return id; };
globalThis.clearTimeout = id => timers.delete(id);
globalThis.setInterval = () => 1;
globalThis.clearInterval = () => {};
await import(VIEWER);
for (let i = 0; i < 50 && !clients.length; i++) await new Promise(setImmediate);
assert.equal(clients.length, 1);
assert.match(element('state').textContent, /正在连接/);
assert.equal(clients[0].viewOnly, true);
clients[0].emit('connect');
assert.match(element('state').textContent, /仅观察已连接/);
assert.equal(element('key-tab').disabled, true);
element('key-tab').handlers.click(); assert.deepEqual(clients[0].sent, []);
element('scale').value = 'actual'; element('scale').handlers.change();
assert.equal(clients[0].scaleViewport, false); assert.equal(clients[0].resizeSession, false);
assert.equal(clients.length, 1); assert.equal(clients[0].viewOnly, true);
await element('fullscreen').handlers.click(); assert.equal(element('fullscreen').textContent, '退出全屏');
await element('fullscreen').handlers.click(); assert.equal(element('fullscreen').textContent, '全屏');
document.documentElement.requestFullscreen = async () => { throw Error(); };
await element('fullscreen').handlers.click();
assert.match(element('tools-message').textContent, /未允许/); assert.equal(clients.length, 1);
element('control').handlers.click();
assert.equal(element('takeover-dialog').open, true);
element('takeover-cancel').handlers.click(); // visible cancellation preserves current connection
assert.equal(clients.length, 1); assert.equal(clients[0].closed, undefined);
element('takeover-accept').handlers.click(); // an old/unsolicited confirmation grants nothing
assert.equal(clients.length, 1);
element('control').handlers.click(); element('takeover-accept').handlers.click();
assert.equal(clients.length, 2); assert.equal(clients[0].closed, true);
clients[0].emit('disconnect', { clean: false }); // stale client cannot alter new mode
assert.match(element('state').textContent, /正在连接接管/);
clients[1].emit('connect');
assert.match(element('state').textContent, /接管已连接/);
assert.equal(element('key-tab').disabled, false);
assert.equal(clients[1].scaleViewport, false); // viewport choice survives a mode switch
element('key-tab').handlers.click(); assert.equal(clients[1].sent[0][1], 'Tab');
element('key-escape').handlers.click(); assert.equal(clients[1].sent[1][1], 'Escape');
element('key-address').handlers.click();
assert.deepEqual(clients[1].sent.slice(-3).map(v => [v[1], v[2]]), [['ControlLeft',true],['KeyL',undefined],['ControlLeft',false]]);
clients[1].failLetter = true; element('key-address').handlers.click();
assert.deepEqual(clients[1].sent[clients[1].sent.length-1], [0xffe3,'ControlLeft',false]);
assert.match(element('tools-message').textContent, /未能发送/);
clients[1].emit('disconnect', { clean: false });
assert.match(element('state').textContent, /连接中断/);
assert.equal(element('reconnect').disabled, false); assert.equal(clients.length, 2);
const sentBefore = clients[1].sent.length;
element('key-tab').handlers.click(); assert.equal(clients[1].sent.length, sentBefore);
assert.equal(element('key-tab').disabled, true);
element('reconnect').handlers.click(); element('takeover-cancel').handlers.click();
assert.equal(clients.length, 2); // reconnecting writable mode asks again
element('reconnect').handlers.click(); element('takeover-accept').handlers.click();
assert.equal(clients.length, 3);
clients[1].emit('connect'); assert.match(element('state').textContent, /正在连接/);
for (const item of [...timers.values()]) if (item.ms === 12000) item.fn();
assert.match(element('state').textContent, /连接超时/);
assert.equal(clients[2].closed, true); assert.equal(clients.length, 3);
element('observe').handlers.click(); clients[3].emit('connect');
element('disconnect').handlers.click();
assert.match(element('state').textContent, /主动断开/);
assert.deepEqual(element('screen').items, []); assert.equal(clients.length, 4);
// A cached old HTML page may load the new script during an asset refresh.
for (const id of ['scale','fullscreen','tools-message','key-address','key-tab','key-escape','takeover-dialog','takeover-cancel','takeover-accept']) elements.delete(id);
await import(VIEWER + '?legacy=1');
for (let i = 0; i < 50 && clients.length < 5; i++) await new Promise(setImmediate);
assert.equal(clients.length, 5); clients[4].emit('connect');
assert.match(element('state').textContent, /仅观察已连接/);
assert.equal(clients[4].scaleViewport, true); assert.equal(clients[4].viewOnly, true);
element('control').handlers.click(); assert.equal(clients.length, 5);
assert.match(element('state').textContent, /确认组件不可用/);
console.log('connection lifecycle passed');
""".replace("VIEWER", json.dumps((tmp_path / "viewer.js").as_uri()))
    result = subprocess.run([node, "--input-type=module", "-e", driver], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
