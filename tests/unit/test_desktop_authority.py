"""Managed desktop authority exercised through the real tool/backend protocol, offline."""
import asyncio
import base64
import json
from pathlib import Path
import runpy
import threading
import time

import pytest

from src.agents.tools import desktop
from src.agents.trust import INTERACT

helper = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/resident-desktop.py"))
PNG = b"\x89PNG\r\n\x1a\n" + b"test pixels"


@pytest.fixture
def authority(tmp_path, monkeypatch):
    monkeypatch.setitem(helper["Control"].rpc.__globals__, "password_store_status",
                        lambda _env: {"backend": "gnome-libsecret", "state": "unavailable"})
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    control = helper["Control"](state, tmp_path)
    control.start()
    control.heartbeat({}, ready=True)
    calls = []

    def command(argv, *, stdin=b""):
        if argv[0] == "scrot":
            Path(argv[-1]).write_bytes(PNG)
            return b""
        if "getwindowname" in argv:
            return b"Synthetic window"
        calls.append((argv, stdin))
        return b""

    control.command = command
    return control, calls


def release(control):
    epoch = control.rpc({"op": "status"})["epoch"]
    return control.rpc({"op": "release", "epoch": epoch})


def permit(control, lease):
    shot = control.rpc({"op": "screenshot"})
    assert base64.b64decode(shot["png"]) == PNG
    return {"lease": lease["lease"], "epoch": shot["epoch"], "frame": shot["frame"]}


def action(control, token):
    return control.rpc({"op": "action", **token, "kind": "key", "keys": "Tab"})


def test_viewer_invalidates_waiting_actions_and_disconnect_never_releases(authority, monkeypatch):
    control, calls = authority
    lease = release(control)
    token = permit(control, lease)
    monkeypatch.setenv("RFB_CLIENT_ID", "0x1")
    control.rpc({"op": "viewer-connect"})
    with pytest.raises(RuntimeError, match="expired"):
        action(control, token)
    assert calls == []
    with pytest.raises(RuntimeError, match="viewer"):
        release(control)
    control.rpc({"op": "viewer-disconnect"})
    assert control.rpc({"op": "status"})["mode"] == "human"
    with pytest.raises(RuntimeError, match="expired"):
        action(control, token)
    action(control, permit(control, release(control)))
    assert len(calls) == 1


def test_consumed_frame_cannot_replay_even_after_response_loss(authority):
    control, calls = authority
    token = permit(control, release(control))
    action(control, token)
    with pytest.raises(RuntimeError, match="expired"):
        action(control, token)
    assert len(calls) == 1


def test_observer_does_not_invalidate_lease_epoch_or_pending_frame(authority):
    control, calls = authority
    token = permit(control, release(control))
    control.observer_start("observer-1")
    control.observer_heartbeat("observer-1", ready=True)
    control.observer_event("observer-1", "connect", "0x1")
    result = control.rpc({"op": "status"})
    assert result["mode"] == "agent" and result["epoch"] == token["epoch"]
    assert result["viewers"] == 0 and result["observation"] == {"available": True, "viewers": 1}
    action(control, token)
    assert len(calls) == 1
    control.observer_event("observer-1", "disconnect", "0x1")
    assert control.rpc({"op": "status"})["mode"] == "agent"


def test_observer_allows_explicit_agent_release_but_controlling_viewer_cancels(authority, monkeypatch):
    control, calls = authority
    control.observer_start("observer-1")
    control.observer_heartbeat("observer-1", ready=True)
    control.observer_event("observer-1", "connect", "0x1")
    token = permit(control, release(control))
    monkeypatch.setenv("RFB_CLIENT_ID", "0x2")
    control.rpc({"op": "viewer-connect"})
    with pytest.raises(RuntimeError, match="expired"):
        action(control, token)
    assert not calls
    result = control.rpc({"op": "status"})
    assert result["viewers"] == 1 and result["observation"]["viewers"] == 1
    control.rpc({"op": "viewer-disconnect"})
    assert control.rpc({"op": "status"})["mode"] == "human"


