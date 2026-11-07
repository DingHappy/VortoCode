"""Read-only owner-device policy and privacy-limited projection of the existing audit.

No permission grants, tool execution, new ledger or approval replay lives here.
"""
from datetime import datetime, timezone
import hashlib

from src.agents.confirmation_context import approval_details
from src.agents.capabilities import OWNER_DEVICE_PROFILE, OWNER_DEVICE_WRITE_TOOLS
from src.agents.trust import KINDS, ceiling, pre_authorized, resolve
from src.gateway.audit import audit_snapshot
from src.gateway.trust_setting import get_trust_level


def _domain_entries(items: list[dict], record_type: str) -> tuple[list[dict], bool]:
    from src.agents.approval_records import approval_events
    output, partial = [], False
    states = {"proposed", "approved", "sending", "sent", "failed", "unknown", "cancelled", "expired",
              "send_failed", "submission_unknown", "running", "waiting", "done", "stopped"}
    for item in items:
        if not isinstance(item.get("id"), str) or not isinstance(item.get("status"), str):
            partial = True
            continue
        try:
            events = approval_events(item)
        except (ValueError, TypeError):
            partial, events = True, []
        partial = partial or item.get("approval_events_truncated") is True
        if not events:
            if item["status"] == "proposed" and "approval_events" not in item:
                continue  # A proposal is not a past approval.
            events = [{"id": "legacy", "outcome": "unknown", "at": "", "origin": "unknown",
                       "source": "unknown", "scope": "unknown", "tainted": item.get("tainted") is True}]
        for event in reversed(events):
            outcome = event["outcome"]
            identity = f"{record_type}:{item['id']}:{event['id']}"
            output.append({"id": "history-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                "ts": event["at"], "record_type": record_type,
                "domain_kind": item.get("kind") if record_type == "codex_action" and isinstance(item.get("kind"), str) and item["kind"] in {"message", "interrupt"} else "unknown",
                "origin": event["origin"], "approval_outcome": outcome,
                "decision": "allowed" if outcome == "approved" else "denied" if outcome in {"denied", "cancelled"} else "unknown",
                "decision_source": event["source"], "approval_scope": event["scope"], "tainted": event["tainted"],
                "source_status": item["status"] if item["status"] in states else "unknown",
                "decision_metadata_missing": event["id"] == "legacy"})
    return output, partial


def _event_order(row: dict) -> datetime:
    try:
        parsed = datetime.fromisoformat(row.get("ts") or "")
        if parsed.tzinfo is None:
            raise ValueError()
        when = parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        when = datetime.min.replace(tzinfo=timezone.utc)
    return when


def snapshot(root: str) -> dict:
    stored = get_trust_level(root)
    effective = resolve(stored, OWNER_DEVICE_PROFILE)
    history = audit_snapshot(root, 500)
    # Explicit allowlist: no operation body, tool arguments, result, path or credential.
    fields = ("ts", "operation_kind", "risk", "required_key", "approval_scope",
              "decision_source", "approval_outcome", "decision", "tainted")
    entries = []
    for item in history["entries"]:
        if item["category"] != "decision" or not item.get("operation_kind"):
            continue
        entries.append({**{key: item[key] for key in fields if key in item},
                        "id": "history-" + hashlib.sha256(item["id"].encode()).hexdigest()[:24],
                        "record_type": "card" if item["operation"].startswith("App 审批卡 ") else "decision",
                        "origin": "app" if item["session"].startswith("app-") else "runtime"})
        if len(entries) == 100:
            break
    from src.scheduling.codex_watch import read_action_snapshot
    from src.workflow.tasks.assistant_dispatch import read_dispatch_snapshot
    sources = {"audit": history["status"]}
    for name, record_type, read in (("codex", "codex_action", read_action_snapshot),
                                    ("dispatches", "dispatch", read_dispatch_snapshot)):
        source = read(root)
        rows, partial = _domain_entries(source["items"], record_type)
        sources[name] = "partial" if partial and source["status"] == "current" else source["status"]
        entries.extend(rows)
    entries.sort(key=_event_order, reverse=True)
    incomplete = any(value != "current" for value in sources.values())
    status = ("unavailable" if not entries and "unavailable" in sources.values() else "partial") if incomplete else "current"
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "permissions": {"profile": OWNER_DEVICE_PROFILE, "stored_level": stored,
                "effective_level": effective, "ceiling": ceiling(OWNER_DEVICE_PROFILE),
                "scopes": ["once"], "taint_revokes_preauthorization": True,
                "write_tools": sorted(OWNER_DEVICE_WRITE_TOOLS),
                "categories": [{**approval_details(kind), "preauthorized": pre_authorized(effective, kind)} for kind in KINDS]},
            "history": {"entries": entries[:100], "status": status, "bounded": True, "sources": sources}}
