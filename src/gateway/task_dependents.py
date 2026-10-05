"""Bounded reverse discovery and stop coordination on the original ledger/pool.

No persistent index, watcher, polling, release or model scheduling. Event callers
and the one startup audit share this operation; partial work is always reported.
"""
from __future__ import annotations

from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
import logging

from src.gateway.audit import _clip_text
from src.gateway.collaboration import CollaborationConflict
from src.gateway.task_dependencies import MAX_DEPTH, MAX_DEPENDENCIES, dependency_state
from src.gateway.tasks import MAX_SCAN_ENTRIES, MAX_SCAN_RECORD_BYTES, TASK_STATE_LOCK
from src.utils.ids import typed_id

MAX_DEPENDENTS, MAX_EDGE_VISITS = 32, 128
_ACTIVE: ContextVar[bool] = ContextVar("dependency_coordination_active", default=False)
_LOGGER = logging.getLogger(__name__)


def _validate_identity(task) -> None:
    if (not typed_id(task.id, "task")
            or type(task.collaboration.get("round")) is not int or not 1 <= task.collaboration["round"] <= 3
            or not isinstance(task.owner_session, str) or not task.owner_session.strip()
            or not isinstance(task.status, str) or task.status not in {
                "waiting", "queued", "running", "blocked", "done", "failed", "cancelled", "paused", "interrupted"}
            or not isinstance(task.prompt, str) or not isinstance(task.result, str)):
        raise CollaborationConflict("下游任务身份、轮次或执行状态损坏")


@dataclass
class DependentScan:
    tasks: list
    scanned: int
    skipped: int
    complete: bool
    truncated: bool
    errors: list[dict]
    readable: bool
    damaged: int

    def summary(self) -> dict:
        return {"scanned": self.scanned, "skipped": self.skipped, "complete": self.complete,
                "truncated": self.truncated, "scan_readable": self.readable,
                "damaged": self.damaged, "errors": self.errors,
                "limits": {"scan_entries": MAX_SCAN_ENTRIES, "record_bytes": MAX_SCAN_RECORD_BYTES,
                           "dependents": MAX_DEPENDENTS, "depth": MAX_DEPTH, "edge_visits": MAX_EDGE_VISITS}}


def discover(ledger, source_id: str | None = None, owner: str | None = None) -> DependentScan:
    """Make an ephemeral reverse map from one bounded snapshot, then discard it."""
    with TASK_STATE_LOCK:
        snapshot = ledger.scan()
        reverse, candidates, errors = {}, {}, []
        for task in snapshot.tasks:
            state = task.collaboration
            if (task.kind != "delegation" or not isinstance(state, dict)
                    or not isinstance(state.get("dispatch"), dict)
                    or state["dispatch"].get("source") != "api"
                    or owner is not None and task.owner_session != owner):
                continue
            contracts = [task.dependencies.get("requires") if isinstance(task.dependencies, dict) else None,
                         state["dispatch"].get("depends_on")]
            if not any(contracts) and not task.dependencies:
                continue
            candidates[task.id] = task
            try:
                _validate_identity(task)
                dependency_state(task)
            except (CollaborationConflict, TypeError, KeyError) as error:
                errors.append({"task_id": task.id, "error": _clip_text(str(error), 500)})
            # Keep bounded references from both contracts so a damaged mismatch
            # is discoverable, while its authoritative reconciliation fails closed.
            parents = set()
            for contract in contracts:
                if isinstance(contract, list):
                    for ref in contract[:MAX_DEPENDENCIES]:
                        if isinstance(ref, dict) and typed_id(ref.get("task_id"), "task"):
                            parents.add(ref["task_id"])
            for parent in parents:
                reverse.setdefault(parent, set()).add(task.id)

        selected, truncated = [], snapshot.truncated
        if source_id is None:
            selected = [candidates[tid] for tid in sorted(candidates)
                        if not isinstance(candidates[tid].dependencies, dict)
                        or candidates[tid].dependencies.get("resolution") == "consumed"
                        or candidates[tid].dependencies.get("resolution") == "invalidated"
                        and candidates[tid].status in ("queued", "running", "blocked")]
            truncated |= len(selected) > MAX_DEPENDENTS
            selected = selected[:MAX_DEPENDENTS]
        else:
            queue, seen, visits = deque([(source_id, 0)]), {source_id}, 0
            while queue:
                parent, depth = queue.popleft()
                for tid in sorted(reverse.get(parent, ())):
                    visits += 1
                    if visits > MAX_EDGE_VISITS:
                        truncated = True
                        queue.clear()
                        break
                    if tid in seen:
                        continue
                    if depth >= MAX_DEPTH or len(selected) >= MAX_DEPENDENTS:
                        truncated = True
                        continue
                    seen.add(tid)
                    selected.append(candidates[tid])
                    queue.append((tid, depth + 1))
        # Return only errors in the selected owner/graph, but retain incompleteness
        # if other damaged records prevented a reliable reverse-map claim.
        selected_ids = {task.id for task in selected}
        return DependentScan(selected, snapshot.scanned, snapshot.skipped,
                             snapshot.complete and not truncated and not errors, truncated,
                             [error for error in errors if error["task_id"] in selected_ids],
                             snapshot.readable, len(errors))


