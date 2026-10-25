"""Observe this runtime's desktop locally; never fetch screens on the controller.

This is readiness metadata, not a task runner, authorization or completion proof.
The resident worker has not been connected, so its execution flags stay false.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess


def _base(state: str) -> dict:
    return {"schema_version": 1, "execution_location": "runtime_host", "state": state,
            "control_mode": "unknown", "viewers": None, "password_store": "unknown", "observation": None,
            "resident_worker": {"connected": False, "unattended": False}}


def _parse(raw: bytes) -> dict:
    if len(raw) > 16384:
        raise ValueError("oversized status")
    data = json.loads(raw)
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("unsupported protocol")
    ready, age, viewers = data.get("ready"), data.get("heartbeat_age_seconds"), data.get("viewers")
    if (type(ready) is not bool or type(age) not in (int, float) or not math.isfinite(age) or age < 0
            or type(viewers) is not int or not 0 <= viewers <= 32):
        raise ValueError("invalid status")
    result = _base("ready" if ready and age <= 8 else "not_ready")
    mode = data.get("mode")
    result["control_mode"] = mode if mode in ("human", "agent") else "unknown"
    result["viewers"] = viewers
    store = data.get("password_store")
    if isinstance(store, dict) and store.get("backend") == "gnome-libsecret":
        state = store.get("state")
        if state in ("needs_setup", "locked", "unlocked", "unavailable"):
            result["password_store"] = state
    observer = data.get("observation")
    if isinstance(observer, dict):
        available, count = observer.get("available"), observer.get("viewers")
        if type(available) is bool and type(count) is int and 0 <= count <= 32:
            result["observation"] = {"available": available, "viewers": count}
    # Deliberately omit epochs, leases, arbitrary remote keys and all media.
    return result


def read_local_computer_status() -> dict:
    home = Path.home()
    state = home / ".local/share/vortocode/computer"
    helper = state / "resident-desktop.py"
    try:
        directory, info = state.lstat(), helper.lstat()
    except FileNotFoundError:
        return _base("not_installed")
    except OSError:
        return _base("unavailable")
    if (not stat.S_ISDIR(directory.st_mode) or not stat.S_ISREG(info.st_mode)
            or directory.st_uid != os.getuid() or info.st_uid != os.getuid()
            or directory.st_mode & 0o077 or info.st_mode & 0o077):
        return _base("unavailable")
    python = shutil.which("python3", path="/usr/local/bin:/usr/bin:/bin")
    if not python:
        return _base("unavailable")
    # Do not follow VORTOCODE_DESKTOP_TARGET or workspace settings: this status
    # belongs to the runtime serving the request, never a different computer.
    env = {"HOME": str(home), "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
    child = None
    try:
        child = subprocess.Popen([python, str(helper), "status"], env=env, cwd=state,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        raw, _ = child.communicate(timeout=8)
        if child.returncode:
            return _base("unavailable")
        return _parse(raw)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return _base("unavailable")
    finally:
        if child is not None and child.poll() is None:
            # Only our still-running process group. No global process cleanup.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
