"""Deployment preparation is explicit, private, and never overwrites an existing desktop."""
from pathlib import Path
import json
import runpy
import socket
import stat
import subprocess

import pytest

desktop = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/resident-desktop.py"))


def _browser(root):
    browser = root / "browser"
    browser.write_text("#!/bin/sh\nexit 0\n")
    browser.chmod(0o700)
    return browser


def test_install_is_private_and_a_second_install_preserves_the_original(tmp_path):
    browser = _browser(tmp_path)
    state, units = tmp_path / "state", tmp_path / "units"
    result = desktop["install"](browser, tmp_path, state, units)
    assert result["started"] is False
    unit = units / desktop["UNIT"]
    original = unit.read_bytes()
    assert stat.S_IMODE(unit.stat().st_mode) == 0o600
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert (state / "resident-desktop.py").read_bytes() == (Path(__file__).resolve().parents[2] / "scripts/resident-desktop.py").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        desktop["install"](browser, tmp_path, state, units)
    assert unit.read_bytes() == original


def test_install_refuses_invalid_browser_and_symlink_state_without_writes(tmp_path):
    state, units = tmp_path / "state", tmp_path / "units"
    with pytest.raises(ValueError):
        desktop["install"](tmp_path / "missing", tmp_path, state, units)
    assert not state.exists() and not units.exists()
    other = tmp_path / "other"
    other.mkdir()
    state.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        desktop["install"](_browser(tmp_path), tmp_path, state, units)
    assert not list(other.iterdir())


def test_unit_preserves_literal_paths_and_run_plan_keeps_control_on_loopback(tmp_path):
    text = desktop["unit_text"](Path('/usr/bin/python3'), tmp_path / 'a "%$ b.py',
                                tmp_path / "browser", tmp_path, tmp_path / "state")
    assert '\\"%%$$' in text
    assert "KillMode=control-group" in text and "MemoryMax=2G" in text
    plan = desktop["command_plan"](tmp_path / "browser", tmp_path, tmp_path / "state")
    assert "127.0.0.1:6089" in plan["viewer"] and "127.0.0.1:5909" in plan["viewer"]
    assert "-localhost" in plan["vnc"] and "-auth" in plan["display"]
    assert "-no6" in plan["vnc"] and "-noipv6" in plan["vnc"]
    # The packaged library may still create ::1 despite those switches. Keep
    # its explicit port aligned rather than letting it open default port 5900.
    assert plan["vnc"][plan["vnc"].index("-rfbportv6") + 1] == "5909"
    assert "--no-sandbox" not in plan["browser"]
    assert "--password-store=gnome-libsecret" in plan["browser"]
    assert "--password-store=basic" not in plan["browser"]


def test_observer_install_is_separate_private_and_never_overwrites_desktop(tmp_path):
    state, units = tmp_path / "state", tmp_path / "units"
    desktop["install"](_browser(tmp_path), tmp_path, state, units)
    original = (units / desktop["UNIT"]).read_bytes()
    result = desktop["install_observer"](tmp_path, state, units)
    assert not result["started"]
    observer = units / desktop["OBSERVER_UNIT"]
    assert stat.S_IMODE(observer.stat().st_mode) == 0o600
    unit = observer.read_text()
    assert "BindsTo=vortocode-computer.service" in unit
    assert "PartOf=vortocode-computer.service" in unit
    assert '" observe ' in unit
    assert (units / desktop["UNIT"]).read_bytes() == original
    with pytest.raises(ValueError, match="already exists"):
        desktop["install_observer"](tmp_path, state, units)
    plan = desktop["observation_plan"](tmp_path, state, "fixture-session")
    vnc = plan["observation_vnc"]
    assert "-viewonly" in vnc and vnc[vnc.index("-input") + 1] == "," and "-nosel" in vnc
    assert vnc[vnc.index("-rfbport") + 1] == "5908"
    assert vnc[vnc.index("-rfbportv6") + 1] == "5908"
    assert "127.0.0.1:6088" in plan["observation_viewer"]
    assert "--observer-event connect" in vnc[vnc.index("-accept") + 1]


def test_viewer_assets_are_private_and_mode_targets_cannot_select_arbitrary_hosts(tmp_path):
    desktop["prepare_observer_assets"](tmp_path)
    web = tmp_path / "observer-web"
    assert stat.S_IMODE(web.stat().st_mode) == 0o700
    assert (web / "novnc").readlink() == Path("/usr/share/novnc")
    assert (tmp_path / "observer-targets").read_text().splitlines() == [
        "observe: 127.0.0.1:5908", "control: 127.0.0.1:5909"]
    assert stat.S_IMODE((web / "index.html").stat().st_mode) == 0o600
    assert not (web / "observer-targets").exists()
    (web / "novnc").unlink()
    (web / "novnc").symlink_to("/tmp")
    with pytest.raises(ValueError, match="unexpected"):
        desktop["prepare_observer_assets"](tmp_path)