def reconcile(service, scan: DependentScan) -> dict:
    """Persist first, then stop via DispatchService; independent failures remain visible."""
    token = _ACTIVE.set(True)
    try:
        return _reconcile(service, scan)
    finally:
        _ACTIVE.reset(token)


def _reconcile(service, scan: DependentScan) -> dict:
    items, failures = [], []
    with TASK_STATE_LOCK:
        for selected in scan.tasks:
            try:
                current = service.get(selected.owner_session, selected.id)
                _validate_identity(current)
                if (current.owner_session != selected.owner_session
                        or current.collaboration.get("round") != selected.collaboration.get("round")):
                    raise CollaborationConflict("下游身份或轮次在发现后变化")
                state = dependency_state(current)
                changed, stopped = False, False
                if state["resolution"] in {"consumed", "invalidated"}:
                    current, changed, stopped = service.reconcile_dependencies(
                        current.owner_session, current.id, current.collaboration["round"])
                elif state["resolution"] == "waiting":
                    # Readiness/failure projection only; no save, release or slot.
                    service.notify_update(current)
                items.append({"task_id": current.id, "round": current.collaboration["round"],
                              "status": current.status, "resolution": current.dependencies["resolution"],
                              "invalidation_recorded": changed, "stop_requested": stopped})
            except (ValueError, OSError, TypeError, KeyError) as error:
                failures.append({"task_id": selected.id, "error": _clip_text(str(error), 500)})
        return {**scan.summary(), "complete": scan.complete and not failures,
                "tasks": items, "errors": failures}


def coordinate(service, source_id: str | None = None, owner: str | None = None) -> dict | None:
    """Collapse nested notices into the one bounded traversal already in progress."""
    if _ACTIVE.get():
        return None
    token = _ACTIVE.set(True)
    try:
        with TASK_STATE_LOCK:
            report = reconcile(service, discover(service.runner.ledger, source_id, owner))
        if not report["complete"]:
            _LOGGER.warning("Dependency reconciliation incomplete: source=%s scanned=%s skipped=%s readable=%s truncated=%s damaged=%s errors=%s",
                            source_id, report["scanned"], report["skipped"], report["scan_readable"],
                            report["truncated"], report["damaged"], report["errors"])
        return report
    finally:
        _ACTIVE.reset(token)

# Paged coverage uses TaskLedger-owned checkpoints, never task copies or a pool.
MAX_COVERAGE_NODES, MAX_COVERAGE_EDGES = 1024, 8192
MAX_COVERAGE_PAGES = 1024


