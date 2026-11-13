"""Resident launch configuration is private data, never a second authority system."""

import json
import os
from pathlib import Path

import pytest

from desktop.runtime_entry import build_parser


def private_config(tmp_path, **changes):
    tmp_path.chmod(0o700)
    workspace = tmp_path / "assistant"
    workspace.mkdir(exist_ok=True, mode=0o700)
    value = {
        "schema_version": 1,
        "revision": "fixture-configuration",
        "runtime_version": "fixture-version",
        "workspace_root": str(workspace),
        "port": 18765,
        "cron_enabled": False,
        "model_revision": "fixture-model",
        "token": "fixture-private-token-that-is-long-enough",
        "model_env": {
            "OPENAI_API_BASE": "https://models.example.test/v1",
            "OPENAI_API_KEY": "fixture-model-key",
            "DEFAULT_MODEL": "fixture-model",
            "LLM_MODEL_CHEAP": None,
        },
    }
    value.update(changes)
    path = tmp_path / "service-config.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


def test_resident_entry_accepts_only_a_managed_configuration_not_inline_credentials(tmp_path):
    args = build_parser().parse_args(["resident-server", "--config", str(tmp_path / "config.json")])
    assert args.command == "resident-server"
    for extra in (["--host", "0.0.0.0"], ["--token", "fixture"], ["--scope", "project"]):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["resident-server", "--config", "/fixture", *extra])


def test_resident_launch_uses_snapshot_and_cannot_inherit_desktop_authority(tmp_path):
    from desktop.local_resident_config import prepare_resident

    path = private_config(tmp_path)
    env = {
        "PATH": "/fixture/bin",
        "OPENAI_API_KEY": "old-key",
        "LLM_MODEL_POWERFUL": "old-model",
        "VORTOCODE_DESKTOP_SIDECAR": "1",
        "VORTOCODE_CRON": "1",
        "AUTODEV_API_TOKEN": "old-token",
        "VORTOONE_SUPERVISOR_PID": "123",
        "VORTOONE_ENABLE_SHELL": "1",
        "VORTOONE_IM_BINDINGS_FILE": "/fixture/bindings",
        "VORTOONE_PROVIDER_OLD_KEY": "old-key",
    }
    root, port = prepare_resident(path, environ=env)
    assert (root, port) == (tmp_path / "assistant", 18765)
    assert env["OPENAI_API_KEY"] == "fixture-model-key"
    assert env["VORTOONE_API_TOKEN"] == "fixture-private-token-that-is-long-enough"
    assert env["DEFAULT_MODEL"] == "fixture-model"
    assert env["VORTOONE_WORKSPACE_SCOPE"] == "general"
    assert env["VORTOONE_CRON"] == "0"
    assert env["VORTOONE_HEARTBEAT"] == "0"
    assert env["VORTOONE_IM"] == env["VORTOONE_IM_BINDINGS_FILE"] == ""
    assert env["VORTOONE_DESKTOP_SIDECAR"] == "0"
    assert env["VORTOONE_ENABLE_SHELL"] == "0"
    assert env["VORTOONE_ENABLE_BROWSER"] == env["VORTOONE_ENABLE_DESKTOP_CONTROL"] == "0"
    assert env["VORTOONE_SUPERVISOR_PID"] == ""
    assert env["PYTHON_DOTENV_DISABLED"] == "1"
    assert env["PATH"] == "/fixture/bin"
    assert not any(k.startswith(("VORTOCODE_", "AUTODEV_")) for k in env)
    assert "VORTOONE_PROVIDER_OLD_KEY" not in env
    assert "LLM_MODEL_POWERFUL" not in env


