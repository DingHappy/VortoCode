#!/usr/bin/env python3
"""Dedicated resident X11 desktop. No agent permissions, scheduling or model calls.

Use `plan` before `install`; installation only writes its own files. The user
explicitly starts/enables the generated systemd unit. Access stays on loopback
and is reached through SSH. Browser sandboxing is never disabled.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

UNIT = "vortocode-computer.service"
OBSERVER_UNIT = "vortocode-observer.service"
TOOLS = ("Xvfb", "xauth", "xdpyinfo", "openbox", "x11vnc", "websockify", "xdotool", "scrot",
         "busctl", "dbus-update-activation-environment", "gnome-keyring-daemon")

VIEWER_HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VortoCode 远程桌面</title><link rel="stylesheet" href="viewer.css">
<script type="module" src="viewer.js"></script>
<body><header><strong>远程桌面</strong><nav aria-label="桌面模式">
<button id="observe" aria-pressed="true">仅观察</button>
<button id="control" aria-pressed="false">接管操作</button>
<button id="reconnect" disabled>重连画面</button>
<button id="disconnect">断开画面</button></nav>
<button id="viewer-more" hidden aria-expanded="false">画面工具</button>
<div class="viewer-tools" role="group" aria-label="画面工具">
<label>画面尺寸 <select id="scale" aria-label="画面尺寸"><option value="fit">适应窗口</option><option value="actual">原始尺寸</option></select></label>
<button id="fullscreen">全屏</button><span>快捷按键</span>
<button id="key-address" disabled>地址栏</button><button id="key-tab" disabled>Tab</button><button id="key-escape" disabled>Esc</button>
</div><p id="tools-message" role="status">快捷按键仅在接管并连接成功后可用。</p>
<p id="state" role="status">正在加载画面组件…</p>
<p id="runtime" role="status">正在核对远端控制权…</p>
<p>仅观察不会打断助手。接管后可使用键鼠；切回观察或断开后，助手仍需重新授权才会继续。</p>
</header><main id="screen"></main>
<dialog id="takeover-dialog" aria-labelledby="takeover-title"><h2 id="takeover-title">接管远程桌面？</h2>
<p>接管将撤销助手旧操作许可，并允许你使用键鼠。取消会保留当前连接。</p>
<div><button id="takeover-cancel">取消</button><button id="takeover-accept">确认接管</button></div></dialog>
</body></html>"""

VIEWER_STATE_JS = """export function readRuntime(raw, now) {
  if (!raw || typeof raw !== 'object' || raw.schema_version !== 1 || !Number.isFinite(raw.updated_at)
      || now - raw.updated_at * 1000 < -3000 || now - raw.updated_at * 1000 > 6000
      || typeof raw.desktop_ready !== 'boolean' || typeof raw.observer_ready !== 'boolean'
      || !['human', 'agent'].includes(raw.mode)
      || !Number.isInteger(raw.controlling) || raw.controlling < 0 || raw.controlling > 32
      || !Number.isInteger(raw.observing) || raw.observing < 0 || raw.observing > 32) return null;
  return { desktopReady: raw.desktop_ready, observerReady: raw.observer_ready, mode: raw.mode,
    controlling: raw.controlling, observing: raw.observing };
}
export function runtimeNotice(data, mode, connected) {
  if (!data) return '控制权状态暂未确认，不能据此判断助手是否可以继续。';
  if (!data.desktopReady || !data.observerReady) return '远端组件未就绪，请稍后核对；不会自动恢复操作。';
  const own = mode === 'control' && connected ? 1 : 0;
  if (data.controlling > own) return own
    ? '远端记录 ' + data.controlling + ' 个接管连接（包含本窗口）；其他接管窗口需单独断开。'
    : '远端仍记录 ' + data.controlling + ' 个接管连接，请检查其他窗口或稍后刷新；当前仍由人工控制。';
  if (own) return data.controlling > 0 && data.mode === 'human'
    ? '本窗口正在接管；共 ' + data.observing + ' 个观察连接。' : '本窗口已连接，正在同步远端接管状态。';
  return data.mode === 'agent' ? '助手持有控制权；观察连接不会打断它。'
    : '当前由人工控制。没有接管连接后，助手仍需重新授权才会继续。';
}
"""

