"""Retention requires immutable verified evidence, never silent pruning or work."""

import copy
import json
import os
from pathlib import Path

import pytest

from src.gateway.collaboration import CollaborationConflict
from src.gateway.dispatch import DispatchService
from src.gateway.task_scan import archive_path, digest, verify_archive
from src.gateway.tasks import TaskRunner
from tests.unit.test_task_coverage import fixture, finish, step, task_bytes


def active(service, source):
    return service.coverage_records("owner", source.id)["active"][0]


def retain(service, source, row=None, request="archive-1", **overrides):
    row = row or active(service, source)
    contract = {
        "session": "owner",
        "task_id": source.id,
        "round_number": row["report"]["source_round"],
        "scan_id": row["report"]["scan_id"],
        "request_id": request,
        "checkpoint_digest": row["checkpoint_digest"],
    }
    contract.update(overrides)
    return service.archive_coverage(**contract)


def release(service, source, archive, **overrides):
    contract = {
        "session": "owner",
        "task_id": source.id,
        "round_number": archive["source_round"],
        "scan_id": archive["scan_id"],
        "archive_id": archive["archive_id"],
        "archive_digest": archive["archive_digest"],
        "checkpoint_digest": archive["checkpoint_digest"],
    }
    contract.update(overrides)
    return service.release_coverage(**contract)


def exported(service, source, archive):
    return service.export_coverage(
        "owner", source.id, archive["source_round"], archive["archive_id"]
    )


def test_real_32_cap_archive_alone_does_not_release_explicit_release_retains_history(tmp_path):
    service, source, _, calls = fixture(tmp_path, children=0)
    for i in range(32):
        finish(service, source, request_id=f"scan-{i}")
    before = task_bytes(service)
    with pytest.raises(CollaborationConflict, match="保留上限"):
        step(service, source, request_id="scan-extra")
    row = active(service, source)
    a = retain(service, source, row)
    evidence = exported(service, source, a)
    assert a["historical"] and not a["current_coverage"] and not a["resumable"]
    assert a["report"]["complete"] and a["report"]["next_cursor"] is None
    assert service.coverage_records("owner", source.id)["capacity"]["active_used"] == 32
    result = release(service, source, a)
    assert result["released"] and not result["replayed"]
    assert exported(service, source, a) == evidence
    assert service.coverage_records("owner", source.id)["capacity"]["active_used"] == 31
    assert step(service, source, request_id="scan-extra")["complete"]
    with pytest.raises(CollaborationConflict, match="已归档"):
        step(service, source, request_id=row["report"]["request_id"])
    assert before == task_bytes(service) and not calls


def test_duplicate_archive_release_and_restart_keep_same_immutable_file(tmp_path):
    service, source, _, calls = fixture(tmp_path, children=0)
    finish(service, source)
    row = active(service, source)
    a = retain(service, source, row)
    path = archive_path(service.runner.ledger, a["archive_id"])
    before = path.read_bytes(), path.stat().st_mtime_ns
    repeated = retain(service, source, row)
    assert repeated["replayed"] and repeated["archive_digest"] == a["archive_digest"]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    with pytest.raises(CollaborationConflict, match="合同"):
        retain(service, source, row, checkpoint_digest="f" * 64)
    release(service, source, a)
    runner = TaskRunner(str(tmp_path), lambda *args: None)
    resumed = DispatchService(
        str(tmp_path), runner, execute=lambda *args: None, validate_agent=lambda _: None
    )
    assert release(resumed, source, a)["replayed"]
    assert retain(resumed, source, row)["replayed"]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before and not calls


