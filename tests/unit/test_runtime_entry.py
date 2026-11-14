"""Internal startup uses shared services without importing the public CLI or calling a model."""

import asyncio
import subprocess
import sys
from types import SimpleNamespace

import pytest

from src import runtime_entry


@pytest.mark.parametrize(
    "argv", [["agent"], ["attach"], ["workers"], ["server", "--im", "unknown"]]
)
def test_internal_entry_rejects_user_commands_before_preparation(monkeypatch, argv):
    monkeypatch.setattr(
        runtime_entry, "prepare_runtime", lambda: pytest.fail("prepared invalid input")
    )
    with pytest.raises(SystemExit) as error:
        runtime_entry.main(argv)
    assert error.value.code == 2


def test_internal_server_prepares_before_starting_existing_server(monkeypatch):
    events = []
    monkeypatch.setattr(runtime_entry, "prepare_runtime", lambda: events.append("prepare"))
    monkeypatch.setattr(
        runtime_entry, "run_server", lambda *args, **kwargs: events.append((args, kwargs))
    )
    assert (
        runtime_entry.main(["server", "--host", "127.0.0.1", "--port", "8123", "--im", "telegram"])
        == 0
    )
    assert events == ["prepare", (("127.0.0.1", 8123), {"im": "telegram"})]


def test_internal_help_does_not_prepare_state(monkeypatch):
    monkeypatch.setattr(runtime_entry, "prepare_runtime", lambda: pytest.fail("prepared help"))
    with pytest.raises(SystemExit) as error:
        runtime_entry.main(["--help"])
    assert error.value.code == 0


def test_failed_preparation_does_not_start_service(monkeypatch):
    def failed():
        raise OSError("state unavailable")

    monkeypatch.setattr(runtime_entry, "prepare_runtime", failed)
    monkeypatch.setattr(
        runtime_entry, "run_server", lambda *a, **kw: pytest.fail("started service")
    )
    with pytest.raises(OSError, match="state unavailable"):
        runtime_entry.main(["server"])


def test_internal_im_preserves_explicit_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime_entry, "prepare_runtime", lambda: calls.append("prepare"))

    async def run(channel, *, mode):
        calls.append((channel, mode))

    monkeypatch.setattr(runtime_entry, "run_im", run)
    assert runtime_entry.main(["im", "telegram", "--mode", "build"]) == 0
    assert calls == ["prepare", ("telegram", "build")]


@pytest.mark.parametrize("command", ["doctor", "canary"])
def test_internal_diagnostics_propagate_failure_exit_code(monkeypatch, command):
    monkeypatch.setattr(runtime_entry, "prepare_runtime", lambda: None)

    async def failed():
        return 1

    name = "run_doctor" if command == "doctor" else "run_canary_cli"
    monkeypatch.setattr(runtime_entry, name, failed)
    assert runtime_entry.main([command]) == 1


def test_public_server_wrapper_and_internal_entry_use_the_same_service(monkeypatch):
    from src import cli
    from src.web import server

    calls = []
    monkeypatch.setattr(server, "start_server", lambda *a, **kw: calls.append((a, kw)))
    runtime_entry.run_server("localhost", 8123, im="telegram")
    cli.run_server("localhost", 8123, im="telegram")
    assert calls == [(("localhost", 8123), {"im": "telegram"})] * 2


def test_internal_import_does_not_load_public_cli():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import src.runtime_entry; assert 'src.cli' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_shared_preparation_configures_logging_before_state_migration(monkeypatch, tmp_path):
    from src import runtime_bootstrap
    from src.utils import state_dir

    events = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runtime_bootstrap, "env_compat", lambda *args: None)
    monkeypatch.setattr(state_dir, "state_dir", lambda root: events.append(("cwd", root)))
    monkeypatch.setattr(state_dir, "home_state_dir", lambda: events.append("home"))
    runtime_bootstrap.prepare_runtime(log_setup=lambda: events.append("logging"))
    assert events == ["logging", ("cwd", tmp_path), "home"]


def test_shared_preparation_keeps_legacy_work_and_home_state(monkeypatch, tmp_path):
    from src import runtime_bootstrap

    work, home = tmp_path / "work", tmp_path / "home"
    for root in [work, home]:
        (root / ".vortocode").mkdir(parents=True)
        (root / ".vortocode" / "fixture.txt").write_text(root.name)
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VORTOONE_STATE_DIR_MIGRATION", "on")
    monkeypatch.setattr(runtime_bootstrap, "env_compat", lambda *args: None)
    runtime_bootstrap.prepare_runtime(log_setup=lambda: None)
    for root in [work, home]:
        assert (root / ".vortoone" / "fixture.txt").read_text() == root.name
        assert (root / ".vortocode").is_symlink()
        assert (root / ".vortocode" / "fixture.txt").read_text() == root.name


def test_standalone_im_still_refuses_missing_owner_configuration(monkeypatch):
    from src.gateway import im_service

    monkeypatch.setattr(
        im_service,
        "build_adapter",
        lambda channel: (_ for _ in ()).throw(im_service.IMConfigError("missing owner")),
    )
    with pytest.raises(SystemExit) as error:
        asyncio.run(runtime_entry.run_im("telegram"))
    assert error.value.code == 2


def test_standalone_im_keeps_lease_and_cleanup_order(monkeypatch, tmp_path):
    from src.gateway import im_service, process_lease
    from src.im import bridge

    monkeypatch.chdir(tmp_path)
    events = []

    async def close():
        events.append("adapter-close")

    async def shutdown():
        events.append("runner-shutdown")

    class Bridge:
        allow_from = {"fixture-owner"}
        _runner = SimpleNamespace(shutdown=shutdown)

        def __init__(self, *a, **kw):
            pass

        def allow_from_warning(self):
            return ""

        async def run(self):
            events.append("run")
            raise RuntimeError("fixture failure")

    class Lease:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            events.append("claim")

        def __exit__(self, *args):
            events.append("release")

    monkeypatch.setattr(
        im_service, "build_adapter", lambda channel: (SimpleNamespace(close=close), "fixture-owner")
    )
    monkeypatch.setattr(im_service, "load_conversation", lambda: "primary")
    monkeypatch.setattr(im_service, "load_session_role", lambda: "general")
    monkeypatch.setattr(im_service, "load_allow_from", lambda channel: {"fixture-owner"})
    monkeypatch.setattr(bridge, "IMBridge", Bridge)
    monkeypatch.setattr(process_lease, "ProcessLease", Lease)
    with pytest.raises(RuntimeError, match="fixture failure"):
        asyncio.run(runtime_entry.run_im("telegram"))
    assert events == ["claim", "run", "runner-shutdown", "release", "adapter-close"]