VIEWER_JS = """import { readRuntime, runtimeNotice } from './viewer-state.js';
const screen = document.getElementById('screen');
const state = document.getElementById('state');
const runtime = document.getElementById('runtime');
const retry = document.getElementById('reconnect');
const scale = document.getElementById('scale');
const fullscreen = document.getElementById('fullscreen');
const toolsMessage = document.getElementById('tools-message');
const moreTools = document.getElementById('viewer-more');
if (window.self !== window.top && moreTools) {
  document.documentElement.classList.add('embedded'); moreTools.hidden = false;
  moreTools.addEventListener('click', () => {
    const shown = document.documentElement.classList.toggle('show-tools');
    moreTools.setAttribute('aria-expanded', String(shown));
    moreTools.textContent = shown ? '收起工具' : '画面工具';
  });
}
const takeoverDialog = document.getElementById('takeover-dialog');
let consentRequest = null;
const keyButtons = ['key-address', 'key-tab', 'key-escape'].map(id => document.getElementById(id)).filter(Boolean);
let mode = '';
let rfb = null;
let RFB = null;
let generation = 0;
let connection = 'disconnected';
let snapshot = null;
let pending = null;
let closed = false;
let connectionTimer = null;
function canSendKeys() { return mode === 'control' && connection === 'connected' && rfb && !rfb.viewOnly; }
function toolNotice(text) { if (toolsMessage) toolsMessage.textContent = text; }
function updateTools() {
  const allowed = Boolean(canSendKeys());
  for (const button of keyButtons) button.disabled = !allowed;
  toolNotice(allowed ? '快捷按键会发送到远端当前焦点窗口。' : '快捷按键仅在接管并连接成功后可用。');
}
function applyScale() {
  if (rfb) { rfb.scaleViewport = scale?.value !== 'actual'; rfb.clipViewport = false; rfb.resizeSession = false; }
}
function sendShortcut(kind) {
  if (!canSendKeys()) return;
  const client = rfb;
  try {
    if (kind === 'address') {
      try { client.sendKey(0xffe3, 'ControlLeft', true); client.sendKey(0x006c, 'KeyL'); }
      finally { client.sendKey(0xffe3, 'ControlLeft', false); }
    } else if (kind === 'tab') client.sendKey(0xff09, 'Tab');
    else if (kind === 'escape') client.sendKey(0xff1b, 'Escape');
    else return;
    client.focus();
    toolNotice('快捷按键已发送，请以远端画面为准。');
  } catch (_) { toolNotice('快捷按键未能发送，请先检查连接。'); }
}
function renderRuntime() {
  runtime.textContent = runtimeNotice(readRuntime(snapshot, Date.now()), mode, connection === 'connected');
}
function connectionState(value, text) {
  if (value !== 'connecting' && connectionTimer) { clearTimeout(connectionTimer); connectionTimer = null; }
  connection = value;
  state.textContent = text;
  retry.disabled = value === 'connecting' || value === 'connected' || !RFB;
  updateTools();
  renderRuntime();
}
function invalidateStatus() {
  snapshot = null;
  const request = pending; pending = null;
  if (request) request.abort();
}
function cancelConsent() {
  consentRequest = null;
  if (takeoverDialog?.open) takeoverDialog.close();
}
document.getElementById('takeover-cancel')?.addEventListener('click', cancelConsent);
takeoverDialog?.addEventListener('cancel', () => { consentRequest = null; });
document.getElementById('takeover-accept')?.addEventListener('click', () => {
  const request = consentRequest;
  cancelConsent();
  if (request && request.generation === generation && !closed) switchMode('control', request.force, true);
});
function dropConnection() {
  cancelConsent();
  if (connectionTimer) { clearTimeout(connectionTimer); connectionTimer = null; }
  generation++;
  invalidateStatus();
  const old = rfb; rfb = null;
  if (old) old.disconnect();
  screen.replaceChildren();
}
function switchMode(next, force = false, confirmed = false) {
  if (closed || !RFB || (mode === next && !force && (connection === 'connecting' || connection === 'connected'))) return;
  if (next === 'control' && !confirmed) {
    if (consentRequest) return;
    try {
      if (!takeoverDialog || typeof takeoverDialog.showModal !== 'function') throw Error();
      consentRequest = { generation, force }; takeoverDialog.showModal();
    } catch (_) { cancelConsent(); state.textContent = '接管确认组件不可用，请刷新页面；当前连接保持。'; }
    return;
  }
  dropConnection();
  mode = next;
  document.getElementById('observe').setAttribute('aria-pressed', String(next === 'observe'));
  document.getElementById('control').setAttribute('aria-pressed', String(next === 'control'));
  connectionState('connecting', next === 'observe' ? '正在连接只读观察通道…' : '正在连接接管通道…');
  const id = generation;
  let client;
  try {
    const url = new URL('websockify', window.location.href);
    url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    url.searchParams.set('token', next);
    client = new RFB(screen, url.href, { shared: true });
    rfb = client;
    client.viewOnly = next === 'observe';
    applyScale();
  } catch (_) {
    dropConnection();
    connectionState('error', '画面连接失败，请检查服务后手动重连。');
    return;
  }
  const current = () => id === generation && client === rfb;
  client.addEventListener('connect', () => {
    if (!current()) return;
    connectionState('connected', next === 'observe' ? '仅观察已连接 · 键鼠不可操作' : '接管已连接 · 点击画面后可使用键鼠');
    void readStatus();
  });
  client.addEventListener('disconnect', event => {
    if (!current()) return;
    rfb = null; screen.replaceChildren(); invalidateStatus();
    connectionState('disconnected', event.detail.clean ? '画面已断开；需要时请手动重连。' : '画面连接中断，请检查连接后手动重连。');
    void readStatus();
  });
  client.addEventListener('securityfailure', () => {
    if (current()) { dropConnection(); connectionState('error', '画面认证失败，请核对远端配置。'); }
  });
  client.addEventListener('credentialsrequired', () => {
    if (current()) { dropConnection(); connectionState('error', '远端认证配置已改变，请先核对服务设置。'); }
  });
  connectionTimer = setTimeout(() => {
    if (current() && connection === 'connecting') {
      dropConnection(); connectionState('error', '画面连接超时，请检查服务后手动重连。');
    }
  }, 12000);
}
async function readStatus() {
  if (pending || closed) return;
  const request = new AbortController(); pending = request;
  const timer = setTimeout(() => request.abort(), 3000);
  try {
    const response = await fetch('status.json', { cache: 'no-store', signal: request.signal });
    if (!response.ok) throw Error();
    const value = await response.json();
    if (!closed && pending === request) snapshot = value;
  } catch (_) { if (!closed && pending === request) snapshot = null; }
  finally {
    clearTimeout(timer);
    if (pending === request) pending = null;
    renderRuntime();
  }
}
document.getElementById('observe').addEventListener('click', () => switchMode('observe'));
document.getElementById('control').addEventListener('click', () => switchMode('control'));
scale?.addEventListener('change', applyScale);
document.getElementById('key-address')?.addEventListener('click', () => sendShortcut('address'));
document.getElementById('key-tab')?.addEventListener('click', () => sendShortcut('tab'));
document.getElementById('key-escape')?.addEventListener('click', () => sendShortcut('escape'));
if (fullscreen) fullscreen.disabled = !document.fullscreenEnabled;
fullscreen?.addEventListener('click', async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch (_) { if (!closed) toolNotice('浏览器未允许切换全屏，可继续使用窗口模式。'); }
});
document.addEventListener('fullscreenchange', () => {
  if (fullscreen) fullscreen.textContent = document.fullscreenElement ? '退出全屏' : '全屏'; applyScale();
});
retry.addEventListener('click', () => switchMode(mode || 'observe', true));
document.getElementById('disconnect').addEventListener('click', () => {
  dropConnection(); mode = '';
  for (const id of ['observe', 'control']) document.getElementById(id).setAttribute('aria-pressed', 'false');
  connectionState('disconnected', '画面已主动断开，助手不会因此自动恢复操作。');
});
let ticks = 0;
const poll = setInterval(() => {
  renderRuntime(); ticks++;
  if ((!document.hidden && ticks % 2 === 0) || ticks % 10 === 0) void readStatus();
}, 1000);
window.addEventListener('pagehide', () => {
  closed = true; clearInterval(poll); if (pending) pending.abort(); dropConnection();
  connectionState('disconnected', '画面已断开；返回页面后默认以只读模式重新连接。');
});
window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
void readStatus();
import('./novnc/core/rfb.js').then(module => {
  if (!closed) { RFB = module.default; switchMode('observe'); }
}).catch(() => connectionState('error', '画面组件加载失败，请刷新页面或检查远端安装。'));
"""

