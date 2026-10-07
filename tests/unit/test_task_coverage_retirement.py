"""Released scan identities survive archive relocation without becoming coverage."""

import copy
import json
import os
import stat
from pathlib import Path

import pytest

from src.gateway.collaboration import CollaborationConflict
from src.gateway.task_scan import archive_path, retirement_path, digest, verify_retirement, verify_retirement_path
from src.gateway.dispatch import DispatchService
from src.gateway.tasks import TaskRunner
from tests.unit.test_task_coverage import fixture, finish, step, task_bytes
from tests.unit.test_task_coverage_archive import retain, release


def test_released_scan_cannot_recreate_after_archive_is_moved(tmp_path):
    service, source, _, calls = fixture(tmp_path, children=0)
    finish(service, source, request_id="fixed-request")
    archive = retain(service, source)
    release(service, source, archive)
    before = task_bytes(service)
    path = archive_path(service.runner.ledger, archive["archive_id"])
    path.rename(tmp_path / "kept-original-archive.json")
    with pytest.raises(CollaborationConflict, match="已归档"):
        step(service, source, request_id="fixed-request")
    assert before == task_bytes(service) and not calls


def retained(tmp_path):
    service, source, _, calls = fixture(tmp_path, children=1)
    finish(service, source)
    archive = retain(service, source)
    return service, source, archive, calls


def restored(service):
    root = service.runner.ledger.repo_root
    return DispatchService(root, TaskRunner(root, lambda *args: None), execute=lambda *args: None, validate_agent=lambda _: None)


def test_fixed_binding_replay_restart_and_read_are_immutable(tmp_path):
    service, source, archive, calls = retained(tmp_path)
    ledger = service.runner.ledger
    before = task_bytes(service)
    original = archive_path(ledger, archive["archive_id"]).read_bytes()
    assert service.coverage_records("owner", source.id)["archives"][0]["retirement"] == {"state": "not_recorded"}
    with pytest.raises(CollaborationConflict, match="尚未保存"):
        service.coverage_retirement("owner", source.id, 1, archive["scan_id"])
    result = release(service, source, archive)
    view = result["retirement"]
    path = retirement_path(ledger, archive["scan_id"])
    fixed = path.read_bytes(), path.stat().st_mtime_ns
    record = ledger.load_scan_retirement(archive["scan_id"])
    e = verify_retirement(record)
    assert record["sha256"] == digest(e) == view["retirement_digest"]
    assert e["scope"] == ["sid-owner", source.id, 1, archive["report"]["request_id"], "reconcile", 512]
    assert e["coverage"]["snapshot_id"] == archive["report"]["snapshot_id"]
    assert e["coverage"]["evidence_digest"] == archive["report"]["evidence_digest"]
    assert e["coverage"]["receipt_count"] == archive["report"]["receipt_count"]
    assert e["checkpoint_digest"] == archive["checkpoint_digest"] and e["archive_digest"] == archive["archive_digest"]
    restarted = restored(service)
    assert release(restarted, source, archive)["retirement"] == view
    assert restarted.coverage_retirement("owner", source.id, 1, archive["scan_id"]) == view
    assert verify_retirement_path(path) == view
    assert (path.read_bytes(), path.stat().st_mtime_ns) == fixed
    assert archive_path(ledger, archive["archive_id"]).read_bytes() == original
    assert before == task_bytes(service) and not calls
    assert view["historical"] and not any(view[k] for k in ("current_coverage", "resumable", "history_capacity_released", "durable_copy_confirmed"))


def test_legacy_archives_protect_until_explicit_backfill(tmp_path):
    service, source, archive, calls = retained(tmp_path)
    ledger = service.runner.ledger
    from src.gateway.task_scan import checkpoint_path
    checkpoint_path(ledger, archive["scan_id"]).unlink()  # Previous version already released without retirement.
    before = archive_path(ledger, archive["archive_id"]).read_bytes()
    assert not ledger.scan_retirement_ids()
    assert service.coverage_records("owner", source.id)["archives"][0]["retirement"]["state"] == "not_recorded"
    with pytest.raises(CollaborationConflict, match="已归档"):
        step(service, source)
    assert not ledger.scan_retirement_ids()  # No implicit migration on read/start.
    view = release(service, source, archive)["retirement"]
    assert view["reason"] == "explicit_legacy_identity_retirement"
    assert archive_path(ledger, archive["archive_id"]).read_bytes() == before and not calls