def _coverage_time():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _coverage_candidate(task, owner):
    state = task.collaboration
    if (task.kind != "delegation" or not isinstance(state, dict)
            or not isinstance(state.get("dispatch"), dict) or state["dispatch"].get("source") != "api"):
        return None, False
    if not isinstance(task.owner_session, str):
        return None, True
    if task.owner_session != owner:
        return None, False
    contracts = [task.dependencies.get("requires") if isinstance(task.dependencies, dict) else None,
                 state["dispatch"].get("depends_on")]
    if not any(contracts) and not task.dependencies:
        return None, False
    damaged = False
    try:
        _validate_identity(task)
        dependency_state(task)
    except (ValueError, TypeError, KeyError, RecursionError):
        damaged = True
    parents = set()
    for contract in contracts:
        if isinstance(contract, list):
            for ref in contract[:MAX_DEPENDENCIES]:
                if isinstance(ref, dict) and isinstance(ref.get("task_id"), str) and typed_id(ref["task_id"], "task"):
                    parents.add(ref["task_id"])
    return {"owner": task.owner_session, "round": state.get("round") if type(state.get("round")) is int else 0,
            "status": task.status if isinstance(task.status, str) else "damaged",
            "resolution": task.dependencies.get("resolution") if isinstance(task.dependencies, dict)
            and isinstance(task.dependencies.get("resolution"), str) else "damaged",
            "parents": sorted(parents), "damaged": damaged}, damaged


def _coverage_cursor(state):
    from src.gateway.task_scan import digest
    sequence = state["sequence"]
    return f'{state["scan_id"]}.{sequence}.{digest([state["nonce"], sequence, state["scope"]])}'


def _coverage_invalidate(state, reason):
    state["valid"] = False
    state["phase"] = "finished"
    if reason not in state["reasons"]:
        state["reasons"].append(reason)


def _coverage_validity(ledger, state):
    entries, reason = ledger.scan_inventory()
    if reason or entries != state["entries"]:
        _coverage_invalidate(state, reason or "snapshot_changed")
    return state["valid"]


def _coverage_report(state, *, replayed=False):
    from src.gateway.task_scan import digest, MAX_INVENTORY, MAX_PAGE_BYTES, MAX_TOTAL_BYTES
    complete = (state["valid"] and state["phase"] == "finished" and not state["reasons"]
                and state["graph_complete"] and state["processed"] == len(state["selected"]))
    unknown = not state["valid"] or state["skipped"] > 0 or state["damaged"] > 0 or state["failures"] > 0
    return {"scan_id": state["scan_id"], "request_id": state["scope"][3], "mode": state["scope"][4],
            "source_task_id": state["scope"][1], "source_round": state["scope"][2],
            "page_size": state["scope"][5], "sequence": state["sequence"], "phase": state["phase"],
            "replayed": replayed, "page_complete": True, "complete": complete,
            "next_cursor": _coverage_cursor(state) if state["valid"] and state["phase"] != "finished" else None,
            "snapshot_valid": state["valid"], "validity": "observed_namespace_and_lstat",
            "snapshot_id": state["snapshot_id"], "evidence_digest": state["evidence_digest"],
            "receipt_count": len(state["receipts"]),
            "observed_at": state["observed_at"], "order": "filename_utf8_bytes_ascending",
            "coverage": {"state": "complete" if complete else "unknown" if unknown else "incomplete",
                         "entries_total": len(state["entries"]), "entries_read": state["offset"],
                         "record_bytes": state["bytes"], "skipped": state["skipped"], "damaged": state["damaged"],
                         "graph_complete": state["graph_complete"], "edge_visits": state["visits"],
                         "selected": len(state["selected"]), "processed": state["processed"],
                         "record_range": [0, state["offset"]], "task_range": [0, state["processed"]],
                         "covered_digest": digest([[tid, state["candidates"][tid]["owner"], state["candidates"][tid]["round"]]
                                                   for tid in state["selected"][:state["processed"]]]),
                         "reconciled": state["processed"] if state["scope"][4] == "reconcile" else 0,
                         "failures": state["failures"], "reasons": state["reasons"]},
            "tasks": state["last_tasks"], "errors": state["last_errors"],
            "limits": {"inventory_entries": MAX_INVENTORY, "page_entries": MAX_SCAN_ENTRIES,
                       "record_bytes": MAX_SCAN_RECORD_BYTES, "page_bytes": MAX_PAGE_BYTES,
                       "total_bytes": MAX_TOTAL_BYTES, "page_dependents": MAX_DEPENDENTS,
                       "graph_nodes": MAX_COVERAGE_NODES, "graph_edges": MAX_COVERAGE_EDGES, "depth": MAX_DEPTH,
                       "pages": MAX_COVERAGE_PAGES}}