def test_observer_restart_and_old_callbacks_cannot_clobber_new_instance(authority):
    control, _ = authority
    token = permit(control, release(control))
    control.observer_start("old")
    control.observer_heartbeat("old", ready=True)
    control.observer_event("old", "connect", "0x1")
    control.observer_start("new")
    control.observer_heartbeat("new", ready=True)
    control.observer_event("new", "connect", "0x1")
    with pytest.raises(RuntimeError, match="instance changed"):
        control.observer_event("old", "disconnect", "0x1")
    with pytest.raises(RuntimeError, match="instance changed"):
        control.observer_heartbeat("old", ready=False)
    assert control.rpc({"op": "status"})["observation"]["viewers"] == 1
    control.observer_heartbeat("new", ready=False)
    assert control.rpc({"op": "status"})["observation"] == {"available": False, "viewers": 0}
    action(control, token)


def test_missing_or_expired_observer_never_claims_available(authority):
    control, _ = authority
    assert control.rpc({"op": "status"})["observation"] == {"available": False, "viewers": 0}
    control.observer_start("one")
    with pytest.raises(RuntimeError, match="unavailable"):
        control.observer_event("one", "connect", "0x1")
    control.observer_heartbeat("one", ready=True)
    with control.locked():
        data = control.read()
        data["observation"]["heartbeat"] -= 9
        control.save(data)
    assert control.rpc({"op": "status"})["observation"]["available"] is False


@pytest.mark.parametrize("change", ["restart", "stale_frame", "stale_service", "new_screenshot", "wrong_lease", "takeover"])
def test_changed_authority_or_observation_never_sends_old_action(authority, change):
    control, calls = authority
    token = permit(control, release(control))
    if change == "restart":
        control.start()
        control.heartbeat({}, ready=True)
    elif change in ("stale_frame", "stale_service"):
        with control.locked():
            data = control.read()
            if change == "stale_frame":
                data["frame"]["time"] = time.time() - 61
            else:
                data["heartbeat"] = time.time() - 9
            control.save(data)
    elif change == "new_screenshot":
        control.rpc({"op": "screenshot"})
    elif change == "wrong_lease":
        token["lease"] = "wrong"
    else:
        control.rpc({"op": "takeover"})
    with pytest.raises(RuntimeError):
        action(control, token)
    assert calls == []


def test_release_is_bound_to_epoch_and_one_session(authority):
    control, _ = authority
    old = control.rpc({"op": "status"})["epoch"]
    control.rpc({"op": "takeover"})
    with pytest.raises(RuntimeError):
        control.rpc({"op": "release", "epoch": old})
    lease = release(control)
    with pytest.raises(RuntimeError):
        release(control)
    assert lease["lease"] not in json.dumps(control.read())


def test_viewer_acceptance_waits_for_running_action_then_blocks_later_actions(authority, monkeypatch):
    control, _ = authority
    token = permit(control, release(control))
    entered, finish, connected = threading.Event(), threading.Event(), threading.Event()
    errors = []

    def command(argv, *, stdin=b""):
        entered.set()
        if not finish.wait(3):
            raise RuntimeError("test operation stalled")
        return b""

    def perform():
        try:
            action(control, token)
        except Exception as error:
            errors.append(error)

    def connect():
        try:
            control.rpc({"op": "viewer-connect"})
            connected.set()
        except Exception as error:
            errors.append(error)

    control.command = command
    monkeypatch.setenv("RFB_CLIENT_ID", "0x9")
    worker = threading.Thread(target=perform)
    viewer = threading.Thread(target=connect)
    worker.start()
    try:
        assert entered.wait(2)
        viewer.start()
        assert not connected.wait(0.1)
    finally:
        finish.set()
        worker.join(3)
        if viewer.ident:
            viewer.join(3)
    assert connected.is_set() and not errors
    with pytest.raises(RuntimeError, match="expired"):
        action(control, token)