def test_partial_release_freezes_pages_status_observation_but_can_retry(tmp_path, monkeypatch):
    service, source, archive, calls = retained(tmp_path)
    ledger = service.runner.ledger
    from src.gateway.task_scan import checkpoint_path
    path = checkpoint_path(ledger, archive["scan_id"])
    before = path.read_bytes(), task_bytes(service)
    unlink = Path.unlink
    def fail_unlink(target, *args, **kwargs):
        if target == path:
            raise OSError("release interrupted")
        return unlink(target, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(OSError, match="interrupted"):
            release(service, source, archive)
    retirement = service.coverage_retirement("owner", source.id, 1, archive["scan_id"])
    with pytest.raises(CollaborationConflict, match="退役"):
        step(service, source)
    with pytest.raises(CollaborationConflict, match="退役"):
        service.dependency_scan_status("owner", source.id, 1, archive["scan_id"])
    assert service.dependency_scan_status("owner", source.id, 1) is None
    records = service.coverage_records("owner", source.id)
    row = records["active"][0]
    assert row["report"]["coverage"]["state"] == "unknown" and row["report"]["next_cursor"] is None
    ref = {"scan_id": archive["scan_id"], "round": 1, "checkpoint_digest": archive["checkpoint_digest"],
           "snapshot_id": archive["report"]["snapshot_id"], "sequence": archive["report"]["sequence"]}
    observed = service.observe_coverage("owner", source.id, workspace=records["workspace_id"], request_id="refuse", checkpoints=[ref])
    assert observed["results"][0]["reason"] == "scan_retired" and observed["observation_state"] == "unknown"
    assert (path.read_bytes(), task_bytes(service)) == before and not calls
    assert release(restored(service), source, archive)["retirement"] == retirement
    assert ledger.load_scan(archive["scan_id"]) is None


@pytest.mark.parametrize("stage", ["file_sync", "publish", "directory_sync", "parent_sync", "readback"])
def test_save_failure_never_releases_and_retry_preserves_published_evidence(tmp_path, monkeypatch, stage):
    service, source, archive, calls = retained(tmp_path)
    ledger = service.runner.ledger
    before = task_bytes(service)
    archive_bytes = archive_path(ledger, archive["archive_id"]).read_bytes()
    from src.gateway import task_scan as scan
    sync, link, fsync, load = scan._sync_directory, os.link, os.fsync, scan.load_retirement
    def fail_sync(path):
        if (stage == "directory_sync" and path.name == "retirements"
                or stage == "parent_sync" and path.name == "task_scans" and retirement_path(ledger, archive["scan_id"]).exists()):
            raise OSError("retirement directory sync failed")
        return sync(path)
    def fail_fsync(fd):
        if stage == "file_sync" and stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("retirement file sync failed")
        return fsync(fd)
    def fail_link(*args, **kwargs):
        raise OSError("retirement publish failed")
    def fail_load(*args, **kwargs):
        result = load(*args, **kwargs)
        if result is not None:
            raise OSError("retirement readback failed")
        return result
    with monkeypatch.context() as patch:
        if stage == "file_sync":
            patch.setattr(os, "fsync", fail_fsync)
        elif stage == "publish":
            patch.setattr(os, "link", fail_link)
        elif stage == "readback":
            patch.setattr(scan, "load_retirement", fail_load)
        else:
            patch.setattr(scan, "_sync_directory", fail_sync)
        with pytest.raises(OSError):
            release(service, source, archive)
    assert ledger.load_scan(archive["scan_id"]) is not None
    fixed = ledger.load_scan_retirement(archive["scan_id"])
    assert not list(retirement_path(ledger, archive["scan_id"]).parent.glob("*.tmp"))
    result = release(restored(service), source, archive)
    if fixed:
        assert result["retirement"]["artifact"] == fixed
    assert archive_path(ledger, archive["archive_id"]).read_bytes() == archive_bytes
    assert before == task_bytes(service) and not calls


@pytest.mark.parametrize("damage", ["json", "oversize", "symlink", "directory", "checksum", "duplicate", "filename"])
def test_bad_retirement_is_unknown_counted_and_never_allows_recreation(tmp_path, damage):
    service, source, archive, calls = retained(tmp_path)
    release(service, source, archive)
    ledger = service.runner.ledger
    path = retirement_path(ledger, archive["scan_id"])
    original = path.read_bytes()
    if damage == "json":
        path.write_text("{")
    elif damage == "oversize":
        path.write_bytes(b" " * 8193)
    elif damage == "symlink":
        path.unlink(); (tmp_path / "original.json").write_bytes(original); path.symlink_to(tmp_path / "original.json")
    elif damage == "directory":
        path.unlink(); path.mkdir()
    else:
        record = json.loads(original)
        if damage == "checksum":
            record["sha256"] = "f" * 64
        if damage == "filename":
            record["evidence"]["scan_id"] = "scan-" + "f" * 24
            record["retirement_id"] = "retired-" + "f" * 24
            record["sha256"] = digest(record["evidence"])
        raw = json.dumps(record)
        if damage == "duplicate":
            raw = raw[:-1] + ',"version":1}'
        path.write_text(raw)
    records = service.coverage_records("owner", source.id)
    assert records["capacity"]["retired_used"] == 1
    assert records["archives"][0]["retirement"] == {"state": "unknown"}
    with pytest.raises(OSError):
        step(service, source)
    with pytest.raises(OSError):
        release(service, source, archive)
    with pytest.raises(OSError):
        service.coverage_retirement("owner", source.id, 1, archive["scan_id"])
    assert not calls


@pytest.mark.parametrize("key,value", [("scope_round", True), ("scope_mode", "advance"), ("scope_size", 513),
    ("time", "2026-10-04T12:00:00"), ("count", 1026), ("receipt_count", 999), ("current", True), ("state", "incomplete")])
def test_summary_and_identity_validation_even_with_recomputed_sha(tmp_path, key, value):
    service, source, archive, _ = retained(tmp_path)
    record = copy.deepcopy(release(service, source, archive)["retirement"]["artifact"])
    e = record["evidence"]
    if key == "scope_round": e["scope"][2] = value
    if key == "scope_mode": e["scope"][4] = value
    if key == "scope_size": e["scope"][5] = value
    if key == "time": e["retired_at"] = value
    if key == "count": e["coverage"]["sequence"] = value
    if key == "receipt_count": e["coverage"]["receipt_count"] = value
    if key == "current": e["current_coverage"] = value
    if key == "state": e["coverage"]["state"] = value
    record["sha256"] = digest(e)
    with pytest.raises(OSError): verify_retirement(record)


def test_capacity_is_finite_bad_records_count_replay_works_no_pruning(tmp_path):
    service, source, archive, calls = retained(tmp_path)
    ledger = service.runner.ledger
    directory = retirement_path(ledger, archive["scan_id"]).parent
    directory.mkdir(parents=True)
    for i in range(128): (directory / f"bad-{i:03}.json").write_text("{")
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(CollaborationConflict, match="128"):
        release(service, source, archive)
    assert ledger.load_scan(archive["scan_id"]) and before == {p.name: p.read_bytes() for p in directory.iterdir()}
    (directory / "bad-000.json").rename(tmp_path / "explicit-test-kept-bad.json")
    result = release(service, source, archive)
    assert len(ledger.scan_retirement_ids()) == 128
    assert release(service, source, archive)["retirement"] == result["retirement"]
    assert not calls


def test_symlink_retirement_directory_cannot_publish_elsewhere_or_release(tmp_path):
    service, source, archive, _ = retained(tmp_path)
    ledger = service.runner.ledger
    directory = retirement_path(ledger, archive["scan_id"]).parent
    outside = tmp_path / "unrelated-directory"
    outside.mkdir()
    directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError, match="目录"):
        release(service, source, archive)
    assert ledger.load_scan(archive["scan_id"]) and not list(outside.iterdir())