def test_each_resident_process_gets_a_new_launch_identity_and_cron_is_explicit(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    import desktop.local_resident_config as loader
    from desktop.local_resident_config import prepare_resident

    identities = iter(["first-launch", "second-launch"])
    monkeypatch.setattr(loader.uuid, "uuid4", lambda: SimpleNamespace(hex=next(identities)))
    path = private_config(tmp_path, cron_enabled=True)
    first, second = {}, {}
    prepare_resident(path, environ=first)
    prepare_resident(path, environ=second)
    assert first["VORTOONE_CONFIGURATION_LAUNCH_ID"] != second["VORTOONE_CONFIGURATION_LAUNCH_ID"]
    assert first["VORTOONE_MODEL_CONFIGURATION_REVISION"] == "fixture-model"
    assert first["VORTOONE_CRON"] == "1"
    assert first["VORTOONE_HEARTBEAT"] == "0"


def test_package_dotenv_cannot_restore_a_legacy_switch_or_provider(tmp_path, monkeypatch):
    from desktop.local_resident_config import prepare_resident
    from src.env_compat import load_env_file

    path = private_config(tmp_path)
    dotenv = tmp_path / ".env"
    dotenv.write_text("VORTOCODE_ENABLE_SHELL=1\nVORTOCODE_PROVIDER_OLD_KEY=fixture-old\n")
    original = dict(os.environ)
    try:
        prepare_resident(path)
        load_env_file(dotenv)
        assert os.environ["VORTOONE_ENABLE_SHELL"] == "0"
        assert "VORTOONE_PROVIDER_OLD_KEY" not in os.environ
        assert "VORTOCODE_ENABLE_SHELL" not in os.environ
    finally:
        os.environ.clear()
        os.environ.update(original)


def test_generation_update_retains_one_workspace_and_checks_revision(tmp_path):
    from desktop.local_resident_config import prepare_resident

    path = private_config(tmp_path)
    generation = tmp_path / "generations" / "fixture-configuration"
    generation.mkdir(parents=True, mode=0o700)
    moved = generation / path.name
    path.rename(moved)
    assert prepare_resident(moved, environ={})[0] == tmp_path / "assistant"
    generation.rename(generation.with_name("different-revision"))
    with pytest.raises(ValueError):
        prepare_resident(generation.with_name("different-revision") / path.name, environ={})


def test_resident_capability_probe_reports_only_protocol_without_starting_server(capsys):
    from desktop.runtime_entry import main

    assert main(["resident-capabilities"]) == 0
    assert json.loads(capsys.readouterr().out) == {"schema_version": 1, "configuration_schema": 1}


def test_config_read_is_bounded_and_workspace_cannot_be_a_link(tmp_path):
    from desktop.local_resident_config import prepare_resident

    path = private_config(tmp_path)
    path.write_text(" " * (64 * 1024 + 1))
    with pytest.raises(ValueError):
        prepare_resident(path, environ={})
    path = private_config(tmp_path)
    workspace = tmp_path / "assistant"
    workspace.rmdir()
    outside = tmp_path / "other"
    outside.mkdir()
    workspace.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        prepare_resident(path, environ={})


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"port": True},
        {"port": 80},
        {"cron_enabled": "yes"},
        {"token": "short"},
        {"revision": "invalid revision"},
        {"model_revision": "invalid revision"},
        {"workspace_root": "/unmanaged"},
        {"model_env": {"VORTOONE_ENABLE_SHELL": "1"}},
        {"model_env": {"PATH": "/injected"}},
        {"model_env": {"OPENAI_API_KEY": ["invalid"]}},
        {"model_env": {"VORTOONE_PROVIDER_X_KEY": "bad\u0000value"}},
    ],
)
def test_invalid_config_fails_before_changing_environment(tmp_path, changes):
    from desktop.local_resident_config import prepare_resident

    path = private_config(tmp_path, **changes)
    env = {"DEFAULT_MODEL": "unchanged"}
    with pytest.raises(ValueError):
        prepare_resident(path, environ=env)
    assert env == {"DEFAULT_MODEL": "unchanged"}


def test_config_rejects_symlinks_and_non_private_files_and_parent(tmp_path):
    from desktop.local_resident_config import prepare_resident

    path = private_config(tmp_path)
    link = tmp_path / "linked.json"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        prepare_resident(link, environ={})
    path.chmod(0o644)
    with pytest.raises(ValueError):
        prepare_resident(path, environ={})
    path.chmod(0o600)
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError):
        prepare_resident(path, environ={})


def test_invalid_config_errors_never_echo_credentials(tmp_path):
    from desktop.local_resident_config import prepare_resident

    path = private_config(tmp_path, token="SENSITIVE-BAD-TOKEN")
    with pytest.raises(ValueError) as failure:
        prepare_resident(path, environ={})
    assert "SENSITIVE-BAD-TOKEN" not in str(failure.value)


def test_resident_main_calls_original_server_after_environment_and_workdir_are_ready(
    tmp_path, monkeypatch
):
    from desktop.runtime_entry import main
    import desktop.local_resident_config as loader
    import src.web.server as server

    observed = []
    path = private_config(tmp_path)
    monkeypatch.setattr(
        loader, "prepare_resident_socket", lambda root: root.parent / "service.sock"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        server,
        "start_server",
        lambda host, port, **options: observed.append(
            (
                host,
                port,
                Path.cwd(),
                os.environ["VORTOONE_WORKSPACE_SCOPE"],
                os.environ["VORTOONE_API_TOKEN"],
                options,
            )
        ),
    )
    # Restore all process environment changes at teardown, including variables removed by the launcher.
    original = dict(os.environ)
    try:
        assert main(["resident-server", "--config", str(path)]) == 0
        assert observed == [
            (
                "127.0.0.1",
                18765,
                tmp_path / "assistant",
                "general",
                "fixture-private-token-that-is-long-enough",
                {"uds": str(tmp_path / "service.sock")},
            )
        ]
    finally:
        os.environ.clear()
        os.environ.update(original)


