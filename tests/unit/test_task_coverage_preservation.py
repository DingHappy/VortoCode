"""Local preservation is observable evidence, never a retirement authorization."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from src.gateway import task_scan as scan
from tests.unit.test_task_coverage_file import file_fixture, expected


def prepared(root):
    service, source, archive, artifact, raw, calls = file_fixture(root / "ledger")
    source_file = root / "export.json"
    source_file.write_bytes(raw)
    store = root / "store"
    store.mkdir()
    return service, source, archive, source_file, store, raw, calls


def fingerprints(root):
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*.json")
    }


def test_explicit_preservation_sync_reread_repeat_restart_and_ledger_unchanged(tmp_path):
    service, _, archive, file, store, raw, calls = prepared(tmp_path)
    original = fingerprints(tmp_path / "ledger")
    result = scan.preserve_file_path(file, store, expected=expected(archive, raw))
    assert result["preservation"]["state"] == "synced_and_read_back"
    assert result["preservation"]["storage_observed_now"] and not result["durable_copy_confirmed"]
    assert (
        result["historical"]
        and not result["current_coverage"]
        and not result["capacity_released"]
        and not result["imported"]
    )
    assert Path(result["artifact_path"]).read_bytes() == raw
    assert len(list(store.iterdir())) == 3
    assert Path(result["artifact_path"]).stat().st_mode & 0o777 == 0o600
    stored = fingerprints(store)
    again = scan.preserve_file_path(file, store)
    assert (
        again["replayed"]
        and again["preservation"] == result["preservation"]
        and stored == fingerprints(store)
    )
    reopened = scan.verify_preservation_path(result["receipt_path"], store)
    assert reopened["preservation"]["state"] == "read_back_now" and stored == fingerprints(store)
    assert original == fingerprints(tmp_path / "ledger") and not calls
    assert (
        service.coverage_records("owner", archive["source_task_id"])["capacity"]["archives_used"]
        == 1
    )


def test_uploaded_receipt_and_bytes_never_read_path_or_current_source(tmp_path, monkeypatch):
    service, source, archive, file, store, raw, _ = prepared(tmp_path)
    result = scan.preserve_file_path(file, store)
    receipt = Path(result["receipt_path"]).read_bytes()
    source.owner_session = "sid-changed"
    source.collaboration["round"] = 2
    service.runner.ledger.save(source)
    scan.archive_path(service.runner.ledger, archive["archive_id"]).write_text("{")
    Path(
        result["artifact_path"]
    ).unlink()  # Upload consistency must not claim storage still exists.
    payload = json.dumps({"artifact_text": raw.decode(), "receipt_text": receipt.decode()}).encode()

    def deny(*args, **kwargs):
        raise AssertionError("no current filesystem/task operation")

    for method in (
        "load",
        "save",
        "load_scan",
        "save_scan",
        "load_scan_archive",
        "save_scan_archive",
        "scan_inventory",
    ):
        monkeypatch.setattr(service.runner.ledger, method, deny)
    monkeypatch.setattr("src.gateway.task_scan.os.open", deny)
    view = service.verify_coverage_preservation(
        "owner",
        source.id,
        1,
        archive["archive_id"],
        archive_digest=archive["archive_digest"],
        file_digest=hashlib.sha256(raw).hexdigest(),
        receipt_file_digest=hashlib.sha256(receipt).hexdigest(),
        payload=payload,
    )
    assert (
        view["verified"]
        and view["preservation"]["state"] == "receipt_and_uploaded_bytes_consistent"
    )
    assert not view["preservation"]["storage_observed_now"] and not view["durable_copy_confirmed"]


@pytest.mark.parametrize(
    "failure",
    [
        "artifact_write",
        "artifact_sync",
        "artifact_link",
        "directory_sync",
        "receipt_write",
        "receipt_sync",
        "receipt_link",
        "read_back",
    ],
)
def test_storage_failures_preserve_ledger_and_retry_without_overwriting(
    tmp_path, monkeypatch, failure
):
    _, _, archive, file, store, raw, _ = prepared(tmp_path)
    before = fingerprints(tmp_path / "ledger")
    original_publish = scan._publish_preserved
    original_read = scan._read_preserved
    original_fsync = os.fsync
    original_link = os.link
    seen = {"publishes": 0, "syncs": 0, "links": 0}

    def publish(fd, name, payload):
        seen["publishes"] += 1
        if (
            failure == "artifact_write"
            and seen["publishes"] == 1
            or failure == "receipt_write"
            and seen["publishes"] == 2
        ):
            raise OSError("injected write failure")
        return original_publish(fd, name, payload)

    def sync(fd):
        seen["syncs"] += 1
        info = os.fstat(fd)
        if (
            failure == "artifact_sync"
            and seen["syncs"] == 1
            or failure == "receipt_sync"
            and seen["publishes"] == 2
        ):
            raise OSError("injected file sync failure")
        if failure == "directory_sync" and __import__("stat").S_ISDIR(info.st_mode):
            raise OSError("injected dir sync failure")
        return original_fsync(fd)

    def link(*args, **kwargs):
        seen["links"] += 1
        if (
            failure == "artifact_link"
            and seen["links"] == 1
            or failure == "receipt_link"
            and seen["links"] == 2
        ):
            raise OSError("injected no overwrite publish failure")
        return original_link(*args, **kwargs)

    def read(*args, **kwargs):
        if failure == "read_back" and str(args[1]).endswith(".preservation.json"):
            raise OSError("injected reread failure")
        return original_read(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(scan, "_publish_preserved", publish)
        patch.setattr(scan, "_read_preserved", read)
        patch.setattr(scan.os, "fsync", sync)
        patch.setattr(scan.os, "link", link)
        with pytest.raises(OSError):
            scan.preserve_file_path(file, store, expected=expected(archive, raw))
    assert fingerprints(tmp_path / "ledger") == before
    assert not any(p.name.endswith(".tmp") for p in store.iterdir())
    result = scan.preserve_file_path(file, store)
    assert Path(result["artifact_path"]).read_bytes() == raw and result["verified"]
    assert fingerprints(tmp_path / "ledger") == before


def test_orphan_original_occupies_capacity_and_same_identity_retry_completes(tmp_path, monkeypatch):
    _, _, _, file, store, raw, _ = prepared(tmp_path)
    publish = scan._publish_preserved
    with monkeypatch.context() as patch:

        def interrupted(fd, name, data):
            if name.endswith(".preservation.json"):
                raise OSError("interrupted after preserving original")
            return publish(fd, name, data)

        patch.setattr(scan, "_publish_preserved", interrupted)
        with pytest.raises(OSError):
            scan.preserve_file_path(file, store)
    for index in range(127):
        (store / f"damaged-{index}.archive.json").write_text("{")
    result = scan.preserve_file_path(file, store)
    assert not result["replayed"] and len(list(store.glob("*.archive.json"))) == 128
    before = fingerprints(store)
    file.write_bytes(raw + b" ")
    with pytest.raises(OSError, match="128"):
        scan.preserve_file_path(file, store)
    assert fingerprints(store) == before


@pytest.mark.parametrize(
    "damage",
    [
        "original",
        "receipt",
        "original_missing",
        "original_inode",
        "receipt_renamed",
        "directory_replaced",
    ],
)
def test_damaged_missing_or_changed_storage_cannot_be_repaired_by_repeat(tmp_path, damage):
    _, _, _, file, store, raw, _ = prepared(tmp_path)
    result = scan.preserve_file_path(file, store)
    original = Path(result["artifact_path"])
    receipt = Path(result["receipt_path"])
    if damage == "original":
        original.write_bytes(b"{")
    if damage == "receipt":
        receipt.write_bytes(b"{")
    if damage == "original_missing":
        original.unlink()
    if damage == "original_inode":
        replacement = store / "replacement"
        replacement.write_bytes(raw)
        replacement.replace(original)
    if damage == "receipt_renamed":
        renamed = store / (
            "archive-" + "f" * 24 + "-" + "f" * 24 + "." + "f" * 64 + ".preservation.json"
        )
        receipt.rename(renamed)
        original.rename(store / renamed.name.replace(".preservation.json", ".archive.json"))
        receipt = renamed
    if damage == "directory_replaced":
        store.rename(tmp_path / "old-store")
        store.mkdir()
        for p in (tmp_path / "old-store").glob("*.json"):
            (store / p.name).write_bytes(p.read_bytes())
    before = fingerprints(store)
    with pytest.raises((ValueError, OSError)):
        scan.verify_preservation_path(receipt, store)
    if damage != "receipt_renamed":
        with pytest.raises((ValueError, OSError)):
            scan.preserve_file_path(file, store)
    assert fingerprints(store) == before


@pytest.mark.parametrize(
    "field",
    [
        "archive_id",
        "workspace_id",
        "owner_session",
        "source_round",
        "file_digest",
        "file_bytes",
        "coverage",
        "storage",
        "sha256",
        "duplicate",
        "oversize",
    ],
)
def test_receipt_integrity_and_contract_damage_rejected(tmp_path, field):
    _, _, archive, file, store, raw, _ = prepared(tmp_path)
    result = scan.preserve_file_path(file, store)
    receipt = json.loads(Path(result["receipt_path"]).read_bytes())
    if field in receipt["evidence"] and field != "storage":
        receipt["evidence"][field] = True if field == "source_round" else "wrong"
    if field == "storage":
        receipt["evidence"]["storage"]["artifact_name"] = "../other.json"
    receipt["sha256"] = scan.digest(receipt["evidence"])
    if field == "sha256":
        receipt["sha256"] = "f" * 64
    data = json.dumps(receipt).encode()
    if field == "duplicate":
        data = data[:-1] + b',"version":1}'
    if field == "oversize":
        data = b" " * (scan.MAX_PRESERVATION_BYTES + 1)
    with pytest.raises((ValueError, OSError)):
        scan.verify_preservation_bytes(raw, data, expected=expected(archive, raw))


@pytest.mark.parametrize(
    "kind",
    [
        "symlink",
        "file",
        "missing",
        "ledger",
        "git",
        "lock_symlink",
        "lock_fifo",
        "directory_budget",
        "lock_busy",
    ],
)
def test_destination_permissions_identity_and_budgets(tmp_path, kind):
    _, _, _, file, store, _, _ = prepared(tmp_path)
    if kind == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(store)
        store = alias
    if kind == "file":
        store.rmdir()
        store.write_text("not a directory")
    if kind == "missing":
        store.rmdir()
    if kind in ("ledger", "git"):
        store = tmp_path / (".vortocode" if kind == "ledger" else ".git") / "preserve"
        store.mkdir(parents=True)
    if kind == "lock_symlink":
        (store / ".coverage-preservation.lock").symlink_to(file)
    if kind == "lock_fifo":
        os.mkfifo(store / ".coverage-preservation.lock")
    if kind == "directory_budget":
        for index in range(513):
            (store / str(index)).write_text("ignored but counted")
    handle = None
    if kind == "lock_busy":
        import fcntl

        handle = (store / ".coverage-preservation.lock").open("a+b")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises((ValueError, OSError)):
            scan.preserve_file_path(file, store)
    finally:
        if handle:
            handle.close()


@pytest.mark.parametrize("kind", ["directory", "lock"])
def test_directory_or_lock_replacement_during_save_stays_unknown(tmp_path, monkeypatch, kind):
    _, _, _, file, store, _, _ = prepared(tmp_path)
    original = scan._preservation_directory_current
    count = 0

    def replace(path, fd, before):
        nonlocal count
        count += 1
        if count == 2:
            if kind == "directory":
                store.rename(tmp_path / "retained-old")
                store.mkdir()
            else:
                (store / ".coverage-preservation.lock").unlink()
                (store / ".coverage-preservation.lock").touch()
        return original(path, fd, before)

    monkeypatch.setattr(scan, "_preservation_directory_current", replace)
    with pytest.raises(OSError, match="身份"):
        scan.preserve_file_path(file, store)
    retained = tmp_path / "retained-old" if kind == "directory" else store
    assert list(retained.glob("*.preservation.json"))  # keep unconfirmed evidence


def test_cli_explicit_store_and_readonly_verification_before_logging(tmp_path, monkeypatch, capsys):
    import sys
    from src import cli

    _, _, archive, file, store, raw, _ = prepared(tmp_path)
    monkeypatch.setattr(
        cli,
        "_setup_logging",
        lambda: (_ for _ in ()).throw(AssertionError("no provider/log setup")),
    )
    monkeypatch.setenv("VORTOCODE_LOG_FILE", str(tmp_path / "must-not-create.log"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "vc",
            "coverage-preserve",
            str(file),
            "--directory",
            str(store),
            "--owner-session",
            archive["owner_session"],
        ],
    )
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    saved = json.loads(capsys.readouterr().out)
    before = fingerprints(store)
    monkeypatch.setattr(
        sys,
        "argv",
        ["vc", "coverage-preservation-verify", saved["receipt_path"], "--directory", str(store)],
    )
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert (
        result.value.code == 0
        and json.loads(capsys.readouterr().out)["preservation"]["state"] == "read_back_now"
    )
    assert before == fingerprints(store) and not (tmp_path / "must-not-create.log").exists()
    monkeypatch.setattr(
        sys,
        "argv",
        ["vc", "coverage-preserve", str(file), "--directory", str(store), "--source-round", "2"],
    )
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 1 and json.loads(capsys.readouterr().out)["state"] == "unknown"
    assert before == fingerprints(store)


@pytest.mark.asyncio
async def test_http_raw_strings_identity_no_paths_written_and_stream_budget(tmp_path, monkeypatch):
    import httpx
    from src.web.server import app

    service, source, archive, file, store, raw, _ = prepared(tmp_path)
    saved = scan.preserve_file_path(file, store)
    receipt = Path(saved["receipt_path"]).read_bytes()
    payload = json.dumps({"artifact_text": raw.decode(), "receipt_text": receipt.decode()}).encode()
    monkeypatch.setattr("src.web.routers.delegations.get_dispatch_service", lambda: service)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "preservation-local")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    url = f"/api/delegations/{source.id}/coverage-archives/{archive['archive_id']}/verify-preservation"
    params = {
        "session": "owner",
        "round": 1,
        "archive_digest": archive["archive_digest"],
        "file_digest": hashlib.sha256(raw).hexdigest(),
        "receipt_file_digest": hashlib.sha256(receipt).hexdigest(),
    }
    before = fingerprints(tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        assert (await client.post(url, params=params, content=payload)).status_code == 401
        client.headers.update(
            {"Authorization": "Bearer preservation-local", "Content-Type": "application/json"}
        )
        result = await client.post(url, params=params, content=payload)
        assert (
            result.status_code == 200
            and result.json()["preservation"]["storage_observed_now"] is False
        )
        assert (
            await client.post(url, params={**params, "session": "other"}, content=payload)
        ).status_code == 409
        assert (
            await client.post(
                url, params={**params, "receipt_file_digest": "f" * 64}, content=payload
            )
        ).status_code == 409
        assert (
            await client.post(url, params=[*params.items(), ("session", "other")], content=payload)
        ).status_code == 422
        assert (
            await client.post(
                url,
                params=params,
                json={"artifact_text": json.loads(raw), "receipt_text": receipt.decode()},
            )
        ).status_code == 400
        assert (
            await client.post(
                url, params=params, content=payload, headers={"Content-Type": "text/plain"}
            )
        ).status_code == 415
        assert (
            await client.post(
                url,
                params=params,
                content=payload,
                headers={"Content-Length": str(scan.MAX_PRESERVATION_REQUEST_BYTES + 1)},
            )
        ).status_code == 413

        async def chunks():
            yield b" " * scan.MAX_PRESERVATION_REQUEST_BYTES
            yield b"extra"

        assert (await client.post(url, params=params, content=chunks())).status_code == 413
        assert (
            await client.post(url, params=params, content=payload[:-1] + b',"receipt_text":"x"}')
        ).status_code == 400
        monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
        assert (await client.post(url, params=params, content=payload)).status_code == 409
    assert fingerprints(tmp_path) == before


def test_receipt_changed_while_other_file_read_never_confirms_current_storage(
    tmp_path, monkeypatch
):
    _, _, _, file, store, _, _ = prepared(tmp_path)
    saved = scan.preserve_file_path(file, store)
    receipt_path = Path(saved["receipt_path"])
    original = scan._read_preserved

    def change(fd, name, maximum, **kwargs):
        result = original(fd, name, maximum, **kwargs)
        if name.endswith(".archive.json"):
            replacement = store / "new-receipt"
            replacement.write_bytes(receipt_path.read_bytes())
            replacement.replace(receipt_path)
        return result

    monkeypatch.setattr(scan, "_read_preserved", change)
    with pytest.raises(OSError, match="核对期间变化"):
        scan.verify_preservation_path(receipt_path, store)