def test_new_identity_reserves_final_directory_slot_without_pruning_orphans(tmp_path):
    service, source, archive, calls = retained(tmp_path)
    ledger = service.runner.ledger
    directory = retirement_path(ledger, archive["scan_id"]).parent
    directory.mkdir(parents=True)
    for i in range(256):
        (directory / f"orphan-{i:03}.tmp").write_text("retained failure evidence")
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(CollaborationConflict, match="256"):
        release(service, source, archive)
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}
    assert ledger.load_scan(archive["scan_id"]) and not ledger.load_scan_retirement(archive["scan_id"])
    (directory / "orphan-000.tmp").rename(tmp_path / "explicit-test-kept-orphan.tmp")
    result = release(service, source, archive)
    assert result["released"] and len(list(directory.iterdir())) == 256
    assert len(ledger.scan_retirement_ids()) == 1 and release(service, source, archive)["retirement"] == result["retirement"]
    assert not calls


def test_read_scope_offline_and_deleted_tasks_do_not_mutate_evidence(tmp_path):
    service, source, archive, _ = retained(tmp_path)
    view = release(service, source, archive)["retirement"]
    ledger = service.runner.ledger
    path = retirement_path(ledger, archive["scan_id"])
    before = path.read_bytes(), path.stat().st_mtime_ns
    ledger._path(source.id).unlink()
    archive_path(ledger, archive["archive_id"]).rename(tmp_path / "explicitly-kept-archive.json")
    assert service.coverage_retirement("owner", source.id, 1, archive["scan_id"]) == view
    records = restored(service).coverage_records("owner", source.id)
    assert not records["archives"] and records["retirements"] == [view] and records["retirement_count"] == 1
    assert not service.coverage_records("other", source.id)["retirements"]
    for session, task, round_number in [("other", source.id, 1), ("owner", "task-other", 1), ("owner", source.id, 2)]:
        with pytest.raises(CollaborationConflict):
            service.coverage_retirement(session, task, round_number, archive["scan_id"])
    with pytest.raises(CollaborationConflict): verify_retirement_path(path, expected={"owner_session": "sid-other"})
    assert before == (path.read_bytes(), path.stat().st_mtime_ns)