def _coverage_plan(state):
    reverse = {}
    for tid, item in state["candidates"].items():
        for parent in item["parents"]:
            reverse.setdefault(parent, []).append(tid)
    queue, seen = deque([(state["scope"][1], 0)]), {state["scope"][1]}
    state["graph_complete"] = True
    while queue:
        parent, depth = queue.popleft()
        for tid in sorted(reverse.get(parent, ())):
            if state["visits"] == MAX_COVERAGE_EDGES:
                state["reasons"].append("graph_edge_budget")
                state["graph_complete"] = False
                return
            state["visits"] += 1
            if tid in seen:
                continue
            if depth >= MAX_DEPTH:
                state["graph_complete"] = False
                if "graph_depth_budget" not in state["reasons"]:
                    state["reasons"].append("graph_depth_budget")
                continue
            if len(state["selected"]) == MAX_COVERAGE_NODES:
                state["reasons"].append("graph_node_budget")
                state["graph_complete"] = False
                return
            seen.add(tid)
            state["selected"].append(tid)
            queue.append((tid, depth + 1))


def _coverage_read(ledger, state):
    from src.gateway.task_scan import MAX_PAGE_BYTES, MAX_TOTAL_BYTES
    page_bytes, count = 0, 0
    while state["offset"] < len(state["entries"]) and count < state["scope"][5]:
        entry = state["entries"][state["offset"]]
        size = entry[1][3] if entry[0].endswith(".json") and entry[1][3] <= MAX_SCAN_RECORD_BYTES else 0
        if state["bytes"] + size > MAX_TOTAL_BYTES:
            state["reasons"].append("total_read_budget")
            state["phase"] = "finished"
            return
        if page_bytes + size > MAX_PAGE_BYTES:
            break
        task, value, consumed = ledger.read_scan_record(entry)
        page_bytes += consumed
        state["bytes"] += consumed
        state["offset"] += 1
        count += 1
        if value == "snapshot_changed":
            _coverage_invalidate(state, value)
            return
        if task is None:
            if value:
                state["skipped"] += 1
                if "records_unreadable" not in state["reasons"]:
                    state["reasons"].append("records_unreadable")
            continue
        candidate, damaged = _coverage_candidate(task, state["scope"][0])
        state["damaged"] += int(damaged)
        if damaged and "contracts_damaged" not in state["reasons"]:
            state["reasons"].append("contracts_damaged")
        if candidate:
            candidate["sha256"] = value
            state["candidates"][task.id] = candidate
    if state["offset"] == len(state["entries"]):
        _coverage_plan(state)
        state["phase"] = "process" if state["selected"] else "finished"
        if state["skipped"] or state["damaged"]:
            state["graph_complete"] = False
            # Unknown records can conceal graph edges or oversized sources.
            # Do not claim a graph or run batch reconciliation against it.
            if state["scope"][4] == "reconcile":
                state["phase"] = "finished"


def _coverage_immutable(task):
    # Stop/marker messages and execution state may change; consumed identity and
    # historical evidence may not be rebased silently by a coverage operation.
    state = task.collaboration
    return [task.id, task.kind, task.owner_session, task.prompt, task.result, task.chain_budget,
            state.get("round"), state.get("assignee"), state.get("acceptance"), state.get("dispatch"), state.get("tainted"),
            state.get("review") if task.status == "done" else None,
            task.dependencies.get("requires"), task.dependencies.get("inputs"), task.dependencies.get("released_round")]


