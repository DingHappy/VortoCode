"""Certificate automation boundaries: credentials, CA isolation and rotation rollback."""

import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("fcntl")

spec = importlib.util.spec_from_file_location(
    "mobile_certificate", Path(__file__).parents[2] / "scripts/manage-mobile-certificate.py"
)
cert = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cert)


def document(host="assistant.example.com"):
    return {"dnsconf": [{"dns": {"name": "alidns", "id": "test-key-id", "secret": "test-secret"},
                         "ipv4": {"domains": [host]}}]}


@pytest.mark.parametrize("value", ["https://a.example.com", "a.example.com:18443", "*.example.com",
                                  "a..example.com", "127.0.0.1", "a.example.com/path", "A.example.com"])
def test_hostname_cannot_inject_options_or_origin_parts(value):
    with pytest.raises(cert.Problem, match="invalid_hostname"):
        cert.hostname(value)


def test_credential_selection_requires_exact_single_host():
    assert cert.credentials(document(), "assistant.example.com") == ("test-key-id", "test-secret")
    with pytest.raises(cert.Problem, match="ambiguous"):
        cert.credentials(document(), "other.example.com")
    value = document()
    value["dnsconf"] *= 2
    with pytest.raises(cert.Problem, match="ambiguous"):
        cert.credentials(value, "assistant.example.com")


def test_credential_selection_rejects_newline():
    value = document()
    value["dnsconf"][0]["dns"]["secret"] = "secret\nexport BAD=1"
    with pytest.raises(cert.Problem, match="invalid"):
        cert.credentials(value, "assistant.example.com")


def test_private_source_rejects_shared_files_and_symlinks(tmp_path):
    source = tmp_path / "source"
    source.write_text("private")
    source.chmod(0o644)
    with pytest.raises(cert.Problem, match="permissions"):
        cert.private_file(source)
    source.chmod(0o600)
    assert cert.private_file(source) == source
    alias = tmp_path / "alias"
    alias.symlink_to(source)
    with pytest.raises(cert.Problem, match="permissions"):
        cert.private_file(alias)


def test_acme_failure_scrubs_cached_keys_without_echoing_output(tmp_path, monkeypatch):
    acme = tmp_path / "acme.sh"
    acme.write_text("test")
    acme.chmod(0o600)
    config = tmp_path / "config"
    data = tmp_path / "data"
    ctx = SimpleNamespace(acme=acme, home=tmp_path, config=tmp_path / "settings", host="assistant.example.com",
                          read_credentials=lambda: ("test-key-id", "test-secret"),
                          acme_paths=lambda staging: (config, data))

    def fake_run(args, **kwargs):
        assert "test-secret" not in args
        assert "test-key-id" not in args
        assert kwargs["env"]["Ali_Secret"] == "test-secret"
        (config / "account.conf").write_text("SAVED_Ali_Key='test-key-id'\nSAVED_Ali_Secret='test-secret'\nOTHER='keep'\n")
        (config / "account.conf").chmod(0o600)
        raise cert.Problem("external_command_failed")

    monkeypatch.setattr(cert, "run", fake_run)
    with pytest.raises(cert.Problem, match="external_command_failed"):
        cert.issue(ctx, staging=True)
    assert (config / "account.conf").read_text() == "OTHER='keep'\n"


def test_staging_never_installs_or_restarts_production(tmp_path, monkeypatch):
    acme = tmp_path / "acme.sh"
    acme.write_text("test")
    acme.chmod(0o600)
    ctx = SimpleNamespace(acme=acme, home=tmp_path, config=tmp_path / "settings", host="assistant.example.com",
                          read_credentials=lambda: ("test-key-id", "test-secret"),
                          acme_paths=lambda staging: (tmp_path / "stage-config", tmp_path / "stage-data"))
    calls = []
    monkeypatch.setattr(cert, "run", lambda args, **kwargs: calls.append(args) or 0)
    monkeypatch.setattr(cert, "validate_certificate", lambda *args: (None, None))
    monkeypatch.setattr(cert, "activate", lambda *args: pytest.fail("must not activate staging"))
    result = cert.issue(ctx, staging=True)
    assert cert.STAGING in calls[0] and cert.PRODUCTION not in calls[0]
    assert result["certificate_installed"] is False


def test_proxy_only_applies_to_certificate_subprocess(tmp_path, monkeypatch):
    acme = tmp_path / "acme.sh"
    acme.write_text("test")
    acme.chmod(0o600)
    ctx = SimpleNamespace(acme=acme, home=tmp_path, config=tmp_path / "settings", host="assistant.example.com",
                          proxy=cert.CERTIFICATE_PROXY,
                          read_credentials=lambda: ("test-key-id", "test-secret"),
                          acme_paths=lambda staging: (tmp_path / "stage-config", tmp_path / "stage-data"))
    seen = []
    previous_proxy = os.environ.get("https_proxy")
    monkeypatch.setattr(cert, "wait_certificate_proxy", lambda: None)
    def fake_run(args, **kwargs):
        seen.append(kwargs["env"])
        return 0
    monkeypatch.setattr(cert, "run", fake_run)
    monkeypatch.setattr(cert, "validate_certificate", lambda *args: (None, None))
    cert.issue(ctx, staging=True)
    assert seen[0]["https_proxy"] == cert.CERTIFICATE_PROXY
    assert os.environ.get("https_proxy") == previous_proxy


