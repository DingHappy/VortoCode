"""Runtime-host-only metadata, no SSH, screens, permissions or model access."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from src.computer import status


def wire(**changes):
    return {"version": 1, "ready": True, "mode": "human", "viewers": 0, "heartbeat_age_seconds": .1,
            "password_store": {"backend": "gnome-libsecret", "state": "unlocked"}, **changes}


def helper(tmp_path, monkeypatch, code):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".local/share/vortocode/computer"
    root.mkdir(parents=True, mode=0o700)
    script = root / "resident-desktop.py"
    script.write_text(code)
    script.chmod(0o600)
    return script


def test_installed_helper_is_local_and_does_not_inherit_credentials_or_target(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_DESKTOP_TARGET", "ssh:other-machine")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-credential")
    observed = tmp_path / "observed.json"
    code = ("import json,os,sys\n"
            f"open({str(observed)!r},'w').write(json.dumps({{'env':dict(os.environ),'args':sys.argv[1:]}}))\n"
            f"print({json.dumps(json.dumps(wire()))})\n")
    helper(tmp_path, monkeypatch, code)
    result = status.read_local_computer_status()
    assert result["state"] == "ready" and result["execution_location"] == "runtime_host"
    assert result["password_store"] == "unlocked"
    assert result["resident_worker"] == {"connected": False, "unattended": False}
    seen = json.loads(observed.read_text())
    assert seen["args"] == ["status"]
    assert "OPENAI_API_KEY" not in seen["env"] and "VORTOCODE_DESKTOP_TARGET" not in seen["env"]


@pytest.mark.parametrize("changes", [
    {"version": True}, {"version": 2}, {"ready": 1}, {"heartbeat_age_seconds": float("nan")},
    {"heartbeat_age_seconds": -1}, {"viewers": True}, {"viewers": 100},
])
def test_malformed_metadata_is_rejected(changes):
    with pytest.raises(ValueError):
        status._parse(json.dumps(wire(**changes)).encode())


def test_stale_service_cannot_claim_ready_and_arbitrary_secret_fields_are_dropped():
    result = status._parse(json.dumps(wire(heartbeat_age_seconds=9, lease="synthetic-secret",
                                          images=["private.png"], mode="future")).encode())
    assert result["state"] == "not_ready" and result["control_mode"] == "unknown"
    assert "synthetic-secret" not in json.dumps(result) and "private.png" not in json.dumps(result)
    assert status._parse(json.dumps(wire(password_store={"backend": "future", "state": "unlocked"})).encode())["password_store"] == "unknown"


def test_observation_metadata_is_separate_from_controlling_connections():
    result = status._parse(json.dumps(wire(viewers=1, observation={"available": True, "viewers": 3})).encode())
    assert result["viewers"] == 1 and result["observation"] == {"available": True, "viewers": 3}
    assert status._parse(json.dumps(wire()).encode())["observation"] is None
    assert status._parse(json.dumps(wire(observation={"available": True, "viewers": -1})).encode())["observation"] is None


def test_missing_helper_and_insecure_or_symlink_helper_never_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert status.read_local_computer_status()["state"] == "not_installed"
    script = helper(tmp_path, monkeypatch, "raise RuntimeError('must not run')")
    script.chmod(0o644)
    assert status.read_local_computer_status()["state"] == "unavailable"
    script.unlink()
    script.symlink_to(tmp_path / "outside")
    assert status.read_local_computer_status()["state"] == "unavailable"


def test_helper_failure_is_metadata_failure_not_false_readiness(tmp_path, monkeypatch):
    helper(tmp_path, monkeypatch, "raise RuntimeError('synthetic-secret')")
    result = status.read_local_computer_status()
    assert result["state"] == "unavailable" and "synthetic-secret" not in json.dumps(result)


def test_timeout_kills_only_the_owned_process_group(tmp_path, monkeypatch):
    helper(tmp_path, monkeypatch, "")
    killed = []
    class Child:
        pid = 424242
        returncode = None
        def communicate(self, **kwargs):
            assert kwargs["timeout"] == 8
            raise subprocess.TimeoutExpired("fixed helper", 8)
        def poll(self):
            return None
        def wait(self):
            self.returncode = -9
    monkeypatch.setattr(status.subprocess, "Popen", lambda *args, **kwargs: Child())
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append(pid))
    assert status.read_local_computer_status()["state"] == "unavailable"
    assert killed == [424242]


async def test_existing_resident_route_retains_projects_and_adds_only_metadata(tmp_path, monkeypatch):
    from src.web.routers import resident
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(resident, "load_resident_projects", lambda: [{"name": "demo", "port": 8082, "repo_root": "/srv/demo"}])
    result = await resident.resident_projects()
    assert result["projects"][0]["name"] == "demo"
    assert result["computer"]["state"] == "not_installed"
    assert result["computer"]["resident_worker"]["unattended"] is False