@pytest.mark.parametrize("change", ["result", "owner", "round", "delete"])
def test_archive_observation_preserves_original_version_and_records_invalidation(tmp_path, change):
    service, source, _, calls = fixture(tmp_path, children=0)
    finish(service, source)
    row = active(service, source)
    ledger = service.runner.ledger
    path = ledger._dir().parent / "task_scans" / (row["report"]["scan_id"] + ".json")
    before = path.read_bytes()
    if change == "result":
        source.result += " changed"
    if change == "owner":
        source.owner_session = "sid-other"
    if change == "round":
        source.collaboration["round"] = 2
    if change == "delete":
        ledger._path(source.id).unlink()
    else:
        assert ledger.save(source)
    a = retain(service, source, row)
    artifact = exported(service, source, a)
    assert artifact["evidence"]["checkpoint"]["valid"]
    assert a["report"]["coverage"]["state"] == "unknown" and not a["current_coverage"]
    assert path.read_bytes() == before
    release(service, source, a)
    assert verify_archive(exported(service, source, a)) and not calls


def test_archive_of_incomplete_checkpoint_is_historical_and_never_resume_authority(tmp_path):
    service, source, _, calls = fixture(tmp_path, fillers=600)
    report = step(service, source)
    a = retain(service, source)
    assert a["report"]["coverage"]["state"] == "incomplete" and a["report"]["next_cursor"] is None
    release(service, source, a)
    with pytest.raises(CollaborationConflict):
        step(service, source, report)
    assert (
        step(service, source, request_id="new")["coverage"]["state"] == "incomplete" and not calls
    )


def test_read_only_export_verify_and_list_do_not_observe_or_modify_tasks_or_active(tmp_path):
    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    a = retain(service, source)
    source.result += " later"
    service.runner.ledger.save(source)
    before = task_bytes(service)
    state = copy.deepcopy(service.runner.ledger.load_scan(a["scan_id"]))
    artifact = exported(service, source, a)
    assert verify_archive(artifact)[1]["valid"]  # Historical observation stays fixed.
    assert service.coverage_records("owner", source.id)["archives"][0]["report"]["complete"]
    assert service.runner.ledger.load_scan(a["scan_id"]) == state
    assert task_bytes(service) == before


def test_activity_or_observation_change_requires_new_archive_before_release(tmp_path):
    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    a = retain(service, source)
    service.dependency_scan_status("owner", source.id, 1, a["scan_id"])
    with pytest.raises(CollaborationConflict, match="已变化"):
        release(service, source, a)
    b = retain(service, source, request="new-archive")
    assert b["checkpoint_digest"] != a["checkpoint_digest"]
    assert release(service, source, b)["released"]
    assert len(service.coverage_records("owner", source.id)["archives"]) == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"session": "other"},
        {"round_number": 2},
        {"task_id": "task-other"},
        {"archive_digest": "f" * 64},
        {"checkpoint_digest": "f" * 64},
        {"scan_id": "scan-other"},
    ],
)
def test_cross_scope_and_wrong_digests_cannot_release(tmp_path, overrides):
    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    a = retain(service, source)
    with pytest.raises((ValueError, OSError)):
        release(service, source, a, **overrides)
    assert service.runner.ledger.load_scan(a["scan_id"])
    assert not service.coverage_records("other", source.id)["active"]
    assert not service.coverage_records("other", source.id)["archives"]


@pytest.mark.parametrize("failure", ["write", "replace", "sync", "readback"])
def test_archive_save_failures_never_release_active(tmp_path, monkeypatch, failure):
    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    row = active(service, source)
    before = copy.deepcopy(service.runner.ledger.load_scan(row["report"]["scan_id"]))
    with monkeypatch.context() as patch:
        if failure == "sync":
            patch.setattr(
                "src.gateway.task_scan.os.fsync",
                lambda _: (_ for _ in ()).throw(OSError("disk sync failed")),
            )
        elif failure == "write":
            original_open = os.open

            def fail_write(path, flags, *args):
                if flags & os.O_WRONLY:
                    raise OSError("archive write unavailable")
                return original_open(path, flags, *args)

            patch.setattr("src.gateway.task_scan.os.open", fail_write)
        elif failure == "replace":
            patch.setattr(
                Path, "replace", lambda *args: (_ for _ in ()).throw(OSError("replace failed"))
            )
        else:
            original = __import__("src.gateway.task_scan", fromlist=["load_archive"]).load_archive
            count = 0

            def readback(*args):
                nonlocal count
                count += 1
                if count > 1:
                    raise OSError("readback failed")
                return original(*args)

            patch.setattr("src.gateway.task_scan.load_archive", readback)
        with pytest.raises(OSError):
            retain(service, source, row)
    assert service.runner.ledger.load_scan(row["report"]["scan_id"]) == before
    a = retain(service, source, row)
    assert release(service, source, a)["released"]