def _coverage_process(service, state):
    ledger = service.runner.ledger
    entries = {entry[0]: entry for entry in state["entries"]}
    tids = state["selected"][state["processed"]:state["processed"] + MAX_DEPENDENTS]
    tasks = []
    for tid in tids:
        task, value, _ = ledger.read_scan_record(entries[tid + ".json"])
        candidate = state["candidates"][tid]
        if (task is None or value != candidate["sha256"] or not candidate["damaged"] and (
                task.owner_session != candidate["owner"] or not isinstance(task.collaboration, dict)
                or task.collaboration.get("round") != candidate["round"])):
            _coverage_invalidate(state, "selected_identity_changed")
            return
        tasks.append(task)
    if state["scope"][4] == "discover":
        state["last_tasks"] = [{"task_id": task.id, "round": task.collaboration.get("round"),
                                "status": task.status, "resolution": state["candidates"][task.id]["resolution"]}
                               for task in tasks]
    else:
        identities = {task.id: _coverage_immutable(task) for task in tasks if not state["candidates"][task.id]["damaged"]}
        scan = DependentScan(tasks, 0, 0, True, False, [], True, 0)
        report = reconcile(service, scan)
        state["last_tasks"], state["last_errors"] = report["tasks"], report["errors"]
        state["failures"] += len(report["errors"])
        if report["errors"] and "reconciliation_failed" not in state["reasons"]:
            state["reasons"].append("reconciliation_failed")
        current, reason = ledger.scan_inventory()
        if reason or [entry[0] for entry in current] != [entry[0] for entry in state["entries"]]:
            _coverage_invalidate(state, reason or "snapshot_changed")
            return
        for entry in current:
            if entry == entries[entry[0]]:
                continue
            tid = entry[0][:-5] if entry[0].endswith(".json") else ""
            if tid not in identities:
                _coverage_invalidate(state, "snapshot_changed")
                return
            task, value, _ = ledger.read_scan_record(entry)
            if task is None or _coverage_immutable(task) != identities[tid]:
                _coverage_invalidate(state, "selected_identity_changed")
                return
            state["candidates"][tid]["sha256"] = value
        state["entries"] = current
    state["processed"] += len(tids)
    if state["processed"] == len(state["selected"]):
        state["phase"] = "finished"