def test_private_write_refuses_symlink_and_preserves_existing_temp_file(tmp_path):
    original = tmp_path / "original"
    original.write_text("keep")
    link = tmp_path / "link"
    link.symlink_to(original)
    with pytest.raises(ValueError):
        desktop["write_private"](link, b"replace")
    assert original.read_text() == "keep"
    target = tmp_path / "unit"
    temporary = tmp_path / "unit.tmp"
    temporary.write_text("keep pending")
    with pytest.raises(FileExistsError):
        desktop["write_private"](target, b"replace")
    assert temporary.read_text() == "keep pending" and not target.exists()


def test_port_preflight_rejects_listener_but_allows_restart_after_connection_close():
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        with pytest.raises(OSError):
            desktop["check_ports"]([port])
        with socket.create_connection(("127.0.0.1", port)) as client:
            connection, _ = server.accept()
            connection.close()  # Server closes first, leaving TIME_WAIT on its port.
            assert client.recv(1) == b""
    desktop["check_ports"]([port])


def test_desktop_environment_uses_persistent_bus_and_never_inherits_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/untrusted/bus")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/untrusted/runtime")
    env = desktop["desktop_environment"](tmp_path)
    assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path=/run/user/{desktop['os'].getuid()}/bus"
    assert env["XAUTHORITY"] == str(tmp_path / ".Xauthority")
    assert "OPENAI_API_KEY" not in env and "synthetic-secret" not in env.values()


@pytest.mark.parametrize("owner,alias,locked,expected", [
    (False, "/", True, "unavailable"),
    (True, "/", True, "needs_setup"),
    (True, "/org/freedesktop/secrets/collection/login", True, "locked"),
    (True, "/org/freedesktop/secrets/collection/login", False, "unlocked"),
    (True, "/untrusted/path", False, "unavailable"),
])
def test_password_status_reads_only_collection_metadata(tmp_path, monkeypatch, owner, alias, locked, expected):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["capture_output"] and kwargs["timeout"] <= 3
        if "NameHasOwner" in argv:
            result = {"type": "b", "data": [owner]}
        elif "ReadAlias" in argv:
            result = {"type": "o", "data": [alias]}
        elif "Locked" in argv:
            result = {"type": "b", "data": locked}
        else:
            raise AssertionError("unexpected secret API")
        return subprocess.CompletedProcess(argv, 0, json.dumps(result).encode())
    monkeypatch.setattr(desktop["subprocess"], "run", run)
    result = desktop["password_store_status"](desktop["desktop_environment"](tmp_path))
    assert result == {"backend": "gnome-libsecret", "state": expected}
    assert not any("GetSecrets" in argv or "GetSecret" in argv or "SearchItems" in argv for argv in calls)


def test_password_status_failure_is_unavailable_without_raw_error_or_secret(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["busctl"], stderr=b"synthetic-secret")
    monkeypatch.setattr(desktop["subprocess"], "run", fail)
    status = desktop["password_store_status"](desktop["desktop_environment"](tmp_path))
    assert status["state"] == "unavailable" and "synthetic-secret" not in json.dumps(status)


def test_password_service_is_started_outside_desktop_and_not_unlocked(tmp_path, monkeypatch):
    calls = []
    owners = iter([False, True])
    def run(argv, **kwargs):
        calls.append(argv)
        if "NameHasOwner" in argv:
            data = {"data": [next(owners)]}
        elif "ReadAlias" in argv:
            data = {"data": ["/"]}
        else:
            data = {}
        return subprocess.CompletedProcess(argv, 0, json.dumps(data).encode())
    monkeypatch.setattr(desktop["subprocess"], "run", run)
    desktop["prepare_password_store"](desktop["desktop_environment"](tmp_path))
    assert ["systemctl", "--user", "start", "gnome-keyring-daemon.service"] in calls
    assert calls[0] == ["dbus-update-activation-environment", "--systemd", "DISPLAY", "XAUTHORITY"]
    assert not any("--unlock" in argv or "GetSecret" in argv for argv in calls)


def test_unavailable_password_service_prevents_browser_preparation(tmp_path, monkeypatch):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, json.dumps({"data": [False]}).encode())
    monkeypatch.setattr(desktop["subprocess"], "run", run)
    with pytest.raises(RuntimeError, match="refusing to start browser"):
        desktop["prepare_password_store"](desktop["desktop_environment"](tmp_path))