def test_release_unlink_or_directory_sync_failure_retry_keeps_archive(tmp_path, monkeypatch):
    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    a = retain(service, source)
    original = Path.unlink
    with monkeypatch.context() as patch:
        patch.setattr(
            Path,
            "unlink",
            lambda self, *args, **kwargs: (
                (_ for _ in ()).throw(OSError("unlink failed"))
                if self.name == a["scan_id"] + ".json"
                else original(self, *args, **kwargs)
            ),
        )
        with pytest.raises(OSError):
            release(service, source, a)
    assert service.runner.ledger.load_scan(a["scan_id"]) and exported(service, source, a)
    sync = __import__("src.gateway.task_scan", fromlist=["_sync_directory"])._sync_directory

    def fail_active(path):
        if path.name == "task_scans":
            raise OSError("active directory sync failed")
        sync(path)

    # The archive parent sync occurs before unlink, so this is a pre-release failure.
    with monkeypatch.context() as patch:
        patch.setattr("src.gateway.task_scan._sync_directory", fail_active)
        with pytest.raises(OSError):
            release(service, source, a)
    assert service.runner.ledger.load_scan(a["scan_id"])
    assert release(service, source, a)["released"] and exported(service, source, a)


@pytest.mark.parametrize("damage", ["json", "checksum", "chain", "range", "symlink", "oversize"])
def test_damaged_history_cannot_free_active_or_claim_verified(tmp_path, monkeypatch, damage):
    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    a = retain(service, source)
    artifact = exported(service, source, a)
    path = archive_path(service.runner.ledger, a["archive_id"])
    if damage == "json":
        path.write_text("{")
    elif damage == "symlink":
        path.unlink()
        path.symlink_to(service.runner.ledger._path(source.id))
    elif damage == "oversize":
        monkeypatch.setattr("src.gateway.task_scan.MAX_ARCHIVE_BYTES", 32)
    else:
        changed = copy.deepcopy(artifact)
        if damage == "checksum":
            changed["evidence"]["checkpoint"]["offset"] = 0
        else:
            state = changed["evidence"]["checkpoint"]
            if damage == "chain":
                state["receipts"][0]["outcome"] = "f" * 64
            else:
                state["receipts"][0]["after"][1] = -1
            changed["evidence"]["checkpoint_digest"] = digest(state)
            changed["sha256"] = digest(changed["evidence"])
        path.write_text(json.dumps(changed))
    with pytest.raises(OSError):
        release(service, source, a)
    row = service.coverage_records("owner", source.id)["archives"][0]
    assert not row["verified"] and row["state"] == "unknown"
    assert service.runner.ledger.load_scan(a["scan_id"])


def test_corrupt_active_still_consumes_capacity_without_delete_path(tmp_path):
    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    row = active(service, source)
    path = service.runner.ledger._dir().parent / "task_scans" / f"{row['report']['scan_id']}.json"
    path.write_text("{")
    result = service.coverage_records("owner", source.id)
    assert result["capacity"]["unreadable_active"] == 1 and result["capacity"]["active_used"] == 1
    assert not result["active"]
    with pytest.raises(OSError):
        retain(service, source, row)
    assert path.read_text() == "{"


