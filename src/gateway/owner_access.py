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


def snapshot(root: str) -> dict:
    stored = get_trust_level(root)
    effective = resolve(stored, OWNER_DEVICE_PROFILE)
    history = audit_snapshot(root, 500)
    # Explicit allowlist: no operation body, tool arguments, result, path or credential.
    fields = ("ts", "operation_kind", "risk", "required_key", "approval_scope",
              "decision_source", "approval_outcome", "decision", "tainted")
    entries = []
    for item in history["entries"]:
        if item["category"] != "decision":
            continue
        entries.append({**{key: item[key] for key in fields if key in item},
                        "id": "history-" + hashlib.sha256(item["id"].encode()).hexdigest()[:24],
                        "record_type": "card" if item["operation"].startswith("App 审批卡 ") else "decision",
                        "origin": "app" if item["session"].startswith("app-") else "runtime"})
        if len(entries) == 100:
            break
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "permissions": {"profile": OWNER_DEVICE_PROFILE, "stored_level": stored,
                "effective_level": effective, "ceiling": ceiling(OWNER_DEVICE_PROFILE),
                "scopes": ["once"], "taint_revokes_preauthorization": True,
                "write_tools": sorted(OWNER_DEVICE_WRITE_TOOLS),
                "categories": [{**approval_details(kind), "preauthorized": pre_authorized(effective, kind)} for kind in KINDS]},
            "history": {"entries": entries, "status": history["status"], "bounded": True}}
