"""Bounded snapshot/checkpoint storage owned by TaskLedger, never a task database.

Validity means matching observed namespace and lstat identities. The process lock
serializes our writers; this is not a filesystem transaction against other writers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from src.gateway.tasks import BackgroundTask, MAX_SCAN_RECORD_BYTES
from src.utils.ids import typed_id

MAX_INVENTORY = 8192
MAX_CHECKPOINT_BYTES = 8 * 1024 * 1024
MAX_CHECKPOINTS = 32
MAX_PAGE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def identity(value) -> list[int]:
    return [value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns]


def inventory(ledger) -> tuple[list, str]:
    entries = []
    try:
        with os.scandir(ledger._dir()) as iterator:
            for item in iterator:
                if len(entries) == MAX_INVENTORY:
                    return [], "inventory_budget"
                entries.append([item.name, identity(item.stat(follow_symlinks=False))])
    except FileNotFoundError:
        if entries or ledger._dir().exists():
            return [], "inventory_unreadable"
    except OSError:
        return [], "inventory_unreadable"
    entries.sort(key=lambda entry: entry[0].encode("utf-8", errors="surrogatepass"))
    return entries, ""


def read_record(ledger, entry) -> tuple[BackgroundTask | None, str, int]:
    name, expected = entry
    if not name.endswith(".json"):
        return None, "", 0
    if not stat.S_ISREG(expected[2]) or expected[3] > MAX_SCAN_RECORD_BYTES:
        return None, "record_unreadable", 0
    payload = b""
    try:
        descriptor = os.open(ledger._dir() / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            if identity(os.fstat(source.fileno())) != expected:
                return None, "snapshot_changed", 0
            payload = source.read(MAX_SCAN_RECORD_BYTES + 1)
            if identity(os.fstat(source.fileno())) != expected:
                return None, "snapshot_changed", len(payload)
        if len(payload) > MAX_SCAN_RECORD_BYTES:
            return None, "record_unreadable", len(payload)
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get("id") != name[:-5] or not typed_id(data.get("id"), "task"):
            return None, "record_damaged", len(payload)
        return BackgroundTask.from_dict(data), hashlib.sha256(payload).hexdigest(), len(payload)
    except (OSError, ValueError, TypeError, RecursionError):
        return None, "record_damaged", len(payload)


def _directory(ledger) -> Path:
    return ledger._dir().parent / "task_scans"


def checkpoint_path(ledger, scan_id: str) -> Path:
    if not isinstance(scan_id, str) or not typed_id(scan_id, "scan") or len(scan_id) > 80:
        raise ValueError("无效覆盖扫描 ID")
    return _directory(ledger) / f"{scan_id}.json"


def load(ledger, scan_id: str) -> dict | None:
    return read_checkpoint(ledger, scan_id)[0]


def read_checkpoint(ledger, scan_id: str, *, max_bytes=MAX_CHECKPOINT_BYTES):
    """Return actual bytes read so explicit multi-record observations share a budget."""
    path = checkpoint_path(ledger, scan_id)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, 0
    payload = b""
    try:
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("not a checkpoint file")
            payload = source.read(min(max_bytes, MAX_CHECKPOINT_BYTES) + 1)
        if len(payload) > min(max_bytes, MAX_CHECKPOINT_BYTES):
            if max_bytes < MAX_CHECKPOINT_BYTES:
                raise ObservationBudget("checkpoint_byte_budget")
            raise ValueError("checkpoint exceeds byte budget")
        envelope = json.loads(payload)
        state = envelope["state"]
        if (type(envelope["version"]) is not int or envelope["version"] != 1 or not isinstance(state, dict) or state["scan_id"] != scan_id
                or envelope["sha256"] != digest(state)):
            raise ValueError("checkpoint integrity mismatch")
        return validate_state(state, scan_id), len(payload)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        unreadable = OSError("覆盖记录不可读取；状态未知，请使用新的扫描请求")
        unreadable.read_bytes = len(payload)
        raise unreadable from error


class ObservationBudget(Exception):
    pass


def validate_state(state, scan_id):
    import re
    # Strictly bound the collections before domain interpretation.
    required = {"scan_id", "scope", "nonce", "sequence", "entries", "valid", "phase", "offset", "bytes", "skipped",
                "damaged", "candidates", "selected", "processed", "visits", "graph_complete", "failures", "reasons",
                "last_tasks", "last_errors", "observed_at", "last_input", "snapshot_id", "evidence_digest", "receipts"}
    if (set(state) != required or not isinstance(state["scope"], list) or len(state["scope"]) != 6
            or not isinstance(state["nonce"], str) or not isinstance(state["observed_at"], str)
            or type(state["valid"]) is not bool or type(state["graph_complete"]) is not bool
            or state["phase"] not in ("records", "process", "finished")
            or any(type(state[key]) is not int or state[key] < 0 for key in (
                "sequence", "offset", "bytes", "skipped", "damaged", "processed", "visits", "failures"))
            or not isinstance(state["candidates"], dict) or not isinstance(state["entries"], list)
            or not isinstance(state["selected"], list) or len(state["selected"]) > 1024
            or state["offset"] > len(state["entries"]) or state["processed"] > len(state["selected"])
            or state["bytes"] > MAX_TOTAL_BYTES or state["visits"] > 8192
            or any(tid not in state["candidates"] for tid in state["selected"])
            or not isinstance(state["reasons"], list) or len(state["reasons"]) > 16
            or not isinstance(state["receipts"], list) or len(state["receipts"]) > 1025
            or len(state["receipts"]) != state["sequence"]
            or len(state["entries"]) > MAX_INVENTORY or len(state["candidates"]) > MAX_INVENTORY
            or any(not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[0], str)
                   or Path(entry[0]).name != entry[0] or len(entry[1]) != 6
                   or any(type(value) is not int for value in entry[1]) for entry in state["entries"])):
        raise ValueError("checkpoint shape mismatch")
    if (state["entries"] != sorted(state["entries"], key=lambda entry: entry[0].encode("utf-8", errors="surrogatepass"))
            or len({entry[0] for entry in state["entries"]}) != len(state["entries"])
            or any(not typed_id(tid, "task") or not isinstance(value, dict)
                   or set(value) != {"owner", "round", "status", "resolution", "parents", "damaged", "sha256"}
                   or not isinstance(value["owner"], str) or type(value["round"]) is not int
                   or type(value["damaged"]) is not bool or not isinstance(value["sha256"], str)
                   or not isinstance(value["parents"], list) or len(value["parents"]) > 16
                   or any(not isinstance(parent, str) or not typed_id(parent, "task") for parent in value["parents"])
                   for tid, value in state["candidates"].items())):
        raise ValueError("checkpoint references mismatch")
    scope = state["scope"]
    if (not isinstance(scope[0], str) or not typed_id(scope[0], "sid") or not typed_id(scope[1], "task")
            or type(scope[2]) is not int or not 1 <= scope[2] <= 3
            or not isinstance(scope[3], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", scope[3])
            or scope[4] not in ("discover", "reconcile") or type(scope[5]) is not int or not 1 <= scope[5] <= 512
            or any(not isinstance(state[key], str) or not re.fullmatch(r"[a-f0-9]{64}", state[key])
                   for key in ("snapshot_id", "evidence_digest"))):
        raise ValueError("checkpoint scope mismatch")
    evidence = state["snapshot_id"]
    previous = None
    for index, receipt in enumerate(state["receipts"], 1):
        if (not isinstance(receipt, dict) or set(receipt) != {"sequence", "before", "after", "outcome"}
                or type(receipt["sequence"]) is not int or receipt["sequence"] != index
                or not isinstance(receipt["outcome"], str) or not re.fullmatch(r"[a-f0-9]{64}", receipt["outcome"])):
            raise ValueError("checkpoint receipt order mismatch")
        for point in (receipt["before"], receipt["after"]):
            if (not isinstance(point, list) or len(point) != 4 or point[0] not in ("records", "process", "finished")
                    or type(point[1]) is not int or not 0 <= point[1] <= len(state["entries"])
                    or type(point[2]) is not int or not 0 <= point[2] <= len(state["selected"])
                    or not isinstance(point[3], str) or not re.fullmatch(r"[a-f0-9]{64}", point[3])):
                raise ValueError("checkpoint receipt range mismatch")
        if (previous is not None and receipt["before"] != previous
                or receipt["after"][1] < receipt["before"][1] or receipt["after"][2] < receipt["before"][2]):
            raise ValueError("checkpoint receipt discontinuity")
        previous = receipt["after"]
        evidence = digest([evidence, receipt])
    if (evidence != state["evidence_digest"] or previous is not None
            and previous[1:] != [state["offset"], state["processed"], digest(state["entries"])]):
        raise ValueError("checkpoint cumulative evidence mismatch")
    return state


def save(ledger, state: dict) -> None:
    from src.utils.state_dir import ensure_state_gitignore
    path = checkpoint_path(ledger, state["scan_id"])
    payload = json.dumps({"version": 1, "state": state, "sha256": digest(state)}, ensure_ascii=True).encode()
    if len(payload) > MAX_CHECKPOINT_BYTES:
        raise OSError("覆盖记录超过保存上限；本次进度未确认")
    ensure_state_gitignore(ledger.repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def ids(ledger) -> list[str]:
    values, scanned = [], 0
    try:
        with os.scandir(_directory(ledger)) as iterator:
            for item in iterator:
                scanned += 1
                if scanned > MAX_CHECKPOINTS * 2:
                    raise OSError("覆盖记录目录超过上限；状态未知")
                if item.name.endswith(".json"):
                    values.append(item.name[:-5])
    except FileNotFoundError:
        pass
    return sorted(values)

# Immutable evidence files remain in the same ledger-owned coverage directory.
MAX_ARCHIVES = 128
MAX_ARCHIVE_BYTES = MAX_CHECKPOINT_BYTES + 65536
ARCHIVE_PAGE_SIZE = 16


def workspace_id(ledger):
    return digest(str(Path(ledger.repo_root).resolve()))


def _archive_group(workspace, owner, source):
    return digest([workspace, owner, source])[:24]


def archive_path(ledger, archive_id):
    import re
    if not isinstance(archive_id, str) or not re.fullmatch(r"archive-[a-f0-9]{24}-[a-f0-9]{24}", archive_id):
        raise ValueError("无效覆盖归档 ID")
    return _directory(ledger) / "archives" / f"{archive_id}.json"


def archive_ids(ledger):
    values, count = [], 0
    try:
        with os.scandir(_directory(ledger) / "archives") as entries:
            for item in entries:
                count += 1
                if count > MAX_ARCHIVES * 2:
                    raise OSError("历史覆盖目录超过上限；状态未知")
                if item.name.endswith(".json"):
                    archive_path(ledger, item.name[:-5])
                    values.append(item.name[:-5])
    except FileNotFoundError:
        pass
    return sorted(values)


def verify_archive(artifact):
    """Pure bounded integrity check, without a ledger read or current validity claim."""
    from copy import deepcopy
    from datetime import datetime
    import re
    try:
        if len(json.dumps(artifact, ensure_ascii=True).encode()) > MAX_ARCHIVE_BYTES:
            raise ValueError("archive exceeds byte budget")
        if (not isinstance(artifact, dict) or set(artifact) != {"version", "kind", "archive_id", "sha256", "evidence"}
                or type(artifact["version"]) is not int or artifact["version"] != 1
                or artifact["kind"] != "task_coverage_archive" or artifact["sha256"] != digest(artifact["evidence"])):
            raise ValueError("archive checksum mismatch")
        evidence = artifact["evidence"]
        if set(evidence) != {"workspace_id", "scope", "scan_id", "checkpoint_digest", "checkpoint",
                             "observation", "archived_at", "request_id"}:
            raise ValueError("archive scope mismatch")
        state = validate_state(evidence["checkpoint"], evidence["scan_id"])
        if (evidence["scope"] != state["scope"] or evidence["checkpoint_digest"] != digest(state)
                or not re.fullmatch(r"[a-f0-9]{64}", evidence["workspace_id"])
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", evidence["request_id"])):
            raise ValueError("archive reference mismatch")
        group = _archive_group(evidence["workspace_id"], *state["scope"][:2])
        expected = "archive-" + group + "-" + digest([state["scope"][:3], state["scan_id"], evidence["request_id"]])[:24]
        if artifact["archive_id"] != expected:
            raise ValueError("archive identity mismatch")
        observation = evidence["observation"]
        if (set(observation) != {"valid", "phase", "reasons", "observed_at"}
                or type(observation["valid"]) is not bool or observation["phase"] not in ("records", "process", "finished")
                or not isinstance(observation["reasons"], list) or len(observation["reasons"]) > 16
                or not all(isinstance(reason, str) for reason in observation["reasons"])
                or not set(state["reasons"]).issubset(observation["reasons"])
                or observation["valid"] and not state["valid"]
                or observation["valid"] and (observation["phase"] != state["phase"] or observation["reasons"] != state["reasons"])
                or not observation["valid"] and observation["phase"] != "finished"):
            raise ValueError("archive observation mismatch")
        for timestamp in (evidence["archived_at"], observation["observed_at"], state["observed_at"]):
            if datetime.fromisoformat(timestamp).tzinfo is None:
                raise ValueError("archive timestamp missing timezone")
        observed = deepcopy(state)
        observed.update(observation)
        return evidence, observed
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise OSError("历史覆盖资料损坏或不可核验；状态未知，不能释放活动检查点") from error


def load_archive(ledger, archive_id):
    path = archive_path(ledger, archive_id)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("not an archive file")
            payload = source.read(MAX_ARCHIVE_BYTES + 1)
        if len(payload) > MAX_ARCHIVE_BYTES:
            raise ValueError("archive byte budget")
        artifact = json.loads(payload)
        verify_archive(artifact)
        if artifact["archive_id"] != archive_id:
            raise ValueError("archive filename mismatch")
        return artifact
    except (OSError, ValueError, TypeError, RecursionError) as error:
        raise OSError("历史覆盖资料不可读取；状态未知，不能释放活动检查点") from error


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_archive(ledger, artifact):
    import secrets
    verify_archive(artifact)
    path = archive_path(ledger, artifact["archive_id"])
    if path.exists() or path.is_symlink():
        existing = load_archive(ledger, artifact["archive_id"])
        if existing != artifact:
            raise OSError("已有历史归档不能覆盖")
        _sync_directory(path.parent)
        _sync_directory(path.parent.parent)
        return
    if len(archive_ids(ledger)) >= MAX_ARCHIVES:
        from src.gateway.collaboration import CollaborationConflict
        raise CollaborationConflict("历史归档已达到 128 个上限；保留现有资料，可只读导出；未释放活动容量")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(json.dumps(artifact, ensure_ascii=True).encode())
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(path)
        _sync_directory(path.parent)
        _sync_directory(path.parent.parent)
        if load_archive(ledger, artifact["archive_id"]) != artifact:
            raise OSError("历史归档回读核验失败；未释放容量")
    finally:
        temporary.unlink(missing_ok=True)


def scan_was_archived(ledger, scan_id, owner, source):
    if load_retirement(ledger, scan_id) is not None:
        # A retirement never becomes reusable, even after history moves elsewhere.
        return True
    # Bounded filename prefix lookup plus verified evidence; no persistent index.
    group = "archive-" + _archive_group(workspace_id(ledger), owner, source) + "-"
    for archive_id in archive_ids(ledger):
        if not archive_id.startswith(group):
            continue
        artifact = load_archive(ledger, archive_id)
        if artifact["evidence"]["scan_id"] == scan_id:
            return True
    return False


def archive_view(artifact, *, replayed=False):
    from src.gateway.task_dependents import _coverage_report
    evidence, state = verify_archive(artifact)
    report = _coverage_report(state)
    report["next_cursor"] = None
    return {"archive_id": artifact["archive_id"], "archive_digest": artifact["sha256"],
            "scan_id": evidence["scan_id"], "owner_session": evidence["scope"][0],
            "source_task_id": evidence["scope"][1], "source_round": evidence["scope"][2],
            "checkpoint_digest": evidence["checkpoint_digest"], "workspace_id": evidence["workspace_id"],
            "request_id": evidence["request_id"], "archived_at": evidence["archived_at"],
            "historical": True, "current_coverage": False, "resumable": False, "verified": True,
            "integrity": "sha256_and_receipt_chain", "replayed": replayed, "report": report}


def _archive_scope(ledger, artifact, owner, source, round_number):
    from src.gateway.collaboration import CollaborationConflict
    evidence, _ = verify_archive(artifact)
    if evidence["workspace_id"] != workspace_id(ledger) or evidence["scope"][:3] != [owner, source, round_number]:
        raise CollaborationConflict("历史覆盖归属、来源、轮次或工作区不匹配")
    return evidence


def retain(service, session, source, round_number, scan_id, request_id, checkpoint_digest):
    from copy import deepcopy
    import re
    from src.gateway.dispatch import session_identity
    from src.gateway.collaboration import CollaborationConflict
    from src.gateway.tasks import TASK_STATE_LOCK
    from src.gateway.task_dependents import _coverage_time, _coverage_validity, _coverage_invalidate
    owner, ledger = session_identity(session), service.runner.ledger
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", request_id):
        raise ValueError("无效归档请求 ID")
    group = _archive_group(workspace_id(ledger), owner, source)
    archive_id = "archive-" + group + "-" + digest([[owner, source, round_number], scan_id, request_id])[:24]
    with TASK_STATE_LOCK:
        existing = load_archive(ledger, archive_id)
        if existing:
            evidence = _archive_scope(ledger, existing, owner, source, round_number)
            if evidence["checkpoint_digest"] != checkpoint_digest or evidence["scan_id"] != scan_id:
                raise CollaborationConflict("归档请求合同已变化")
            save_archive(ledger, existing)  # Also confirm durability after an interrupted prior sync.
            return archive_view(existing, replayed=True)
        state = ledger.load_scan(scan_id)
        if state is None or state["scope"][:3] != [owner, source, round_number]:
            raise CollaborationConflict("活动覆盖不存在或归属不匹配")
        if digest(state) != checkpoint_digest:
            raise CollaborationConflict("活动检查点版本已变化；请加载后保留当前版本")
        observed = deepcopy(state)
        try:
            service._dependency_source(session, source, round_number)
        except (ValueError, TypeError, KeyError, AttributeError):
            _coverage_invalidate(observed, "source_identity_changed")
        if observed["valid"]:
            _coverage_validity(ledger, observed)
        observed["observed_at"] = _coverage_time()
        evidence = {"workspace_id": workspace_id(ledger), "scope": state["scope"], "scan_id": scan_id,
                    "checkpoint_digest": checkpoint_digest, "checkpoint": state,
                    "observation": {key: observed[key] for key in ("valid", "phase", "reasons", "observed_at")},
                    "archived_at": observed["observed_at"], "request_id": request_id}
        artifact = {"version": 1, "kind": "task_coverage_archive", "archive_id": archive_id,
                    "sha256": digest(evidence), "evidence": evidence}
        save_archive(ledger, artifact)
        return archive_view(artifact)


def release(service, session, source, round_number, scan_id, archive_id, archive_digest, checkpoint_digest):
    from src.gateway.dispatch import session_identity
    from src.gateway.collaboration import CollaborationConflict
    from src.gateway.tasks import TASK_STATE_LOCK
    ledger, owner = service.runner.ledger, session_identity(session)
    with TASK_STATE_LOCK:
        artifact = load_archive(ledger, archive_id)
        if artifact is None:
            raise CollaborationConflict("历史归档不存在；不能释放活动容量")
        evidence = _archive_scope(ledger, artifact, owner, source, round_number)
        if (artifact["sha256"] != archive_digest or evidence["checkpoint_digest"] != checkpoint_digest
                or evidence["scan_id"] != scan_id):
            raise CollaborationConflict("归档摘要或活动版本不匹配；未释放容量")
        state = ledger.load_scan(scan_id)
        if state is not None and (state["scope"][:3] != [owner, source, round_number] or digest(state) != checkpoint_digest):
            raise CollaborationConflict("活动检查点已变化；需先保留新版本")
        save_archive(ledger, artifact)
        retirement = load_retirement(ledger, scan_id)
        if retirement is not None:
            _retirement_scope(ledger, retirement, owner, source, round_number)
            if retirement["evidence"]["scope"] != evidence["scope"] or (state is not None
                    and retirement["evidence"]["checkpoint_digest"] != checkpoint_digest):
                raise CollaborationConflict("退役身份或原释放版本不匹配；未释放容量")
        else:
            from src.gateway.task_dependents import _coverage_time
            view = archive_view(artifact)
            retired = {"workspace_id": evidence["workspace_id"], "scope": evidence["scope"], "scan_id": scan_id,
                       "archive_id": archive_id, "archive_digest": archive_digest, "checkpoint_digest": checkpoint_digest,
                       "archived_at": evidence["archived_at"], "coverage": _preservation_summary(view),
                       "retired_at": _coverage_time(), "reason": "explicit_active_checkpoint_release" if state is not None
                       else "explicit_legacy_identity_retirement"}
            retirement = {"version": 1, "kind": "task_coverage_retirement", "retirement_id": "retired-" + scan_id[5:],
                          "sha256": digest(retired), "evidence": retired}
        save_retirement(ledger, retirement)  # Must succeed before unlink; retries never replace identity or time.
        path = checkpoint_path(ledger, scan_id)
        if state is not None:
            path.unlink()
        _sync_directory(path.parent)
        return {**archive_view(artifact), "released": True, "replayed": state is None,
                "retirement": retirement_view(retirement)}


def records(service, session, source, *, offset=0, retirement_offset=0):
    from src.gateway.dispatch import session_identity
    from src.gateway.tasks import TASK_STATE_LOCK
    if (not typed_id(source, "task") or type(offset) is not int or not 0 <= offset <= MAX_ARCHIVES
            or type(retirement_offset) is not int or not 0 <= retirement_offset <= MAX_RETIREMENTS):
        raise ValueError("无效覆盖资料列表范围")
    ledger, owner = service.runner.ledger, session_identity(session)
    with TASK_STATE_LOCK:
        active_ids, historical_ids, retired_ids = ledger.scan_ids(), archive_ids(ledger), retirement_ids(ledger)
        active, unreadable = [], 0
        for scan_id in active_ids:
            try:
                state = ledger.load_scan(scan_id)
                if state and state["scope"][:2] == [owner, source]:
                    from src.gateway.task_dependents import _coverage_report
                    retirement = retirement_status(ledger, scan_id, owner, source, state["scope"][2])
                    report = _coverage_report(state)
                    if retirement["state"] != "not_recorded":
                        report.update(complete=False, snapshot_valid=False, next_cursor=None)
                        report["coverage"] = {**report["coverage"], "state": "unknown", "reasons": ["scan_retired_pending_release"
                                              if retirement["state"] == "retired" else "retirement_unreadable"]}
                    active.append({"checkpoint_digest": digest(state), "report": report,
                                   "owner_session": owner, "historical": False, "retirement": retirement})
            except (OSError, ValueError):
                unreadable += 1
        group = "archive-" + _archive_group(workspace_id(ledger), owner, source) + "-"
        matching = [value for value in historical_ids if value.startswith(group)]
        history = []
        for archive_id in matching[offset:offset + ARCHIVE_PAGE_SIZE]:
            try:
                artifact = load_archive(ledger, archive_id)
                _archive_scope(ledger, artifact, owner, source, artifact["evidence"]["scope"][2])
                view = archive_view(artifact)
                view["retirement"] = retirement_status(ledger, view["scan_id"], owner, source, view["source_round"])
                history.append(view)
            except (OSError, ValueError):
                history.append({"archive_id": archive_id, "historical": True, "current_coverage": False,
                                "verified": False, "state": "unknown"})
        next_offset = offset + ARCHIVE_PAGE_SIZE if offset + ARCHIVE_PAGE_SIZE < len(matching) else None
        retired, unreadable_retired = [], 0
        for scan_id in retired_ids:
            try:
                record = load_retirement(ledger, scan_id)
                if record is None:
                    raise OSError("retirement disappeared")
                e = record["evidence"]
                if e["workspace_id"] == workspace_id(ledger) and e["scope"][:2] == [owner, source]:
                    retired.append(retirement_view(record))
            except (ValueError, OSError):
                unreadable_retired += 1
        retired.sort(key=lambda row: row["scan_id"])
        next_retirement_offset = retirement_offset + ARCHIVE_PAGE_SIZE if retirement_offset + ARCHIVE_PAGE_SIZE < len(retired) else None
        return {"owner_session": owner, "source_task_id": source, "workspace_id": workspace_id(ledger),
                "capacity": {"active_used": len(active_ids), "active_limit": MAX_CHECKPOINTS,
                             "archives_used": len(historical_ids), "archives_limit": MAX_ARCHIVES,
                             "retired_used": len(retired_ids), "retired_limit": MAX_RETIREMENTS,
                             "unreadable_retired": unreadable_retired,
                             "unreadable_active": unreadable}, "active": active, "archives": history,
                "offset": offset, "next_offset": next_offset, "archive_count": len(matching),
                "retirements": retired[retirement_offset:retirement_offset + ARCHIVE_PAGE_SIZE],
                "retirement_offset": retirement_offset, "next_retirement_offset": next_retirement_offset,
                "retirement_count": len(retired)}


MAX_OBSERVATIONS = 4
MAX_OBSERVATION_BYTES = 16 * 1024 * 1024


def observe(service, session, source, *, workspace, request_id, checkpoints):
    """Explicit bounded validity observation. Never consumes pages or touches tasks.

    Versions are fixed by the caller. Failures are per record, not an atomic batch;
    repeats after a lost response require reloading versions, not guessed progress.
    """
    import re
    from src.gateway.dispatch import session_identity
    from src.gateway.collaboration import CollaborationConflict
    from src.gateway.tasks import TASK_STATE_LOCK
    from src.gateway.task_dependents import _coverage_invalidate, _coverage_report, _coverage_time
    ledger, owner = service.runner.ledger, session_identity(session)
    if (not typed_id(source, "task") or not isinstance(request_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", request_id)
            or not isinstance(checkpoints, list) or not 1 <= len(checkpoints) <= MAX_OBSERVATIONS):
        raise ValueError("无效观察范围；每次显式选择 1 至 4 项活动检查点")
    for ref in checkpoints:
        if (not isinstance(ref, dict) or set(ref) != {"scan_id", "round", "checkpoint_digest", "snapshot_id", "sequence"}
                or not isinstance(ref["scan_id"], str) or not re.fullmatch(r"scan-[a-f0-9]{24}", ref["scan_id"])
                or type(ref["round"]) is not int or not 1 <= ref["round"] <= 3
                or type(ref["sequence"]) is not int or not 0 <= ref["sequence"] <= 1025
                or any(not isinstance(ref[key], str) or not re.fullmatch(r"[a-f0-9]{64}", ref[key])
                       for key in ("checkpoint_digest", "snapshot_id"))):
            raise ValueError("无效活动观察合同")
    if len({ref["scan_id"] for ref in checkpoints}) != len(checkpoints):
        raise ValueError("观察范围不能重复")
    with TASK_STATE_LOCK:
        if workspace != workspace_id(ledger):
            raise CollaborationConflict("观察工作区不匹配")
        states, results, read_bytes = [], [], 0
        # Preflight all readable identities before any save. A foreign ref cannot
        # cause an earlier owned ref to be mutated as a side effect of rejection.
        for ref in checkpoints:
            row = {"reference": ref, "state": "not_observed", "persisted": False, "reason": ""}
            results.append(row)
            if read_bytes >= MAX_OBSERVATION_BYTES:
                row["reason"] = "checkpoint_byte_budget"
                continue
            try:
                retirement = load_retirement(ledger, ref["scan_id"])
                if retirement is not None:
                    _retirement_scope(ledger, retirement, owner, source, ref["round"])
                    row["reason"] = "scan_retired"
                    continue
            except OSError:
                row["reason"] = "retirement_unreadable"
                continue
            try:
                state, used = ledger.read_scan_checkpoint(ref["scan_id"], max_bytes=MAX_OBSERVATION_BYTES - read_bytes)
                read_bytes += used
            except ObservationBudget:
                read_bytes = MAX_OBSERVATION_BYTES + 1  # One bounded overflow detection byte.
                row["reason"] = "checkpoint_byte_budget"
                continue
            except (OSError, ValueError) as error:
                read_bytes += getattr(error, "read_bytes", 0)
                row["reason"] = "checkpoint_unreadable"
                continue
            if state is None:
                row["reason"] = "checkpoint_missing"
                continue
            if state["scope"][:3] != [owner, source, ref["round"]]:
                raise CollaborationConflict("活动观察归属、来源或轮次不匹配")
            if (digest(state) != ref["checkpoint_digest"] or state["snapshot_id"] != ref["snapshot_id"]
                    or state["sequence"] != ref["sequence"]):
                row["reason"] = "checkpoint_changed"
                continue
            states.append((state, row))
        before, before_reason = ledger.scan_inventory() if states else ([], "not_observed")
        after, after_reason = ledger.scan_inventory() if states else ([], "not_observed")
        stable = not before_reason and not after_reason and before == after
        observed_at = _coverage_time()
        for state, row in states:
            if not stable:
                _coverage_invalidate(state, before_reason or after_reason or "snapshot_changed")
            elif before != state["entries"]:
                _coverage_invalidate(state, "snapshot_changed")
            # Previously invalidated evidence stays latched, even after restoration.
            state["observed_at"] = observed_at
            try:
                ledger.save_scan(state)
            except OSError:
                row["reason"] = "checkpoint_save_failed"
                continue
            row.update(state="observed", persisted=True, checkpoint_digest=digest(state), report=_coverage_report(state))
        observed = sum(row["persisted"] for row in results)
        return {"request_id": request_id, "owner_session": owner, "source_task_id": source,
                "workspace_id": workspace, "observed_at": observed_at, "results": results,
                "requested": len(checkpoints), "observed": observed, "unobserved": len(checkpoints) - observed,
                "observation_state": "observed" if observed == len(checkpoints) else "partial" if observed else "unknown",
                "task_graph_reconciled": False, "pages_advanced": 0,
                "namespace": {"stable": stable, "before_digest": digest(before) if not before_reason else None,
                              "after_digest": digest(after) if not after_reason else None,
                              "reason": before_reason or after_reason or ("" if stable else "snapshot_changed")},
                "limits": {"checkpoints": MAX_OBSERVATIONS, "checkpoint_bytes": MAX_OBSERVATION_BYTES,
                           "inventory_entries": MAX_INVENTORY}, "checkpoint_bytes_read": read_bytes}


def export(service, session, source, round_number, archive_id, artifact=None):
    from src.gateway.dispatch import session_identity
    from src.gateway.collaboration import CollaborationConflict
    from src.gateway.tasks import TASK_STATE_LOCK
    ledger = service.runner.ledger
    with TASK_STATE_LOCK:
        artifact = artifact if artifact is not None else load_archive(ledger, archive_id)
        if artifact is None:
            raise CollaborationConflict("历史归档不存在")
        verify_archive(artifact)
        if artifact["archive_id"] != archive_id:
            raise CollaborationConflict("归档身份不匹配")
        _archive_scope(ledger, artifact, session_identity(session), source, round_number)
        return artifact


def _strict_json(payload, maximum):
    if not isinstance(payload, bytes) or not 0 < len(payload) <= maximum:
        raise ValueError("文件为空或超过字节上限；未确认文件")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("non-finite JSON number")

    def floating(value):
        import math
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite JSON exponent")
        return number

    try:
        text = payload.decode("utf-8", errors="strict")
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=floating)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("历史文件不是有效的 UTF-8 JSON；状态未知") from error


def verify_file_bytes(payload: bytes, *, expected=None):
    """Verify a portable copy, never import it or attest to durable external storage."""
    from src.gateway.collaboration import CollaborationConflict
    from src.gateway.task_dependents import _coverage_time
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_ARCHIVE_BYTES:
        raise ValueError("历史文件为空或超过 8454144 字节上限；未确认文件")
    expected = {} if expected is None else expected
    allowed = {"archive_id", "archive_digest", "workspace_id", "owner_session", "source_task_id", "source_round", "file_digest"}
    if not isinstance(expected, dict) or set(expected) - allowed:
        raise ValueError("无效历史文件核验合同")
    for key, value in expected.items():
        if key == "source_round":
            if type(value) is not int or not 1 <= value <= 3:
                raise ValueError("无效历史文件期望轮次")
        elif not isinstance(value, str) or not value:
            raise ValueError("无效历史文件期望身份")
    raw_digest = hashlib.sha256(payload).hexdigest()
    if "file_digest" in expected and expected["file_digest"] != raw_digest:
        raise CollaborationConflict("历史文件原文摘要不匹配；未确认文件")
    artifact = _strict_json(payload, MAX_ARCHIVE_BYTES)
    view = archive_view(artifact)
    for key, value in expected.items():
        if key != "file_digest" and view.get(key) != value:
            raise CollaborationConflict("历史文件归属、来源、轮次、工作区或归档版本不匹配")
    return {**view, "file_digest": raw_digest, "file_bytes": len(payload), "verified_at": _coverage_time(),
            "verification": "uploaded_or_read_bytes", "expected_binding_checked": sorted(expected),
            "durable_copy_confirmed": False, "capacity_released": False, "imported": False}


def _read_regular_file(path, maximum):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise ValueError("必须读取上限内的普通历史 JSON 文件；状态未知")
        payload = source.read(maximum + 1)
        if identity(before) != identity(os.fstat(source.fileno())) or identity(before) != identity(os.lstat(path)):
            raise OSError("历史文件在读取期间变化；状态未知")
    return payload


def verify_file_path(path, *, expected=None):
    """Offline, bounded regular-file read; no source ledger, network or logging writes."""
    return verify_file_bytes(_read_regular_file(path, MAX_ARCHIVE_BYTES), expected=expected)


def verify_scoped_file(service, session, source, round_number, archive_id, archive_digest, file_digest, payload):
    from src.gateway.dispatch import session_identity
    from src.gateway.tasks import TASK_STATE_LOCK
    with TASK_STATE_LOCK:
        return verify_file_bytes(payload, expected={"owner_session": session_identity(session), "source_task_id": source,
            "source_round": round_number, "workspace_id": workspace_id(service.runner.ledger), "archive_id": archive_id,
            "archive_digest": archive_digest, "file_digest": file_digest})


# Local preservation records are portable evidence, never a task index or a
# permission to retire ledger history. The HTTP path only checks supplied bytes.
MAX_PRESERVATIONS = 128
MAX_PRESERVATION_BYTES = 65536
MAX_PRESERVATION_REQUEST_BYTES = 2 * (MAX_ARCHIVE_BYTES + MAX_PRESERVATION_BYTES) + 1024


def _preservation_summary(view):
    report = view["report"]
    return {"snapshot_id": report["snapshot_id"], "evidence_digest": report["evidence_digest"],
            "sequence": report["sequence"], "receipt_count": report["receipt_count"],
            "observed_at": report["observed_at"], "state": report["coverage"]["state"],
            "snapshot_valid": report["snapshot_valid"], "complete": report["complete"],
            "reasons": report["coverage"]["reasons"]}


def _preservation_names(view):
    stem = view["archive_id"] + "." + view["file_digest"]
    return stem + ".archive.json", stem + ".preservation.json"


def verify_preservation_bytes(artifact_bytes, receipt_bytes, *, expected=None, receipt_file_digest=None):
    """A receipt is a local assertion, not a signed storage or durability proof."""
    from datetime import datetime
    from src.gateway.collaboration import CollaborationConflict
    view = verify_file_bytes(artifact_bytes, expected=expected)
    raw_digest = hashlib.sha256(receipt_bytes).hexdigest()
    if receipt_file_digest is not None and receipt_file_digest != raw_digest:
        raise CollaborationConflict("保存凭据原文摘要不匹配；状态未知")
    receipt = _strict_json(receipt_bytes, MAX_PRESERVATION_BYTES)
    try:
        if (not isinstance(receipt, dict) or set(receipt) != {"kind", "version", "receipt_id", "sha256", "evidence"}
                or receipt["kind"] != "task_coverage_preservation" or type(receipt["version"]) is not int
                or receipt["version"] != 1 or receipt["sha256"] != digest(receipt["evidence"])):
            raise ValueError("preservation checksum mismatch")
        evidence = receipt["evidence"]
        fields = {"archive_id", "archive_digest", "workspace_id", "owner_session", "source_task_id", "source_round",
                  "scan_id", "checkpoint_digest", "file_digest", "file_bytes"}
        if (set(evidence) != fields | {"coverage", "storage", "preserved_at"}
                or any(type(evidence[key]) is not type(view[key]) or evidence[key] != view[key] for key in fields)
                or digest(evidence["coverage"]) != digest(_preservation_summary(view))
                or datetime.fromisoformat(evidence["preserved_at"]).tzinfo is None):
            raise ValueError("preservation evidence mismatch")
        storage = evidence["storage"]
        directory = storage["directory"]
        if (set(storage) != {"directory", "artifact_name", "directory_device", "directory_inode", "artifact_inode", "method"}
                or not isinstance(directory, str) or len(directory) > 4096 or "\0" in directory
                or not Path(directory).is_absolute() or str(Path(directory)) != directory
                or any(part in Path(directory).parts for part in ("..", ".git", ".vortocode"))
                or storage["artifact_name"] != _preservation_names(view)[0]
                or storage["method"] != "local_file_fsync_directory_fsync_read_back"
                or any(not isinstance(storage[key], str) or not storage[key].isdigit() or len(storage[key]) > 32
                       for key in ("directory_device", "directory_inode", "artifact_inode"))
                or receipt["receipt_id"] != "preserve-" + digest([view["archive_id"], view["file_digest"], directory])[:24]):
            raise ValueError("preservation storage mismatch")
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise OSError("保存凭据损坏或与原文不一致；状态未知") from error
    return {**view, "preservation": {"receipt_id": receipt["receipt_id"], "receipt_digest": receipt["sha256"],
        "receipt_file_digest": raw_digest, "preserved_at": evidence["preserved_at"], "storage": storage,
        "state": "receipt_and_uploaded_bytes_consistent", "storage_observed_now": False,
        "independent_failure_domain_confirmed": False}}


def _preservation_directory(directory):
    path = Path(directory)
    if path.is_symlink():
        raise ValueError("保存目标不能是符号链接")
    path = path.resolve(strict=True)
    if any(part in path.parts for part in (".git", ".vortocode")):
        raise ValueError("保存目标须在 .git/.vortocode 之外的既有专用目录")
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    before = os.fstat(descriptor)
    return path, descriptor, before


def _preservation_directory_current(path, descriptor, before):
    current = os.stat(path, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino) or not stat.S_ISDIR(current.st_mode):
        raise OSError("保存目录身份已变化；状态未知，历史未释放")
    if (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino) != (before.st_dev, before.st_ino):
        raise OSError("保存目录描述符已变化")


def _read_preserved(descriptor, name, maximum, *, sync=False):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
    with os.fdopen(handle, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum or before.st_nlink != 1:
            raise OSError("保存文件不是上限内的独立普通文件")
        if sync:
            os.fsync(source.fileno())
        raw = source.read(maximum + 1)
        if (len(raw) > maximum or identity(before) != identity(os.fstat(source.fileno()))
                or identity(before) != identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False))):
            raise OSError("保存文件在读取期间变化；状态未知")
    return raw, before


def _publish_preserved(descriptor, name, raw):
    import secrets
    temporary = ".coverage-" + secrets.token_hex(12) + ".tmp"
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
    try:
        with os.fdopen(handle, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        try:
            os.link(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor, follow_symlinks=False)
        except FileExistsError:
            pass  # Existing evidence is checked below, never overwritten.
    finally:
        os.unlink(temporary, dir_fd=descriptor)
    os.fsync(descriptor)


def preserve_file_path(path, directory, *, expected=None):
    """Explicit local copy + fsync + reread. No ledger retirement or provider work."""
    import fcntl
    from src.gateway.tasks import TASK_STATE_LOCK
    from src.gateway.task_dependents import _coverage_time
    raw = _read_regular_file(path, MAX_ARCHIVE_BYTES)
    view = verify_file_bytes(raw, expected=expected)
    artifact_name, receipt_name = _preservation_names(view)
    with TASK_STATE_LOCK:
        location, descriptor, before = _preservation_directory(directory)
        try:
            handle = os.open(".coverage-preservation.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=descriptor)
            with os.fdopen(handle, "rb+") as lock:
                lock_info = os.fstat(lock.fileno())
                if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1:
                    raise OSError("保存目录锁不是独立普通文件")
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                _preservation_directory_current(location, descriptor, before)
                names = set()
                with os.scandir(descriptor) as entries:
                    for count, entry in enumerate(entries, 1):
                        if count > 512:
                            raise OSError("保存目录超过 512 项观察上限；状态未知")
                        names.add(entry.name)
                groups = {name.removesuffix(".archive.json").removesuffix(".preservation.json") for name in names
                          if name.endswith((".archive.json", ".preservation.json"))}
                stem = artifact_name.removesuffix(".archive.json")
                if len(groups) > MAX_PRESERVATIONS or stem not in groups and len(groups) >= MAX_PRESERVATIONS:
                    raise OSError("本地保留目录已达到 128 组上限；保留已有文件，不释放历史")
                if receipt_name in names and artifact_name not in names:
                    raise OSError("保存凭据的原文缺失；不自动重建或覆盖旧证据")
                if artifact_name not in names:
                    _publish_preserved(descriptor, artifact_name, raw)
                stored, artifact_info = _read_preserved(descriptor, artifact_name, MAX_ARCHIVE_BYTES, sync=True)
                if stored != raw:
                    raise OSError("已有保存原文不同或损坏；不能覆盖")
                os.fsync(descriptor)
                replayed = receipt_name in names
                if not replayed:
                    keys = ("archive_id", "archive_digest", "workspace_id", "owner_session", "source_task_id",
                            "source_round", "scan_id", "checkpoint_digest", "file_digest", "file_bytes")
                    evidence = {key: view[key] for key in keys}
                    evidence.update(coverage=_preservation_summary(view), preserved_at=_coverage_time(), storage={
                        "directory": str(location), "artifact_name": artifact_name, "directory_device": str(before.st_dev),
                        "directory_inode": str(before.st_ino), "artifact_inode": str(artifact_info.st_ino),
                        "method": "local_file_fsync_directory_fsync_read_back"})
                    receipt = {"kind": "task_coverage_preservation", "version": 1, "evidence": evidence,
                               "receipt_id": "preserve-" + digest([view["archive_id"], view["file_digest"], str(location)])[:24],
                               "sha256": digest(evidence)}
                    serialized = json.dumps(receipt, ensure_ascii=True).encode()
                    if len(serialized) > MAX_PRESERVATION_BYTES:
                        raise OSError("保存凭据超过 65536 字节；未确认保存，原文保留")
                    _publish_preserved(descriptor, receipt_name, serialized)
                result = _verify_preserved_directory(location, descriptor, before, receipt_name, expected=expected, sync=True)
                current_lock = os.stat(".coverage-preservation.lock", dir_fd=descriptor, follow_symlinks=False)
                if identity(current_lock) != identity(lock_info):
                    raise OSError("保存目录锁身份已变化；状态未知")
                return {**result, "replayed": replayed, "artifact_path": str(location / artifact_name),
                        "receipt_path": str(location / receipt_name), "preservation": {**result["preservation"],
                            "state": "synced_and_read_back"}}
        finally:
            os.close(descriptor)


def _verify_preserved_directory(location, descriptor, before, receipt_name, *, expected=None, sync=False):
    import re
    if not re.fullmatch(r"archive-[a-f0-9]{24}-[a-f0-9]{24}\.[a-f0-9]{64}\.preservation\.json", receipt_name):
        raise ValueError("无效保存凭据文件名")
    artifact_name = receipt_name.removesuffix(".preservation.json") + ".archive.json"
    receipt_bytes, receipt_info = _read_preserved(descriptor, receipt_name, MAX_PRESERVATION_BYTES, sync=sync)
    raw, artifact_info = _read_preserved(descriptor, artifact_name, MAX_ARCHIVE_BYTES, sync=sync)
    result = verify_preservation_bytes(raw, receipt_bytes, expected=expected)
    storage = result["preservation"]["storage"]
    if (storage["artifact_name"] != artifact_name or _preservation_names(result)[1] != receipt_name
            or storage["directory"] != str(location) or storage["directory_device"] != str(before.st_dev)
            or storage["directory_inode"] != str(before.st_ino) or storage["artifact_inode"] != str(artifact_info.st_ino)):
        raise OSError("保存目录或原文身份与凭据不一致；状态未知")
    if sync:
        os.fsync(descriptor)
    for name, info in ((receipt_name, receipt_info), (artifact_name, artifact_info)):
        if identity(info) != identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False)):
            raise OSError("保存原文或凭据在核对期间变化；状态未知")
    _preservation_directory_current(location, descriptor, before)
    return {**result, "preservation": {**result["preservation"], "state": "read_back_now", "storage_observed_now": True}}


def verify_preservation_path(receipt, directory, *, expected=None):
    """Explicit readonly re-observation of the named directory, without syncing."""
    location, descriptor, before = _preservation_directory(directory)
    try:
        if Path(receipt).resolve(strict=True).parent != location or Path(receipt).is_symlink():
            raise ValueError("凭据必须位于明确选择的保存目录，且不是符号链接")
        return _verify_preserved_directory(location, descriptor, before, Path(receipt).name, expected=expected)
    finally:
        os.close(descriptor)


def verify_scoped_preservation(service, session, source, round_number, archive_id, *, archive_digest,
                               file_digest, receipt_file_digest, payload):
    from src.gateway.dispatch import session_identity
    from src.gateway.tasks import TASK_STATE_LOCK
    envelope = _strict_json(payload, MAX_PRESERVATION_REQUEST_BYTES)
    if (not isinstance(envelope, dict) or set(envelope) != {"artifact_text", "receipt_text"}
            or any(not isinstance(value, str) for value in envelope.values())):
        raise ValueError("保存凭据核对只接受 artifact_text/receipt_text 原文字符串")
    with TASK_STATE_LOCK:
        return verify_preservation_bytes(envelope["artifact_text"].encode("utf-8"), envelope["receipt_text"].encode("utf-8"),
            receipt_file_digest=receipt_file_digest, expected={"owner_session": session_identity(session),
                "source_task_id": source, "source_round": round_number, "workspace_id": workspace_id(service.runner.ledger),
                "archive_id": archive_id, "archive_digest": archive_digest, "file_digest": file_digest})


# Finite ledger-owned identity evidence, not an index, migration receipt or history deletion permit.
MAX_RETIREMENTS = 128
MAX_RETIREMENT_BYTES = 8192


def _retirement_directory(ledger):
    path = _directory(ledger) / "retirements"
    try:
        value = path.lstat()
        if not stat.S_ISDIR(value.st_mode):
            raise OSError("退役资料目录不是普通目录；状态未知")
    except FileNotFoundError:
        pass
    return path


def retirement_path(ledger, scan_id):
    import re
    if not isinstance(scan_id, str) or not re.fullmatch(r"scan-[a-f0-9]{24}", scan_id):
        raise ValueError("无效退役扫描 ID")
    return _retirement_directory(ledger) / f"{scan_id}.json"


def _retirement_inventory(ledger):
    values, count = [], 0
    try:
        with os.scandir(_retirement_directory(ledger)) as entries:
            for item in entries:
                count += 1
                if count > MAX_RETIREMENTS * 2:
                    raise OSError("退役资料目录超过观察上限；容量未知")
                if item.name.endswith(".json"):
                    values.append(item.name[:-5])  # Damaged names still occupy a slot; never prune.
    except FileNotFoundError:
        pass
    return sorted(values), count


def retirement_ids(ledger):
    return _retirement_inventory(ledger)[0]


def verify_retirement(record):
    """Pure SHA/identity/summary check. The original receipt chain remains in its archive."""
    import re
    from datetime import datetime
    try:
        if (len(json.dumps(record, ensure_ascii=True).encode()) > MAX_RETIREMENT_BYTES
                or set(record) != {"version", "kind", "retirement_id", "sha256", "evidence"}
                or type(record["version"]) is not int or record["version"] != 1
                or record["kind"] != "task_coverage_retirement" or record["sha256"] != digest(record["evidence"])):
            raise ValueError("retirement checksum mismatch")
        e = record["evidence"]
        if set(e) != {"workspace_id", "scope", "scan_id", "archive_id", "archive_digest", "checkpoint_digest",
                      "archived_at", "coverage", "retired_at", "reason"}:
            raise ValueError("retirement evidence mismatch")
        scope, c = e["scope"], e["coverage"]
        if (not isinstance(scope, list) or len(scope) != 6 or not typed_id(scope[0], "sid") or not typed_id(scope[1], "task")
                or type(scope[2]) is not int or not 1 <= scope[2] <= 3
                or not isinstance(scope[3], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", scope[3])
                or scope[4] not in ("discover", "reconcile") or type(scope[5]) is not int or not 1 <= scope[5] <= 512
                or not isinstance(e["scan_id"], str) or not re.fullmatch(r"scan-[a-f0-9]{24}", e["scan_id"])
                or record["retirement_id"] != "retired-" + e["scan_id"][5:]
                or e["reason"] not in ("explicit_active_checkpoint_release", "explicit_legacy_identity_retirement")
                or any(not isinstance(e[key], str) or not re.fullmatch(r"[a-f0-9]{64}", e[key])
                       for key in ("workspace_id", "archive_digest", "checkpoint_digest"))
                or not isinstance(e["archive_id"], str) or not re.fullmatch(r"archive-[a-f0-9]{24}-[a-f0-9]{24}", e["archive_id"])
                or not e["archive_id"].startswith("archive-" + _archive_group(e["workspace_id"], *scope[:2]) + "-")):
            raise ValueError("retirement identity mismatch")
        if (set(c) != {"snapshot_id", "evidence_digest", "sequence", "receipt_count", "observed_at", "state",
                      "snapshot_valid", "complete", "reasons"}
                or any(not isinstance(c[key], str) or not re.fullmatch(r"[a-f0-9]{64}", c[key])
                       for key in ("snapshot_id", "evidence_digest"))
                or type(c["sequence"]) is not int or not 0 <= c["sequence"] <= 1025
                or type(c["receipt_count"]) is not int or c["receipt_count"] != c["sequence"]
                or type(c["snapshot_valid"]) is not bool or type(c["complete"]) is not bool
                or c["state"] not in ("complete", "incomplete", "unknown")
                or (c["state"] == "complete") != c["complete"] or c["complete"] and not c["snapshot_valid"]
                or not isinstance(c["reasons"], list) or len(c["reasons"]) > 16
                or any(not isinstance(reason, str) for reason in c["reasons"])):
            raise ValueError("retirement summary mismatch")
        for value in (e["archived_at"], e["retired_at"], c["observed_at"]):
            if datetime.fromisoformat(value).tzinfo is None:
                raise ValueError("retirement timezone missing")
        return e
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        raise OSError("扫描退役凭据损坏或不可核验；状态未知，不能重建或释放") from error


def load_retirement(ledger, scan_id):
    path = retirement_path(ledger, scan_id)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    try:
        payload = _read_regular_file(path, MAX_RETIREMENT_BYTES)
        record = _strict_json(payload, MAX_RETIREMENT_BYTES)
        evidence = verify_retirement(record)
        if evidence["scan_id"] != scan_id:
            raise ValueError("retirement filename mismatch")
        return record
    except (ValueError, OSError) as error:
        raise OSError("扫描退役凭据不可读取；状态未知，不能重建或释放") from error


def save_retirement(ledger, record):
    import secrets
    from src.gateway.collaboration import CollaborationConflict
    e = verify_retirement(record)
    path = retirement_path(ledger, e["scan_id"])
    if path.exists() or path.is_symlink():
        if load_retirement(ledger, e["scan_id"]) != record:
            raise OSError("已有扫描退役凭据不能覆盖；未释放容量")
        # A prior sync may have failed. Re-confirm file and both directory entries.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _sync_directory(path.parent)
        _sync_directory(path.parent.parent)
    else:
        retired_ids, observed_entries = _retirement_inventory(ledger)
        if len(retired_ids) >= MAX_RETIREMENTS:
            raise CollaborationConflict("扫描退役凭据已达到 128 个上限；保留历史和活动文件，请只读导出；未释放容量")
        if observed_entries >= MAX_RETIREMENTS * 2:
            raise CollaborationConflict("退役资料目录已达到 256 项观察上限；保留残留资料，可只读导出既有凭据；未释放容量")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                target.write(json.dumps(record, ensure_ascii=True).encode())
                target.flush()
                os.fsync(target.fileno())
            os.link(temporary, path, follow_symlinks=False)  # Never replace a concurrent/existing identity.
            temporary.unlink()
            _sync_directory(path.parent)
            _sync_directory(path.parent.parent)
        finally:
            temporary.unlink(missing_ok=True)
    if load_retirement(ledger, e["scan_id"]) != record:
        raise OSError("扫描退役凭据回读失败；未确认活动容量释放")


def retirement_view(record):
    e = verify_retirement(record)
    return {"state": "retired", "retirement_id": record["retirement_id"], "retirement_digest": record["sha256"],
            "workspace_id": e["workspace_id"], "owner_session": e["scope"][0], "source_task_id": e["scope"][1],
            "source_round": e["scope"][2], "scan_id": e["scan_id"], "scan_request_id": e["scope"][3],
            "mode": e["scope"][4], "page_size": e["scope"][5], "archive_id": e["archive_id"],
            "archive_digest": e["archive_digest"], "checkpoint_digest": e["checkpoint_digest"],
            "archived_at": e["archived_at"], "retired_at": e["retired_at"], "reason": e["reason"], "coverage": e["coverage"],
            "historical": True, "current_coverage": False, "resumable": False, "verified": True,
            "integrity": "sha256_and_identity_binding", "history_capacity_released": False,
            "durable_copy_confirmed": False, "artifact": record}


def _retirement_scope(ledger, record, owner, source, round_number):
    from src.gateway.collaboration import CollaborationConflict
    e = verify_retirement(record)
    if e["workspace_id"] != workspace_id(ledger) or e["scope"][:3] != [owner, source, round_number]:
        raise CollaborationConflict("扫描退役凭据归属、来源、轮次或工作区不匹配")


def retirement_status(ledger, scan_id, owner, source, round_number):
    try:
        record = load_retirement(ledger, scan_id)
        if record is None:
            return {"state": "not_recorded"}
        _retirement_scope(ledger, record, owner, source, round_number)
        return retirement_view(record)
    except (OSError, ValueError):
        return {"state": "unknown"}


def read_retirement(service, session, source, round_number, scan_id):
    from src.gateway.dispatch import session_identity
    from src.gateway.collaboration import CollaborationConflict
    from src.gateway.tasks import TASK_STATE_LOCK
    with TASK_STATE_LOCK:
        ledger = service.runner.ledger
        record = load_retirement(ledger, scan_id)
        if record is None:
            raise CollaborationConflict("扫描退役凭据尚未保存；不能确认退役身份")
        _retirement_scope(ledger, record, session_identity(session), source, round_number)
        return retirement_view(record)


def verify_retirement_path(path, *, expected=None):
    record = _strict_json(_read_regular_file(path, MAX_RETIREMENT_BYTES), MAX_RETIREMENT_BYTES)
    view = retirement_view(record)
    from src.gateway.collaboration import CollaborationConflict
    for key, value in (expected or {}).items():
        if key not in view or type(value) is not type(view[key]) or view[key] != value:
            raise CollaborationConflict("扫描退役凭据期望身份不匹配；状态未知")
    return view