VIEWER_CSS = """*{box-sizing:border-box}body{margin:0;background:#161c26;color:#e9eef5;font:14px system-ui;display:flex;flex-direction:column;height:100dvh}
header{padding:14px 20px;border-bottom:1px solid #3b4656}nav{display:inline-flex;gap:8px;margin-left:20px}
button{background:#253244;color:inherit;border:1px solid #607086;border-radius:6px;padding:7px 12px;cursor:pointer}
button[aria-pressed=true]{background:#4263ae;border-color:#97b5ff}button:focus-visible{outline:3px solid #a7c8ff}
button:disabled{opacity:.5;cursor:default}
dialog{max-width:min(440px,90vw);background:#161c26;color:#e9eef5;border:1px solid #607086;border-radius:10px;padding:24px}
dialog::backdrop{background:#0009}dialog h2{margin:0;font-size:20px}dialog>div{display:flex;justify-content:flex-end;gap:12px;margin-top:20px}
.viewer-tools{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:10px;font-size:12px}
.viewer-tools label{display:flex;align-items:center;gap:6px}.viewer-tools>span{margin-left:8px;color:#b9c5d5}
select{background:#253244;color:inherit;border:1px solid #607086;border-radius:6px;padding:7px 8px}
select:focus-visible{outline:3px solid #a7c8ff}#tools-message{font-size:12px;color:#b9c5d5}
p{margin:9px 0 0;line-height:1.5}header>p:last-child{font-size:12px;color:#b9c5d5}
header{flex:none}main{flex:1;min-height:0;overflow:hidden;background:#111}
@media(max-width:620px){header{padding:12px}nav{display:flex;flex-wrap:wrap;margin:10px 0 0}button,select{min-height:44px}.viewer-tools>span{margin-left:0}}
.embedded body{background:#f8f9fa;color:#343b45}.embedded header{padding:10px 12px;border-color:#dce0e5}
.embedded header>strong{display:none}.embedded nav{margin:0;display:inline-flex;flex-wrap:wrap;gap:6px}
.embedded button,.embedded select{background:#fff;color:#343b45;border-color:#c8cfd8;font-size:12px}
.embedded button[aria-pressed=true]{background:#e8eef9;border-color:#a6b8da;color:#365b96}
.embedded header>p{font-size:11px;color:#5e6774}.embedded header>p:last-child{display:none}
.embedded:not(.show-tools) .viewer-tools,.embedded:not(.show-tools) #tools-message{display:none}
.embedded main{background:#eef0f3}#viewer-more{margin-left:8px}
"""


