"""Non-executable hints in the existing audit log. No Future, text, digest or grant is persisted."""
import hashlib
import re

from src.agents.confirmation_context import approval_details
from src.gateway.audit import append_audit


def request_ref(request_id: str) -> str:
    return hashlib.sha256(("app-request:" + request_id).encode()).hexdigest()


def card_ref(rid: str, cid: str) -> str:
    return hashlib.sha256(f"app-card:{rid}:{cid}".encode()).hexdigest()


def open_card(root: str, *, rid: str, cid: str, request_id: str, kind: str, tainted: bool) -> bool:
    return append_audit(root, {"category": "event", "event": "app_card_opened", "session": "app",
        "data": {"card_ref": card_ref(rid, cid), "request_ref": request_ref(request_id),
                 **approval_details(kind), "tainted": bool(tainted), "approval_scope": "once"}}, durable=True)


def read_openings(records: list[dict]) -> tuple[dict, bool]:
    """Newest-first input, finite metadata only. Corruption is a source warning, never permission."""
    openings, partial = {}, False
    for item in records:
        if item.get("category") != "event" or item.get("event") != "app_card_opened":
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            partial = True
            continue
        valid = all(isinstance(data.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", data[key]) for key in ("card_ref", "request_ref"))
        details = approval_details(data.get("operation_kind"))
        if (not valid or type(data.get("tainted")) is not bool or data.get("approval_scope") != "once"
                or any(data.get(key) != value for key, value in details.items())):
            partial = True
            continue
        openings.setdefault(data["card_ref"], {**details, "card_ref": data["card_ref"],
            "request_ref": data["request_ref"], "tainted": data["tainted"], "ts": item.get("ts", "")})
    return openings, partial


def recovery_rows(records: list[dict], live_cards: set[str] | None) -> tuple[list[dict], bool]:
    openings, partial = read_openings(records)
    closed = {row.get("app_card_ref") for row in records if row.get("category") == "decision"}
    rows = []
    for ref, item in openings.items():
        if ref in closed and (live_cards is None or ref not in live_cards):
            continue
        state = "unknown" if live_cards is None else "pending" if ref in live_cards else "interrupted"
        rows.append({"id": "history-wait-" + ref[:24], "ts": item["ts"], "record_type": "card_wait", "origin": "app",
                     **{key: item[key] for key in ("operation_kind", "risk", "required_key", "tainted")},
                     "approval_scope": "once", "decision_source": "unknown", "decision": "unknown",
                     "approval_outcome": state, "source_status": state})
    return rows, partial