def test_bounded_history_capacity_and_pages_no_silent_eviction(tmp_path):
    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    row = active(service, source)
    for i in range(128):
        retain(service, source, row, request=f"archive-{i}")
    first = service.coverage_records("owner", source.id)
    last = service.coverage_records("owner", source.id, offset=112)
    assert len(first["archives"]) == 16 and first["next_offset"] == 16
    assert len(last["archives"]) == 16 and last["next_offset"] is None
    assert first["capacity"]["active_used"] == 1 and first["capacity"]["archives_used"] == 128
    with pytest.raises(CollaborationConflict, match="上限"):
        retain(service, source, row, request="overflow")
    assert len(service.runner.ledger.scan_archive_ids()) == 128


def test_export_workspace_mismatch_or_wrong_source_rejected_without_live_reads(tmp_path):
    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    a = retain(service, source)
    artifact = exported(service, source, a)
    other = DispatchService(
        str(tmp_path / "other"),
        TaskRunner(str(tmp_path / "other"), lambda *args: None),
        execute=lambda *args: None,
        validate_agent=lambda _: None,
    )
    with pytest.raises(CollaborationConflict):
        other.export_coverage("owner", source.id, 1, a["archive_id"], artifact)
    with pytest.raises(CollaborationConflict):
        service.export_coverage("other", source.id, 1, a["archive_id"], artifact)
    with pytest.raises(OSError):
        service.export_coverage(
            "owner", source.id, 1, a["archive_id"], {"archive_id": a["archive_id"]}
        )


@pytest.mark.asyncio
async def test_typed_http_auth_archive_export_verify_release(tmp_path, monkeypatch):
    import httpx
    from src.web.server import app

    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    monkeypatch.setattr("src.web.routers.delegations.get_dispatch_service", lambda: service)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "local-retention-test")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        root = f"/api/delegations/{source.id}"
        assert (await client.get(root + "/coverage-records?session=owner")).status_code == 401
        client.headers["Authorization"] = "Bearer local-retention-test"
        records = (await client.get(root + "/coverage-records?session=owner")).json()
        row = records["active"][0]
        path = root + "/dependency-scans/" + row["report"]["scan_id"]
        body = {
            "session": "owner",
            "round": 1,
            "request_id": "a",
            "checkpoint_digest": row["checkpoint_digest"],
        }
        assert (
            await client.post(path + "/archive", json={**body, "delete": True})
        ).status_code == 422
        a = (await client.post(path + "/archive", json=body)).json()
        assert a["verified"]
        artifact = (
            await client.get(
                root + "/coverage-archives/" + a["archive_id"] + "/export?session=owner&round=1"
            )
        ).json()
        assert (
            await client.post(
                root + "/coverage-archives/" + a["archive_id"] + "/verify",
                json={"session": "owner", "round": 1, "artifact": artifact},
            )
        ).json()["current_coverage"] is False
        assert (
            await client.get(
                root + "/coverage-archives/" + a["archive_id"] + "/verify?session=other&round=1"
            )
        ).status_code == 409
        result = await client.post(
            path + "/release",
            json={
                k: v
                for k, v in {**body, **a}.items()
                if k in {"session", "round", "archive_id", "archive_digest", "checkpoint_digest"}
            },
        )
        assert result.status_code == 200 and result.json()["released"]


def test_post_unlink_sync_failure_can_be_confirmed_after_restart(tmp_path, monkeypatch):
    service, source, _, _ = fixture(tmp_path, children=0)
    finish(service, source)
    a = retain(service, source)
    sync = __import__("src.gateway.task_scan", fromlist=["_sync_directory"])._sync_directory
    def fail_after_unlink(path):
        if path.name == "task_scans" and service.runner.ledger.load_scan(a["scan_id"]) is None:
            raise OSError("post unlink directory sync failed")
        sync(path)

    with monkeypatch.context() as patch:
        patch.setattr("src.gateway.task_scan._sync_directory", fail_after_unlink)
        with pytest.raises(OSError):
            release(service, source, a)
    assert service.runner.ledger.load_scan(a["scan_id"]) is None
    assert exported(service, source, a)
    assert release(service, source, a)["replayed"]