def coverage_page(service, session, task_id, round_number, request_id, *, mode="reconcile", cursor=None, page_size=512):
    """One checkpointed bounded step. Exact scope and cursor; no autonomous loop."""
    import re
    import secrets
    from pathlib import Path
    from src.gateway.dispatch import session_identity
    from src.gateway.task_scan import digest, MAX_CHECKPOINTS
    if (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", request_id)
            or mode not in ("discover", "reconcile") or type(page_size) is not int or not 1 <= page_size <= MAX_SCAN_ENTRIES
            or type(round_number) is not int or not 1 <= round_number <= 3):
        raise ValueError("无效覆盖扫描合同")
    scope = [session_identity(session), task_id, round_number, request_id, mode, page_size]
    scan_id = "scan-" + digest([str(Path(service.runner.ledger.repo_root).resolve()), scope[:4]])[:24]
    ledger = service.runner.ledger
    with TASK_STATE_LOCK:
        from src.gateway.task_scan import load_retirement, _retirement_scope
        retirement = load_retirement(ledger, scan_id)
        if retirement is not None:
            _retirement_scope(ledger, retirement, scope[0], task_id, round_number)
            raise CollaborationConflict("该扫描已归档并退役；请加载资料重试释放或使用新的扫描请求 ID")
        state = ledger.load_scan(scan_id)
        if state is None:
            if cursor is not None:
                raise CollaborationConflict("扫描游标不存在；不能猜测进度")
            from src.gateway.task_scan import scan_was_archived
            if scan_was_archived(ledger, scan_id, scope[0], task_id):
                raise CollaborationConflict("该扫描已归档并释放；请使用新的扫描请求 ID")
            service._dependency_source(session, task_id, round_number)
            if len(ledger.scan_ids()) >= MAX_CHECKPOINTS:
                raise CollaborationConflict(f"活动覆盖已达到 {MAX_CHECKPOINTS} 个保留上限；请加载覆盖资料，先保留历史归档再显式释放；未开始新扫描")
            entries, reason = ledger.scan_inventory()
            state = {"scan_id": scan_id, "scope": scope, "nonce": secrets.token_hex(16), "sequence": 0,
                     "entries": entries, "valid": not reason, "phase": "records" if not reason else "finished",
                     "offset": 0, "bytes": 0, "skipped": 0, "damaged": 0, "candidates": {}, "selected": [],
                     "processed": 0, "visits": 0, "graph_complete": False, "failures": 0,
                     "reasons": [reason] if reason else [], "last_tasks": [], "last_errors": [],
                     "observed_at": _coverage_time(), "last_input": None,
                     "snapshot_id": digest([scope, entries]), "evidence_digest": digest([scope, entries]), "receipts": []}
        elif state["scope"] != scope:
            raise CollaborationConflict("扫描请求合同或作用域已变化")
        expected = _coverage_cursor(state)
        replay = state["sequence"] > 0 and cursor == state["last_input"]
        if not replay and cursor != (None if state["sequence"] == 0 else expected):
            raise CollaborationConflict("扫描游标已过期或与请求不匹配")
        try:
            service._dependency_source(session, task_id, round_number)
        except (ValueError, TypeError, KeyError, AttributeError):
            _coverage_invalidate(state, "source_identity_changed")
        if state["valid"]:
            _coverage_validity(ledger, state)
        if not replay and state["valid"] and state["phase"] != "finished":
            before = [state["phase"], state["offset"], state["processed"], digest(state["entries"])]
            state["last_tasks"], state["last_errors"] = [], []
            if state["sequence"] >= MAX_COVERAGE_PAGES:
                state["phase"] = "finished"
                state["reasons"].append("page_budget")
            elif state["phase"] == "records":
                _coverage_read(ledger, state)
            else:
                _coverage_process(service, state)
            if state["valid"]:
                _coverage_validity(ledger, state)
            state["last_input"] = cursor
            state["sequence"] += 1
            receipt = {"sequence": state["sequence"], "before": before,
                       "after": [state["phase"], state["offset"], state["processed"], digest(state["entries"])],
                       "outcome": digest([state["last_tasks"], state["last_errors"], state["reasons"], state["valid"]])}
            state["evidence_digest"] = digest([state["evidence_digest"], receipt])
            state["receipts"].append(receipt)
        state["observed_at"] = _coverage_time()
        ledger.save_scan(state)  # Failure never acknowledges a page or an all-task transaction.
        return _coverage_report(state, replayed=replay)


def coverage_status(service, session, task_id, round_number, scan_id=None):
    from src.gateway.dispatch import session_identity
    with TASK_STATE_LOCK:
        ledger = service.runner.ledger
        if scan_id is None:
            service._dependency_source(session, task_id, round_number)
            states = [ledger.load_scan(value) for value in ledger.scan_ids()]
            states = [value for value in states if value and value["scope"][:3] == [session_identity(session), task_id, round_number]
                      and value["scope"][4] == "reconcile"]
            from src.gateway.task_scan import load_retirement
            states = [value for value in states if load_retirement(ledger, value["scan_id"]) is None]
            if not states:
                return None
            state = max(states, key=lambda value: (value["observed_at"], value["scan_id"]))
        else:
            from src.gateway.task_scan import load_retirement, _retirement_scope
            retirement = load_retirement(ledger, scan_id)
            if retirement is not None:
                _retirement_scope(ledger, retirement, session_identity(session), task_id, round_number)
                raise CollaborationConflict("扫描已退役；请加载覆盖资料核验或重试释放")
            state = ledger.load_scan(scan_id)
            if state is None or state["scope"][:3] != [session_identity(session), task_id, round_number]:
                raise CollaborationConflict("扫描不存在或作用域不匹配")
        try:
            service._dependency_source(session, task_id, round_number)
        except (ValueError, TypeError, KeyError, AttributeError):
            _coverage_invalidate(state, "source_identity_changed")
        if state["valid"]:
            _coverage_validity(ledger, state)
        state["observed_at"] = _coverage_time()
        ledger.save_scan(state)
        return _coverage_report(state)