def test_proxy_startup_wait_is_bounded(monkeypatch):
    times = iter([0, 11])
    monkeypatch.setattr(cert.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(cert.socket, "create_connection", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    with pytest.raises(cert.Problem, match="certificate_proxy_unavailable"):
        cert.wait_certificate_proxy()


@pytest.mark.parametrize("proxy", ["http://example.com:8080", "socks5h://user:secret@127.0.0.1:17890",
                                   "socks5h://127.0.0.1:7890"])
def test_settings_reject_non_approved_proxy(tmp_path, proxy):
    config = tmp_path / ".config/vortocode-https"
    config.mkdir(parents=True)
    settings = config / "certificate.json"
    settings.write_text(json.dumps({"schema_version": 1, "hostname": "assistant.example.com",
                                   "credential_source": "/private/test.yaml", "certificate_proxy": proxy}))
    settings.chmod(0o600)
    with pytest.raises(cert.Problem, match="certificate_proxy_not_allowed"):
        cert.Context(tmp_path)


def test_forced_renewal_is_only_allowed_in_staging():
    with pytest.raises(cert.Problem, match="force_only_for_staging_renewal"):
        cert.issue(None, staging=False, renew=True, force=True)


def test_command_timeout_is_bounded_and_does_not_echo_output(capsys):
    with pytest.raises(cert.Problem, match="external_command_timeout"):
        cert.run([sys.executable, "-c", "import time; print('test-secret', flush=True); time.sleep(5)"], timeout=0.05)
    assert "test-secret" not in capsys.readouterr().out


def test_command_failure_does_not_echo_output(capsys):
    with pytest.raises(cert.Problem, match="external_command_failed"):
        cert.run([sys.executable, "-c", "import sys; print('test-secret'); sys.exit(1)"])
    assert "test-secret" not in capsys.readouterr().out


def pair(tmp_path, tag):
    fullchain, key = tmp_path / (tag + ".pem"), tmp_path / (tag + ".key")
    fullchain.write_text("test-cert-" + tag)
    key.write_text("test-key-" + tag)
    return fullchain, key


def test_rotation_stages_one_pair_and_does_not_start_inactive_service(tmp_path, monkeypatch):
    ctx = SimpleNamespace(data=tmp_path / "data", host="assistant.example.com")
    commands = []
    monkeypatch.setattr(cert.subprocess, "run", lambda args, **kwargs: commands.append(args) or SimpleNamespace(returncode=3))
    previous_mask = os.umask(0o077)
    try:
        fullchain, key = pair(tmp_path, "one")
        assert cert.activate(ctx, fullchain, key)
        current = ctx.data / "certificates/current"
        assert (current / "fullchain.pem").read_bytes() == fullchain.read_bytes()
        assert (current / "privkey.pem").read_bytes() == key.read_bytes()
        assert (current / "privkey.pem").stat().st_mode & 0o077 == 0
        assert not cert.activate(ctx, fullchain, key)
        assert all("restart" not in cmd for cmd in commands)
    finally:
        os.umask(previous_mask)


def test_failed_live_rotation_restores_old_pair(tmp_path, monkeypatch):
    ctx = SimpleNamespace(data=tmp_path / "data", host="assistant.example.com")
    monkeypatch.setattr(cert.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=3))
    previous_mask = os.umask(0o077)
    try:
        old_cert, old_key = pair(tmp_path, "old")
        cert.activate(ctx, old_cert, old_key)
        current = ctx.data / "certificates/current"
        old_target = os.readlink(current)
        monkeypatch.setattr(cert.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
        monkeypatch.setattr(cert, "run", lambda *args, **kwargs: 0)
        monkeypatch.setattr(cert.ssl, "PEM_cert_to_DER_cert", lambda value: b"new-der")
        monkeypatch.setattr(cert, "local_tls", lambda *args: (_ for _ in ()).throw(OSError("not ready")))
        with pytest.raises(cert.Problem, match="https_rotation_failed"):
            cert.activate(ctx, *pair(tmp_path, "new"))
        assert os.readlink(current) == old_target
        assert (current / "privkey.pem").read_bytes() == old_key.read_bytes()
    finally:
        os.umask(previous_mask)


def test_unexpected_parser_errors_do_not_expose_credentials(monkeypatch, capsys):
    def broken_context():
        raise ValueError("test-secret should never appear in output")
    monkeypatch.setattr(cert, "Context", broken_context)
    assert cert.main(["inspect"]) == 1
    output = capsys.readouterr().out
    assert "test-secret" not in output
    assert json.loads(output)["error"] == "certificate_operation_failed"