@pytest.mark.parametrize("payload", [
    {"kind": "shell", "command": "reboot"},
    {"kind": "key", "keys": "$(reboot)"},
    {"kind": "click", "x": 1280, "y": 50},
    {"kind": "click", "x": True, "y": 50},
    {"kind": "type", "text": "x" * 2001},
    {"kind": "open", "app": "bash"},
])
def test_broker_does_not_accept_shell_or_invalid_operations(authority, payload):
    control, calls = authority
    token = permit(control, release(control))
    with pytest.raises(ValueError):
        control.rpc({"op": "action", **token, **payload})
    assert calls == []


def test_private_control_state_rejects_symlink_and_public_permissions(authority, tmp_path):
    control, _ = authority
    state_file = control.state / "control.json"
    original = state_file.read_bytes()
    state_file.chmod(0o644)
    with pytest.raises(RuntimeError, match="permissions"):
        control.rpc({"op": "status"})
    state_file.unlink()
    other = tmp_path / "other"
    other.write_bytes(original)
    state_file.symlink_to(other)
    with pytest.raises(OSError):
        control.rpc({"op": "status"})


class Transport(desktop.DesktopBackend):
    def __init__(self, control):
        super().__init__("ssh:dedicated-vm", ":0")
        self.control = control
        self.raw_actions = []

    async def _raw_run(self, command, *, stdin=b""):
        if command[0] == "sh" and command[2].startswith("if [ -e"):
            return b"managed"
        if command[0] == "sh" and command[2].startswith("exec python3"):
            return json.dumps(self.control.rpc(json.loads(stdin))).encode()
        self.raw_actions.append((command, stdin))
        raise AssertionError("managed action bypassed broker")


async def test_real_tools_freeze_permit_before_confirmation_and_takeover_blocks_it(authority, tmp_path, monkeypatch):
    control, calls = authority
    backend = Transport(control)
    confirmations = []
    take_during_confirmation = False

    async def confirm(message, kind=None):
        confirmations.append(kind)
        if take_during_confirmation:
            monkeypatch.setenv("RFB_CLIENT_ID", "0x3")
            control.rpc({"op": "viewer-connect"})
        return True

    async def no_settle(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_settle)
    tools = {t.name: t for t in desktop.build_desktop_tools(str(tmp_path), confirm, backend=backend)}
    assert (await tools["desktop_release"].handler({}))["ok"]
    assert (await tools["desktop_screenshot"].handler({}))["images"]
    assert (await tools["desktop_type"].handler({"text": "hello"}))["images"]
    assert calls[0][1] == b"hello" and backend.raw_actions == []
    take_during_confirmation = True
    result = await tools["desktop_click"].handler({"x": 100, "y": 200})
    assert result["ok"] is False and "expired" in result["text"]
    assert len(calls) == 1 and all(kind == INTERACT for kind in confirmations)


async def test_release_refusal_and_denied_confirmation_do_not_grant_lease(authority, tmp_path, monkeypatch):
    control, _ = authority
    backend = Transport(control)
    asked = []

    async def deny(message, kind=None):
        asked.append(message)
        return False

    tools = {t.name: t for t in desktop.build_desktop_tools(str(tmp_path), deny, backend=backend)}
    assert "没有同意" in await tools["desktop_release"].handler({})
    assert control.read()["mode"] == "human" and not backend._lease
    monkeypatch.setenv("RFB_CLIENT_ID", "0x2")
    control.rpc({"op": "viewer-connect"})
    asked.clear()
    assert (await tools["desktop_release"].handler({}))["ok"] is False
    assert asked == []


async def test_installed_broken_protocol_never_falls_back(authority):
    control, _ = authority
    backend = Transport(control)
    (control.state / "control.json").unlink()
    for _ in range(2):
        with pytest.raises(FileNotFoundError):
            await backend.run(["xdotool", "key", "--", "Tab"])
    assert backend._managed is True and backend.raw_actions == []