def test_retirement_pages_are_independent_bounded_and_damaged_scope_is_not_guessed(tmp_path):
    service, source, _, _ = fixture(tmp_path, children=0)
    views = []
    for i in range(18):
        finish(service, source, request_id=f"old-{i}")
        a = retain(service, source, request=f"kept-{i}")
        views.append(release(service, source, a)["retirement"])
    first = service.coverage_records("owner", source.id)
    last = service.coverage_records("owner", source.id, offset=16, retirement_offset=16)
    assert len(first["retirements"]) == 16 and first["next_retirement_offset"] == 16
    assert len(last["retirements"]) == 2 and last["next_retirement_offset"] is None
    assert {v["scan_id"] for v in first["retirements"] + last["retirements"]} == {v["scan_id"] for v in views}
    bad = retirement_path(service.runner.ledger, views[0]["scan_id"])
    bad.write_text("{")
    result = service.coverage_records("owner", source.id)
    assert result["capacity"]["retired_used"] == 18 and result["capacity"]["unreadable_retired"] == 1
    assert result["retirement_count"] == 17 and all(v["scan_id"] != views[0]["scan_id"] for v in result["retirements"])
    with pytest.raises(ValueError): service.coverage_records("owner", source.id, retirement_offset=129)


def test_cli_is_before_env_logs_and_has_explicit_expected_binding(tmp_path, monkeypatch, capsys):
    service, source, archive, _ = retained(tmp_path)
    view = release(service, source, archive)["retirement"]
    path = retirement_path(service.runner.ledger, archive["scan_id"])
    from src.cli import main
    monkeypatch.setenv("VORTOCODE_LOG_FILE", str(tmp_path / "must-not-exist.log"))
    monkeypatch.setattr("sys.argv", ["vc", "coverage-retirement-verify", str(path), "--scan-id", archive["scan_id"],
                                    "--retirement-digest", view["retirement_digest"]])
    with pytest.raises(SystemExit) as result: main()
    assert result.value.code == 0 and json.loads(capsys.readouterr().out) == view
    monkeypatch.setattr("sys.argv", ["vc", "coverage-retirement-verify", str(path), "--source-round", "2"])
    with pytest.raises(SystemExit) as result: main()
    assert result.value.code == 1 and json.loads(capsys.readouterr().out)["state"] == "unknown"
    assert not (tmp_path / "must-not-exist.log").exists()


@pytest.mark.asyncio
async def test_http_read_retirement_auth_scope_and_absent_archive(tmp_path, monkeypatch):
    import httpx
    from src.web.server import app
    service, source, archive, _ = retained(tmp_path)
    view = release(service, source, archive)["retirement"]
    archive_path(service.runner.ledger, archive["archive_id"]).rename(tmp_path / "kept.json")
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "retirement-local-only")
    monkeypatch.setattr("src.web.routers.delegations.get_dispatch_service", lambda: service)
    path = f'/api/delegations/{source.id}/dependency-scans/{archive["scan_id"]}/retirement'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(path, params={"session": "owner", "round": 1})).status_code == 401
        client.headers["Authorization"] = "Bearer retirement-local-only"
        result = await client.get(path, params={"session": "owner", "round": 1})
        assert result.status_code == 200 and result.json() == view
        assert (await client.get(path, params={"session": "other", "round": 1})).status_code == 409
        assert (await client.get(path, params={"session": "owner", "round": 2})).status_code == 409
        assert (await client.get(path, params={"session": "owner", "round": 0})).status_code == 422
        retirement_path(service.runner.ledger, archive["scan_id"]).write_text("{")
        assert (await client.get(path, params={"session": "owner", "round": 1})).status_code == 503