def desktop_environment(home: Path) -> dict[str, str]:
    # Use the persistent user bus, not a bus/daemon owned by the desktop's
    # control group. Restarting the desktop must not relock an unlocked vault.
    runtime = f"/run/user/{os.getuid()}"
    return {"HOME": str(home), "USER": os.environ.get("USER", ""), "LANG": "C.UTF-8",
            "PATH": "/usr/local/bin:/usr/bin:/bin", "DISPLAY": ":0",
            "XAUTHORITY": str(home / ".Xauthority"), "XDG_RUNTIME_DIR": runtime,
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus", "XDG_CURRENT_DESKTOP": "GNOME"}


def secret_bus(env: dict[str, str], args: list[str]) -> dict:
    result = subprocess.run(["busctl", "--address=" + env["DBUS_SESSION_BUS_ADDRESS"],
                             "--timeout=2", "--json=short", *args],
                            env=env, capture_output=True, timeout=3, check=True)
    return json.loads(result.stdout)


def password_store_status(env: dict[str, str]) -> dict:
    """Metadata only: never enumerate items, passwords, accounts or cookies."""
    try:
        owner = secret_bus(env, ["call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
                                 "org.freedesktop.DBus", "NameHasOwner", "s", "org.freedesktop.secrets"])
        if owner.get("data") != [True]:
            return {"backend": "gnome-libsecret", "state": "unavailable"}
        alias = secret_bus(env, ["call", "org.freedesktop.secrets", "/org/freedesktop/secrets",
                                 "org.freedesktop.Secret.Service", "ReadAlias", "s", "default"])["data"][0]
        if alias == "/":
            state = "needs_setup"
        elif isinstance(alias, str) and re.fullmatch(r"/org/freedesktop/secrets/collection/[A-Za-z0-9_]+", alias):
            locked = secret_bus(env, ["get-property", "org.freedesktop.secrets", alias,
                                      "org.freedesktop.Secret.Collection", "Locked"])["data"]
            # busctl method calls return a tuple/list; get-property returns the
            # property value directly, including a scalar for a boolean.
            if type(locked) is not bool:
                raise ValueError("invalid lock state")
            state = "locked" if locked else "unlocked"
        else:
            raise ValueError("invalid collection path")
        return {"backend": "gnome-libsecret", "state": state}
    except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.SubprocessError):
        return {"backend": "gnome-libsecret", "state": "unavailable"}


def prepare_password_store(env: dict[str, str]) -> None:
    # Only the display variables enter the activation environment, never model
    # keys or the parent's complete environment. No PAM or login changes.
    subprocess.run(["dbus-update-activation-environment", "--systemd", "DISPLAY", "XAUTHORITY"],
                   env=env, capture_output=True, timeout=5, check=True)
    owner = secret_bus(env, ["call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
                             "org.freedesktop.DBus", "NameHasOwner", "s", "org.freedesktop.secrets"])
    if owner.get("data") != [True]:
        subprocess.run(["systemctl", "--user", "start", "gnome-keyring-daemon.service"],
                       env=env, capture_output=True, timeout=10, check=True)
        # D-Bus activation waits for the service to own its name. It does not
        # create/unlock a collection or request any stored secret.
        secret_bus(env, ["call", "org.freedesktop.secrets", "/org/freedesktop/secrets",
                         "org.freedesktop.Secret.Service", "ReadAlias", "s", "default"])
    if password_store_status(env)["state"] == "unavailable":
        raise RuntimeError("system password store unavailable; refusing to start browser with basic storage")