def test_resident_socket_is_private_and_existing_active_socket_is_not_unlinked():
    import socket
    import stat
    import tempfile
    from desktop.local_resident_config import prepare_resident_socket, resident_socket_path

    with tempfile.TemporaryDirectory(
        prefix="vr-", dir=str(Path(tempfile.gettempdir()).resolve())
    ) as directory:
        root = Path(directory)
        root.chmod(0o700)
        workspace = root / "assistant"
        workspace.mkdir(mode=0o700)
        path = prepare_resident_socket(workspace)
        try:
            assert path == resident_socket_path(root)
            assert stat.S_ISSOCK(path.stat().st_mode)
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            path.unlink()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as active:
                active.bind(str(path))
                path.chmod(0o600)
                active.listen(1)
                inode = path.stat().st_ino
                assert prepare_resident_socket(workspace) == path
                assert path.stat().st_ino == inode
        finally:
            path.unlink(missing_ok=True)
            path.parent.rmdir()


def test_resident_socket_cannot_replace_a_file_or_link(tmp_path):
    from desktop.local_resident_config import prepare_resident_socket, resident_socket_path

    tmp_path.chmod(0o700)
    workspace = tmp_path / "assistant"
    workspace.mkdir(mode=0o700)
    path = resident_socket_path(tmp_path)
    path.parent.mkdir(mode=0o700)
    try:
        path.write_text("owner data")
        with pytest.raises(ValueError):
            prepare_resident_socket(workspace)
        assert path.read_text() == "owner data"
    finally:
        path.unlink()
        path.parent.rmdir()


def test_socket_identity_matches_native_protocol_and_separates_instances():
    from desktop.local_resident_config import _socket_directory_name

    assert (
        _socket_directory_name(Path("/fixture/application-data/local-resident"), 501)
        == "vortoone-resident-501-e8689798d38e6f28"
    )
    assert _socket_directory_name(Path("/fixture/a"), 501) != _socket_directory_name(
        Path("/fixture/b"), 501
    )
    assert _socket_directory_name(Path("/fixture/a"), 501) != _socket_directory_name(
        Path("/fixture/a"), 502
    )


def test_socket_never_reuses_an_insecure_or_linked_endpoint_directory(tmp_path):
    import stat
    from desktop.local_resident_config import prepare_resident_socket, resident_socket_path

    tmp_path.chmod(0o700)
    workspace = tmp_path / "assistant"
    workspace.mkdir(mode=0o700)
    endpoint = resident_socket_path(tmp_path)
    endpoint.parent.mkdir(mode=0o700)
    endpoint.parent.chmod(0o755)
    try:
        with pytest.raises(ValueError):
            prepare_resident_socket(workspace)
        assert stat.S_IMODE(endpoint.parent.stat().st_mode) == 0o755
        endpoint.parent.rmdir()
        endpoint.parent.symlink_to(workspace, target_is_directory=True)
        with pytest.raises(ValueError):
            prepare_resident_socket(workspace)
        assert endpoint.parent.is_symlink()
    finally:
        if endpoint.parent.is_symlink():
            endpoint.parent.unlink()
        else:
            endpoint.parent.rmdir()


def test_long_state_directory_still_gets_a_private_usable_socket(tmp_path):
    import stat
    from desktop.local_resident_config import prepare_resident_socket

    root = tmp_path / ("long-user-data-" * 8)
    root.mkdir(mode=0o700)
    workspace = root / "assistant"
    workspace.mkdir(mode=0o700)
    assert len(os.fsencode(root / "service.sock")) > 103
    endpoint = None
    try:
        endpoint = prepare_resident_socket(workspace)
        assert len(os.fsencode(endpoint)) <= 103
        assert stat.S_ISSOCK(endpoint.stat().st_mode)
        assert stat.S_IMODE(endpoint.stat().st_mode) == 0o600
        assert stat.S_IMODE(endpoint.parent.stat().st_mode) == 0o700
    finally:
        if endpoint is not None:
            endpoint.unlink()
            endpoint.parent.rmdir()


def test_original_server_uses_unix_transport_without_a_network_listener(monkeypatch):
    import uvicorn
    from src.web.server import start_server

    observed = []
    monkeypatch.setenv("VORTOONE_IM", "")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: observed.append(kwargs))
    start_server(uds="/fixture/private/service.sock")
    assert observed == [
        {"uds": "/fixture/private/service.sock", "log_level": "warning", "access_log": False}
    ]
    observed.clear()
    start_server(host="localhost", port=18765)
    assert observed == [
        {"host": "localhost", "port": 18765, "log_level": "warning", "access_log": False}
    ]
