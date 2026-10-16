"""Local credential readiness is distinct from observed MCP connectivity."""
import json

import pytest

from src.agents import mcp_tools
from src.workflow.workbench import connectors


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setattr(connectors.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    (tmp_path / "config").mkdir()
    (tmp_path / "config/mcp.yaml").write_text(
        "servers:\n  - name: fixture\n    transport: http\n    credential: fixture\n"
        "    oauth:\n      provider: github\n      client_id: Iv23liExampleClient1\n")
    connectors._PENDING.clear()
    monkeypatch.setattr(connectors, "_post", lambda *_: pytest.fail("Status must not probe a provider"))
    yield str(tmp_path)
    connectors._PENDING.clear()


def saved(root):
    connectors._save_token("fixture", "synthetic-private-credential", None)
    return connectors._token_path("fixture")


def row(root):
    result = connectors.status(root)[0]
    encoded = json.dumps(result, allow_nan=False)
    assert "synthetic-private-credential" not in encoded and root not in encoded
    assert result["connection_status"] == "unobserved"
    return result


def test_private_credential_is_available_without_claiming_a_live_connection(configured):
    saved(configured)
    result = row(configured)
    assert result["credential_status"] == "available" and result["credential_available"]
    assert result["authorized_at"] == result["connected_at"]
    assert result["connected"]  # Legacy alias for credential readiness only.


def test_manually_provisioned_token_does_not_require_authorization_metadata(configured):
    saved(configured)
    connectors._meta_path("fixture").unlink()
    result = row(configured)
    assert result["credential_status"] == "available" and result["authorized_at"] is None


@pytest.mark.parametrize("invalid", ["missing", "public", "empty", "whitespace", "oversized", "symlink", "directory", "owner"])
def test_status_agrees_with_real_credential_loader(configured, monkeypatch, invalid):
    token = saved(configured)
    if invalid == "missing":
        token.unlink()
    elif invalid == "public":
        token.chmod(0o644)
    elif invalid == "empty":
        token.write_text("")
    elif invalid == "whitespace":
        token.write_text("invalid credential")
    elif invalid == "oversized":
        token.write_text("x" * 4097)
    elif invalid == "owner":
        actual_owner = connectors.os.getuid()
        monkeypatch.setattr(connectors.os, "getuid", lambda: actual_owner + 1)
    else:
        token.unlink()
        if invalid == "directory":
            token.mkdir()
        else:
            target = token.parent / "synthetic-target"
            target.write_text("synthetic-private-credential")
            target.chmod(0o600)
            token.symlink_to(target)
    with pytest.raises((OSError, ValueError)):
        mcp_tools.load_connector_secret("fixture")
    result = row(configured)
    assert result["credential_status"] == ("missing" if invalid == "missing" else "invalid")
    assert not result["credential_available"] and not result["connected"]


@pytest.mark.parametrize("expiry", ["synthetic-private-credential", True, -1, float("nan"), float("inf"), 2**100])
def test_bad_expiry_cannot_crash_status_or_be_reported_ready(configured, expiry):
    saved(configured)
    connectors._meta_path("fixture").write_text(json.dumps({"expires_at": expiry, "connected_at": "synthetic-private-credential"}))
    result = row(configured)
    assert result["credential_status"] == "unknown" and not result["credential_available"]
    assert result["expires_at"] is None and result["authorized_at"] is None


@pytest.mark.parametrize("raw", ["not-json", "[]", "x" * 8193, "[" * 2000 + "0" + "]" * 2000], ids=["invalid-json", "not-object", "oversized", "deep-json"])
def test_unreadable_metadata_is_unknown_not_an_unexpiring_credential(configured, raw):
    saved(configured)
    connectors._meta_path("fixture").write_text(raw)
    result = row(configured)
    assert result["credential_status"] == "unknown" and result["credential_error"]


@pytest.mark.parametrize("remaining,expected", [(-60, "expired"), (60, "available"), (0, "expired")])
def test_known_expiry_is_enforced_without_provider_calls(configured, monkeypatch, remaining, expected):
    saved(configured)
    monkeypatch.setattr(connectors.time, "time", lambda: 1000)
    connectors._meta_path("fixture").write_text(json.dumps({"expires_at": 1000 + remaining}))
    result = row(configured)
    assert result["credential_status"] == expected
    assert result["credential_available"] is (expected == "available")


def test_status_does_not_follow_authorization_metadata_link(configured):
    saved(configured)
    path = connectors._meta_path("fixture")
    path.unlink()
    target = path.parent / "synthetic-meta"
    target.write_text('{"expires_at": null}')
    path.symlink_to(target)
    assert row(configured)["credential_status"] == "unknown"