class Control:
    """Cooperative authority for this desktop, not an OS sandbox or task ledger.

    All managed actions and viewer acceptance share a lock. An already running
    action finishes before the viewer is accepted; subsequent old actions fail.
    Never automatically return control when a viewer disconnects.
    """

    def __init__(self, state: Path, home: Path):
        self.state, self.home = state, home

    @contextlib.contextmanager
    def locked(self):
        import fcntl
        info = self.state.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("desktop state must be a private owned directory")
        fd = os.open(self.state / "control.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_uid != os.getuid() or os.fstat(fd).st_mode & 0o077:
                raise RuntimeError("control lock must be private")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def read(self) -> dict:
        fd = os.open(self.state / "control.json", os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r") as stream:
            info = os.fstat(stream.fileno())
            if info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 16384:
                raise RuntimeError("invalid control state permissions or size")
            data = json.load(stream)
        if data.get("version") != 1:
            raise RuntimeError("unsupported desktop control protocol")
        return data

    def save(self, data: dict) -> None:
        write_private(self.state / "control.json", json.dumps(data).encode())

    @staticmethod
    def invalidate(data: dict) -> None:
        data.update(epoch=secrets.token_hex(16), mode="human", lease_hash="", frame=None)

    def start(self) -> None:
        with self.locked():
            data = {"version": 1, "ready": False, "heartbeat": time.time(), "viewers": [], "components": {}}
            self.invalidate(data)
            self.save(data)

    def heartbeat(self, components: dict, *, ready: bool) -> None:
        with self.locked():
            data = self.read()
            data.update(heartbeat=time.time(), components=components, ready=ready)
            if not ready:
                self.invalidate(data)
            self.save(data)

    @staticmethod
    def available(data: dict) -> bool:
        age = time.time() - data["heartbeat"]
        return bool(data["ready"] and 0 <= age <= 8)

    def command(self, argv: list[str], *, stdin: bytes = b"") -> bytes:
        env = {"HOME": str(self.home), "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
               "DISPLAY": ":0", "XAUTHORITY": str(self.home / ".Xauthority")}
        result = subprocess.run(argv, input=stdin, capture_output=True, env=env, timeout=12)
        if result.returncode:
            raise RuntimeError("desktop operation failed")
        return result.stdout

    def observer_start(self, session: str) -> None:
        with self.locked():
            data = self.read()
            if not self.available(data):
                raise RuntimeError("resident desktop is not ready")
            data["observation"] = {"session": session, "heartbeat": time.time(), "ready": False, "viewers": []}
            self.save(data)

    def observer_heartbeat(self, session: str, *, ready: bool) -> None:
        with self.locked():
            data = self.read()
            observer = data.get("observation", {})
            if observer.get("session") != session:
                raise RuntimeError("observer instance changed")
            observer.update(heartbeat=time.time(), ready=ready)
            if not ready:
                observer["viewers"] = []
            self.save(data)
            # Feedback failure must not tear down an otherwise usable picture.
            # The last snapshot expires in the page and becomes unknown.
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                self.publish_viewer_status(data)

    def publish_viewer_status(self, data: dict) -> None:
        web = self.state / "observer-web"
        try:
            info = web.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("viewer directory must be private and owned")
        status = {"schema_version": 1, "updated_at": time.time(), "desktop_ready": self.available(data),
                  "observer_ready": self.observer_available(data), "mode": data["mode"],
                  "controlling": len(data["viewers"]),
                  "observing": len(data.get("observation", {}).get("viewers", []))}
        # Static read-only snapshot, not a new command or authority endpoint.
        # No leases, epochs, account data, clipboard, screenshots or window names.
        target = web / "status.json"
        if target.is_symlink():
            raise ValueError("refusing symlink status snapshot")
        # Unique temporary names survive a killed publisher without blocking
        # the next heartbeat; never remove another writer's pending file.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=web, prefix=".status-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(status).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def observer_available(data: dict) -> bool:
        observer = data.get("observation", {})
        return bool(Control.available(data) and observer.get("ready")
                    and 0 <= time.time() - observer.get("heartbeat", 0) <= 8)

    def observer_event(self, session: str, event: str, viewer: str) -> None:
        if event not in ("connect", "disconnect") or not re.fullmatch(r"[0-9A-Fa-fxX]{1,32}", viewer):
            raise ValueError("invalid observer event")
        with self.locked():
            data = self.read()
            observer = data.get("observation", {})
            if observer.get("session") != session:
                raise RuntimeError("observer instance changed")
            viewers = set(observer["viewers"])
            if event == "connect":
                if not self.observer_available(data) or len(viewers) >= 32:
                    raise RuntimeError("observer unavailable or full")
                viewers.add(viewer)
            else:
                viewers.discard(viewer)
            observer["viewers"] = sorted(viewers)
            # No epoch/lease/frame/mode changes. This channel is server-view-only.
            self.save(data)

    def rpc(self, request: dict) -> dict:
        op = request.get("op")
        with self.locked():
            data = self.read()
            ready = self.available(data)
            if op == "status":
                return {"version": 1, "ready": ready, "mode": data["mode"], "epoch": data["epoch"],
                        "viewers": len(data["viewers"]), "heartbeat_age_seconds": round(time.time() - data["heartbeat"], 2),
                        "password_store": password_store_status(desktop_environment(self.home)),
                        "observation": {"available": self.observer_available(data),
                                        "viewers": len(data.get("observation", {}).get("viewers", []))}}
            if op in ("takeover", "viewer-connect", "viewer-disconnect"):
                if op != "takeover":
                    viewer = os.environ.get("RFB_CLIENT_ID", "")
                    if not re.fullmatch(r"[0-9A-Fa-fxX]{1,32}", viewer):
                        raise ValueError("invalid viewer identity")
                    viewers = set(data["viewers"])
                    if op == "viewer-connect":
                        if len(viewers) >= 32:
                            raise RuntimeError("too many viewers")
                        viewers.add(viewer)
                    else:
                        viewers.discard(viewer)
                    data["viewers"] = sorted(viewers)
                self.invalidate(data)
                self.save(data)
                return {"mode": "human", "epoch": data["epoch"]}
            if not ready:
                raise RuntimeError("desktop is unavailable; no action was sent")
            if op == "release":
                if request.get("epoch") != data["epoch"] or data["viewers"] or data["mode"] != "human":
                    raise RuntimeError("desktop changed or a viewer is connected; disconnect and check status again")
                lease = secrets.token_hex(32)
                data.update(mode="agent", epoch=secrets.token_hex(16), lease_hash=hashlib.sha256(lease.encode()).hexdigest(), frame=None)
                self.save(data)
                return {"lease": lease, "epoch": data["epoch"]}
            if op == "screenshot":
                fd, name = tempfile.mkstemp(suffix=".png", dir=self.state)
                os.close(fd)
                try:
                    self.command(["scrot", "-o", name])
                    png = Path(name).read_bytes()
                finally:
                    Path(name).unlink(missing_ok=True)
                if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > 8_000_000:
                    raise RuntimeError("invalid desktop screenshot")
                frame = {"id": secrets.token_hex(16), "time": time.time()}
                data["frame"] = frame
                self.save(data)
                return {"png": base64.b64encode(png).decode(), "frame": frame["id"], "epoch": data["epoch"]}
            if op == "active":
                return {"text": self.command(["xdotool", "getactivewindow", "getwindowname"]).decode("utf-8", "replace")[:300]}
            if op != "action":
                raise ValueError("unsupported desktop operation")
            lease = str(request.get("lease", ""))
            frame = data.get("frame") or {}
            if (data["mode"] != "agent" or data["viewers"] or not lease
                    or not secrets.compare_digest(hashlib.sha256(lease.encode()).hexdigest(), data["lease_hash"])
                    or request.get("epoch") != data["epoch"] or request.get("frame") != frame.get("id")
                    or not 0 <= time.time() - frame.get("time", 0) <= 60):
                raise RuntimeError("desktop control or screenshot expired; take a fresh screenshot and obtain control again if needed")
            # Consume before executing: timeout/response loss never permits replay.
            data["frame"] = None
            self.save(data)
            kind = request.get("kind")
            if kind == "click":
                x, y = request.get("x"), request.get("y")
                if type(x) is not int or type(y) is not int or not (0 <= x < 1280 and 0 <= y < 900):
                    raise ValueError("click is outside the desktop")
                button = request.get("button", "1")
                if button not in ("1", "3") or type(request.get("double", False)) is not bool:
                    raise ValueError("invalid mouse button")
                argv = ["xdotool", "mousemove", "--sync", str(x), str(y), "click"]
                self.command(argv + (["--repeat", "2", "--delay", "120"] if request.get("double") else []) + [button])
            elif kind == "type":
                text = request.get("text")
                if not isinstance(text, str) or not 0 < len(text) <= 2000 or "\0" in text:
                    raise ValueError("invalid text length")
                self.command(["xdotool", "type", "--clearmodifiers", "--delay", "5", "--file", "-"], stdin=text.encode())
            elif kind == "key":
                keys = request.get("keys")
                if (not isinstance(keys, str) or len(keys) > 200 or not keys.split()
                        or not all(re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", key) for key in keys.split())):
                    raise ValueError("invalid key names")
                self.command(["xdotool", "key", "--clearmodifiers", "--", *keys.split()])
            else:
                raise ValueError("managed desktop only supports click, type and key")
            return {"accepted": True}


def command_plan(browser: Path, home: Path, state: Path) -> dict[str, list[str]]:
    """One dedicated display; VNC/viewer TCP listeners stay on loopback."""
    authority = str(home / ".Xauthority")
    hook = shlex.join([sys.executable, str(state / "resident-desktop.py"), "rpc", "--home", str(home), "--state", str(state)])
    return {
        "display": ["Xvfb", ":0", "-screen", "0", "1280x900x24", "-s", "0", "-nolisten", "tcp", "-auth", authority],
        "windows": ["openbox", "--sm-disable"],
        "browser": [str(browser), f"--user-data-dir={state / 'browser-profile'}", "--no-first-run",
                    "--no-default-browser-check", "--password-store=gnome-libsecret", "--window-size=1280,900", "about:blank"],
        "vnc": ["x11vnc", "-display", ":0", "-auth", authority, "-localhost", "-listen", "127.0.0.1",
                "-rfbport", "5909", "-rfbportv6", "5909", "-no6", "-noipv6", "-forever", "-shared", "-nopw", "-noxdamage",
                "-accept", hook + " --viewer-event connect", "-gone", hook + " --viewer-event disconnect"],
        "viewer": ["websockify", "--web=/usr/share/novnc", "127.0.0.1:6089", "127.0.0.1:5909"],
    }


def observation_plan(home: Path, state: Path, session: str) -> dict[str, list[str]]:
    hook = shlex.join([sys.executable, str(state / "resident-desktop.py"), "rpc", "--home", str(home),
                       "--state", str(state), "--observer-session", session])
    return {
        "observation_vnc": ["x11vnc", "-display", ":0", "-auth", str(home / ".Xauthority"),
                            "-localhost", "-listen", "127.0.0.1", "-rfbport", "5908", "-rfbportv6", "5908",
                            "-no6", "-noipv6", "-forever", "-shared", "-nopw", "-noxdamage",
                            "-viewonly", "-input", ",", "-nosel",
                            "-accept", hook + " --observer-event connect", "-gone", hook + " --observer-event disconnect"],
        "observation_viewer": ["websockify", f"--web={state / 'observer-web'}", "--token-plugin=TokenFile",
                               f"--token-source={state / 'observer-targets'}", "127.0.0.1:6088"],
    }


def prepare_observer_assets(state: Path) -> None:
    web = state / "observer-web"
    if web.is_symlink():
        raise ValueError("refusing symlink viewer directory")
    web.mkdir(mode=0o700, exist_ok=True)
    info = web.lstat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("viewer directory must be private and owned")
    link = web / "novnc"
    if link.is_symlink():
        if os.readlink(link) != "/usr/share/novnc":
            raise ValueError("unexpected viewer asset link")
    elif link.exists():
        raise ValueError("unexpected viewer asset directory")
    else:
        link.symlink_to("/usr/share/novnc", target_is_directory=True)
    for name, text in (("index.html", VIEWER_HTML), ("viewer.js", VIEWER_JS), ("viewer-state.js", VIEWER_STATE_JS), ("viewer.css", VIEWER_CSS)):
        write_private(web / name, text.encode())
    # These are public mode selectors, not authentication tokens. Only the
    # already-authorized SSH user can reach this loopback viewer server.
    write_private(state / "observer-targets", b"observe: 127.0.0.1:5908\ncontrol: 127.0.0.1:5909\n")


def systemd_quote(value: Path) -> str:
    text = str(value)
    if any(char in text for char in "\n\r\0"):
        raise ValueError("paths must not contain control characters")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def observer_unit_text(python: Path, script: Path, home: Path, state: Path) -> str:
    argv = [systemd_quote(python), systemd_quote(script), "observe", "--home", systemd_quote(home),
            "--state", systemd_quote(state)]
    return ("[Unit]\nDescription=VortoCode view-only desktop observation (SSH access only)\n"
            f"BindsTo={UNIT}\nAfter={UNIT}\nPartOf={UNIT}\n"
            "StartLimitIntervalSec=120\nStartLimitBurst=3\n\n[Service]\nType=simple\nUMask=0077\n"
            "ExecStart=" + " ".join(argv) + "\nRestart=on-failure\nRestartSec=5\nKillMode=control-group\n"
            "TimeoutStopSec=10\nMemoryMax=512M\nCPUQuota=100%\nTasksMax=64\n\n[Install]\nWantedBy=default.target\n")


def install_observer(home: Path, state: Path, unit_dir: Path) -> dict:
    script = state / "resident-desktop.py"
    control = Control(state, home)
    with control.locked():
        info = script.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("installed desktop script must be private and owned")
    if unit_dir.is_symlink():
        raise ValueError("refusing symlink unit directory")
    unit_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    unit = unit_dir / OBSERVER_UNIT
    if unit.exists():
        raise ValueError("observer deployment already exists; back it up before replacing")
    write_private(unit, observer_unit_text(Path(sys.executable), script, home, state).encode())
    return {"unit": str(unit), "started": False}


def observe(home: Path, state: Path) -> None:
    """Attach an input-disabled viewer to the existing desktop; never restart it."""
    check_ports((5908, 6088))
    control = Control(state, home)
    with control.locked():
        control.read()
    fd = os.open(state / "observer.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    import fcntl
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("observer lock must be private and owned")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise
    session = secrets.token_hex(16)
    children: list[subprocess.Popen] = []
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        # Desktop can be starting after an ordinary systemd restart.
        deadline = time.monotonic() + 12
        while True:
            with control.locked():
                ready = control.available(control.read())
            if stopped:
                return
            if ready:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("resident desktop is not ready")
            time.sleep(0.2)
        control.observer_start(session)
        prepare_observer_assets(state)
        for argv in observation_plan(home, state, session).values():
            children.append(subprocess.Popen(argv, env=desktop_environment(home), start_new_session=True))
        while not stopped:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("observer component exited")
            with control.locked():
                if not control.available(control.read()):
                    raise RuntimeError("resident desktop stopped")
            control.observer_heartbeat(session, ready=True)
            time.sleep(1)
    finally:
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            control.observer_heartbeat(session, ready=False)
        for child in reversed(children):
            if child.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGTERM)
        for child in reversed(children):
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        os.close(fd)


def diagnostics(browser: Path, home: Path) -> dict:
    return {"platform": sys.platform, "browser_executable": browser.is_file() and os.access(browser, os.X_OK),
            "tools": {name: bool(shutil.which(name)) for name in TOOLS},
            "viewer_assets": Path("/usr/share/novnc/vnc.html").is_file(),
            "display_occupied": Path("/tmp/.X11-unix/X0").exists(),
            "authority_present": (home / ".Xauthority").exists()}


def unit_text(python: Path, script: Path, browser: Path, home: Path, state: Path) -> str:
    def quote(value: Path) -> str:
        text = str(value)
        if any(char in text for char in "\n\r\0"):
            raise ValueError("paths must not contain control characters")
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'

    argv = [quote(python), quote(script), "run", "--browser", quote(browser),
            "--home", quote(home), "--state", quote(state)]
    return ("[Unit]\nDescription=VortoCode dedicated resident desktop (SSH access only)\n"
            "After=network.target\nStartLimitIntervalSec=120\nStartLimitBurst=3\n\n"
            "[Service]\nType=simple\nUMask=0077\nExecStart=" + " ".join(argv) + "\n"
            "Restart=on-failure\nRestartSec=5\nKillMode=control-group\nTimeoutStopSec=15\n"
            "MemoryMax=2G\nCPUQuota=200%\nTasksMax=512\n\n[Install]\nWantedBy=default.target\n")


def write_private(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing symlink: {path.name}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install(browser: Path, home: Path, state: Path, unit_dir: Path) -> dict:
    if not browser.is_absolute() or not browser.is_file() or not os.access(browser, os.X_OK):
        raise ValueError("browser must be an existing absolute executable")
    for directory in (state, unit_dir):
        if directory.is_symlink():
            raise ValueError("refusing symlink directory")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    script = state / "resident-desktop.py"
    unit = unit_dir / UNIT
    # Do not overwrite existing deployment. Keep upgrades explicit and reversible.
    if script.exists() or unit.exists():
        raise ValueError("desktop deployment already exists; back it up before replacing")
    source = Path(__file__).read_bytes()
    text = unit_text(Path(sys.executable), script, browser, home, state)
    write_private(script, source)
    try:
        write_private(unit, text.encode())
    except BaseException:
        script.unlink()
        raise
    return {"unit": str(unit), "script_sha256": hashlib.sha256(source).hexdigest(),
            "started": False, "next": f"systemctl --user daemon-reload && systemctl --user enable --now {UNIT}"}


def check_ports(ports=(5909, 6089)) -> None:
    for port in ports:
        with socket.socket() as probe:
            # A recently stopped viewer may leave TIME_WAIT sockets. Those do
            # not mean another server owns the port; an active listener still fails.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))


def run(browser: Path, home: Path, state: Path) -> None:
    check = diagnostics(browser, home)
    if sys.platform != "linux" or not check["browser_executable"] or not check["viewer_assets"] or not all(check["tools"].values()):
        raise RuntimeError("desktop prerequisites missing; use plan for details")
    if check["display_occupied"]:
        raise RuntimeError("display :0 is already occupied; will not attach to an unrelated desktop")
    check_ports()
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = open(state / "service.lock", "a+")
    import fcntl
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    control = Control(state, home)
    control.start()
    authority = home / ".Xauthority"
    fd = os.open(authority, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError("Xauthority must be private and owned by the desktop user")
    finally:
        os.close(fd)
    # Cookie stays out of process arguments and logs; existing authority entries remain.
    subprocess.run(["xauth", "-f", str(authority), "source", "-"],
                   input=f"add :0 . {secrets.token_hex(16)}\n".encode(), check=True, capture_output=True)
    env = desktop_environment(home)
    children: list[subprocess.Popen] = []
    components: dict[str, int] = {}
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    plan = command_plan(browser, home, state)
    try:
        for name, argv in plan.items():
            if stopped:
                return
            if name == "browser":
                prepare_password_store(env)
            children.append(subprocess.Popen(argv, env=env, start_new_session=True))
            components[name] = children[-1].pid
            if name == "display":
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not stopped:
                    if children[-1].poll() is not None:
                        raise RuntimeError("virtual display exited before ready")
                    ready = subprocess.run(["xdpyinfo"], env=env, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, timeout=2)
                    if ready.returncode == 0:
                        break
                    time.sleep(0.15)
                else:
                    raise RuntimeError("virtual display readiness timed out")
        print("desktop started; viewer 127.0.0.1:6089; human control by default; no schedules enabled", flush=True)
        while not stopped:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("desktop component exited; stopping owned child processes")
            control.heartbeat(components, ready=True)
            time.sleep(1)
    finally:
        with contextlib.suppress(OSError, ValueError, RuntimeError):
            control.heartbeat(components, ready=False)
        for child in reversed(children):
            if child.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGTERM)
        for child in reversed(children):
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        lock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "install", "run", "rpc", "status", "takeover", "observe", "install-observer"))
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--state", type=Path)
    parser.add_argument("--viewer-event", choices=("connect", "disconnect"))
    parser.add_argument("--observer-event", choices=("connect", "disconnect"))
    parser.add_argument("--observer-session")
    args = parser.parse_args()
    home = args.home.resolve()
    state = args.state or home / ".local/share/vortocode/computer"
    if args.command == "observe":
        observe(home, state)
        return 0
    if args.command == "install-observer":
        print(json.dumps(install_observer(home, state, home / ".config/systemd/user")))
        return 0
    if args.command in ("rpc", "status", "takeover"):
        if args.command == "rpc":
            if args.observer_event:
                if not args.observer_session or args.viewer_event:
                    raise ValueError("observer session is required")
                Control(state, home).observer_event(args.observer_session, args.observer_event, os.environ.get("RFB_CLIENT_ID", ""))
                return 0
            elif args.viewer_event:
                request = {"op": "viewer-" + args.viewer_event}
            else:
                raw = sys.stdin.buffer.read(16385)
                if len(raw) > 16384:
                    raise ValueError("desktop request too large")
                request = json.loads(raw)
                if not isinstance(request, dict) or request.get("op", "").startswith("viewer-"):
                    raise ValueError("invalid desktop request")
        else:
            request = {"op": args.command}
        print(json.dumps(Control(state, home).rpc(request)))
        return 0
    if args.browser is None:
        parser.error("--browser is required for plan, install and run")
    if args.command == "plan":
        print(json.dumps({"diagnostics": diagnostics(args.browser, home), "commands": command_plan(args.browser, home, state)}, indent=2))
    elif args.command == "install":
        print(json.dumps(install(args.browser, home, state, home / ".config/systemd/user"), indent=2))
    else:
        run(args.browser, home, state)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"resident desktop failed: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
