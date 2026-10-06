"""Portable historical bytes can be verified, never imported or used as retention proof."""
import copy
import hashlib
import json
import os
import sys

import pytest

from src.gateway.collaboration import CollaborationConflict
from src.gateway.task_scan import MAX_ARCHIVE_BYTES, archive_path, digest, verify_file_bytes, verify_file_path
from tests.unit.test_task_coverage import fixture, finish, task_bytes
from tests.unit.test_task_coverage_archive import exported, retain


def file_fixture(root):
    service, source, _, calls = fixture(root, children=0)
    finish(service, source)
    a = retain(service, source)
    artifact = exported(service, source, a)
    raw = json.dumps(artifact, ensure_ascii=True).encode()
    return service, source, a, artifact, raw, calls


def expected(a, raw):
    return {key: a[key] for key in ("archive_id", "archive_digest", "workspace_id", "owner_session", "source_task_id", "source_round")} | {
        "file_digest": hashlib.sha256(raw).hexdigest()}


def test_portable_original_bytes_precision_and_repeated_read_only_verification(tmp_path):
    service, source, a, artifact, raw, calls = file_fixture(tmp_path)
    assert artifact["evidence"]["checkpoint"]["entries"][0][1][4] > 2**53
    before = task_bytes(service), copy.deepcopy(service.runner.ledger.load_scan(a["scan_id"])), archive_path(service.runner.ledger, a["archive_id"]).read_bytes()
    first = verify_file_bytes(raw, expected=expected(a, raw))
    second = verify_file_bytes(raw, expected=expected(a, raw))
    assert first["verified"] and first["historical"] and not first["current_coverage"] and not first["resumable"]
    assert first["file_digest"] == second["file_digest"] == hashlib.sha256(raw).hexdigest()
    assert first["file_bytes"] == len(raw) and not first["durable_copy_confirmed"] and not first["imported"] and not first["capacity_released"]
    assert first["expected_binding_checked"] == sorted(expected(a, raw))
    assert before == (task_bytes(service), service.runner.ledger.load_scan(a["scan_id"]), archive_path(service.runner.ledger, a["archive_id"]).read_bytes()) and not calls


def test_verify_uploaded_copy_needs_no_live_task_checkpoint_or_archive(tmp_path, monkeypatch):
    service, source, a, _, raw, _ = file_fixture(tmp_path)
    source.owner_session = "sid-changed"
    source.collaboration["round"] = 2
    service.runner.ledger.save(source)
    archive_path(service.runner.ledger, a["archive_id"]).write_text("{")
    def deny(*args, **kwargs):
        raise AssertionError("no local evidence reads or writes")
    for name in ("load", "save", "load_scan", "save_scan", "load_scan_archive", "save_scan_archive", "scan_inventory"):
        monkeypatch.setattr(service.runner.ledger, name, deny)
    result = service.verify_coverage_file("owner", source.id, 1, a["archive_id"], archive_digest=a["archive_digest"],
        file_digest=hashlib.sha256(raw).hexdigest(), payload=raw)
    assert result["verified"] and not result["durable_copy_confirmed"]


@pytest.mark.parametrize("field", ["archive_id", "archive_digest", "workspace_id", "owner_session", "source_task_id", "source_round", "file_digest"])
def test_cross_binding_and_wrong_raw_digest_rejected(tmp_path, field):
    _, _, a, _, raw, _ = file_fixture(tmp_path)
    contract = expected(a, raw)
    contract[field] = 2 if field == "source_round" else "wrong"
    with pytest.raises(CollaborationConflict):
        verify_file_bytes(raw, expected=contract)


@pytest.mark.parametrize("damage", ["empty", "utf8", "bom", "duplicate", "nan", "truncated", "checksum", "rounded", "chain", "over_budget"])
def test_damaged_or_ambiguous_files_never_claim_verified(tmp_path, damage):
    _, _, _, artifact, raw, _ = file_fixture(tmp_path)
    if damage == "empty": raw = b""
    if damage == "utf8": raw = b"\xff"
    if damage == "bom": raw = b"\xef\xbb\xbf" + raw
    if damage == "duplicate": raw = raw.replace(b'"version": 1', b'"version": 1, "version": 1', 1)
    if damage == "nan": raw = raw.replace(b'"version": 1', b'"version": NaN', 1)
    if damage == "truncated": raw = raw[:-1]
    if damage == "checksum":
        artifact["sha256"] = "f" * 64
        raw = json.dumps(artifact).encode()
    if damage == "rounded":
        entry = artifact["evidence"]["checkpoint"]["entries"][0][1]
        entry[4] = int(float(entry[4]))
        raw = json.dumps(artifact).encode()
    if damage == "chain":
        state = artifact["evidence"]["checkpoint"]
        state["receipts"][0]["outcome"] = "f" * 64
        artifact["evidence"]["checkpoint_digest"] = digest(state)
        artifact["sha256"] = digest(artifact["evidence"])
        raw = json.dumps(artifact).encode()
    if damage == "over_budget": raw = b" " * (MAX_ARCHIVE_BYTES + 1)
    with pytest.raises((ValueError, OSError)):
        verify_file_bytes(raw)


def test_whitespace_changes_raw_digest_but_not_intrinsic_evidence(tmp_path):
    _, _, a, _, raw, _ = file_fixture(tmp_path)
    original = verify_file_bytes(raw)
    formatted = verify_file_bytes(b" \n" + raw + b"\n")
    assert original["archive_digest"] == formatted["archive_digest"] == a["archive_digest"]
    assert original["file_digest"] != formatted["file_digest"]


def test_overflowing_json_exponent_cannot_verify_or_return_non_finite_report(tmp_path):
    _, _, _, artifact, _, _ = file_fixture(tmp_path)
    state = artifact["evidence"]["checkpoint"]
    state["last_errors"] = [float("inf")]
    artifact["evidence"]["checkpoint_digest"] = digest(state)
    artifact["sha256"] = digest(artifact["evidence"])
    raw = json.dumps(artifact).replace("Infinity", "1e9999").encode()
    with pytest.raises(ValueError, match="有效"):
        verify_file_bytes(raw)


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo", "oversize", "changed"])
def test_offline_file_reader_refuses_unsafe_or_changing_files(tmp_path, monkeypatch, kind):
    _, _, _, _, raw, _ = file_fixture(tmp_path)
    path = tmp_path / "copy.json"
    path.write_bytes(raw)
    if kind == "symlink":
        original = tmp_path / "original.json"
        path.rename(original)
        path.symlink_to(original)
    if kind == "directory":
        path.unlink(); path.mkdir()
    if kind == "fifo":
        path.unlink(); os.mkfifo(path)
    if kind == "oversize":
        with path.open("wb") as target: target.truncate(MAX_ARCHIVE_BYTES + 1)
    if kind == "changed":
        original = os.lstat
        def change(current):
            path.write_bytes(raw + b" ")
            return original(current)
        monkeypatch.setattr("src.gateway.task_scan.os.lstat", change)
    with pytest.raises((ValueError, OSError)):
        verify_file_path(path)


def test_cli_offline_scope_checks_without_repo_provider_or_log_setup(tmp_path, monkeypatch, capsys):
    from src import cli
    _, _, a, _, raw, _ = file_fixture(tmp_path / "original")
    path = tmp_path / "external-copy.json"
    path.write_bytes(raw)
    working = tmp_path / "no-ledger"; working.mkdir()
    monkeypatch.chdir(working)
    monkeypatch.setenv("VORTOCODE_LOG_FILE", str(tmp_path / "must-not-create.log"))
    monkeypatch.setattr(cli, "_setup_logging", lambda: (_ for _ in ()).throw(AssertionError("no env/log setup")))
    args = ["vc", "coverage-verify", str(path), "--owner-session", a["owner_session"], "--workspace-id", a["workspace_id"]]
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as exit:
        cli.main()
    assert exit.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["verified"] and report["expected_binding_checked"] == ["owner_session", "workspace_id"]
    assert list(working.iterdir()) == [] and not (tmp_path / "must-not-create.log").exists()
    monkeypatch.setattr(sys, "argv", args + ["--source-round", "2"])
    with pytest.raises(SystemExit) as exit:
        cli.main()
    assert exit.value.code == 1 and json.loads(capsys.readouterr().out)["state"] == "unknown"


@pytest.mark.asyncio
async def test_raw_http_auth_precision_stream_budget_and_scope(tmp_path, monkeypatch):
    import httpx
    from src.web.server import app
    service, source, a, _, raw, _ = file_fixture(tmp_path)
    monkeypatch.setattr("src.web.routers.delegations.get_dispatch_service", lambda: service)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "file-local")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    url = f"/api/delegations/{source.id}/coverage-archives/{a['archive_id']}/verify-file"
    params = {"session": "owner", "round": 1, "archive_digest": a["archive_digest"], "file_digest": hashlib.sha256(raw).hexdigest()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        assert (await client.post(url, params=params, content=raw)).status_code == 401
        client.headers.update({"Authorization": "Bearer file-local", "Content-Type": "application/json"})
        result = await client.post(url, params=params, content=raw)
        assert result.status_code == 200 and result.json()["file_digest"] == hashlib.sha256(raw).hexdigest()
        assert (await client.post(url, params={**params, "extra": True}, content=raw)).status_code == 422
        assert (await client.post(url, params=[*params.items(), ("session", "other")], content=raw)).status_code == 422
        assert (await client.post(url, params={**params, "session": "other"}, content=raw)).status_code == 409
        assert (await client.post(url, params=params, content=raw, headers={"Content-Type": "text/plain"})).status_code == 415
        assert (await client.post(url, params=params, content=raw, headers={"Content-Length": str(MAX_ARCHIVE_BYTES + 1)})).status_code == 413
        async def chunks():
            yield b" " * MAX_ARCHIVE_BYTES
            yield b"extra"
        assert (await client.post(url, params=params, content=chunks())).status_code == 413
        monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
        assert (await client.post(url, params=params, content=raw)).status_code == 409
