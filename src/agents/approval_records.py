"""Finite decision metadata embedded in the owning domain record; never authorization.

Callers append only inside their existing state transition lock. Reading or
copying these events cannot approve, send, retry or complete any operation.
"""
from datetime import datetime
import re
import uuid

MAX_EVENTS = 20
ORIGINS = {"app", "runtime", "im", "unknown"}
SOURCES = {"user", "automatic_review", "system", "unknown"}


def approval_events(item: dict) -> list[dict]:
    events = item.get("approval_events", [])
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("审批记录格式待核对")
    fields = ("id", "at", "outcome", "origin", "source", "scope", "tainted")
    output = []
    for event in events:
        if (not isinstance(event, dict) or type(event.get("v")) is not int or event.get("v") != 1
                or not isinstance(event.get("id"), str) or not re.fullmatch(r"[0-9a-f]{24}", event["id"])
                or not isinstance(event.get("outcome"), str) or event["outcome"] not in {"approved", "denied", "cancelled"}
                or not isinstance(event.get("origin"), str) or event["origin"] not in ORIGINS
                or not isinstance(event.get("source"), str) or event["source"] not in SOURCES
                or not isinstance(event.get("scope"), str) or event["scope"] not in {"once", "none", "unknown"}
                or type(event.get("tainted")) is not bool or not isinstance(event.get("at"), str) or len(event["at"]) > 80):
            raise ValueError("审批记录格式待核对")
        when = datetime.fromisoformat(event["at"])
        if when.tzinfo is None:
            raise ValueError("审批时点待核对")
        output.append({"v": 1, **{field: event[field] for field in fields}})
    if len({event["id"] for event in output}) != len(output):
        raise ValueError("审批身份待核对")
    return output


def record_approval(item: dict, outcome: str, *, at: str, origin: str = "unknown", source: str = "unknown") -> None:
    """After transition checks, before the original atomic save; no separate store."""
    if outcome not in {"approved", "denied", "cancelled"}:
        raise ValueError("未知审批结果")
    repaired = False
    try:
        events = approval_events(item)
    except (ValueError, TypeError):
        if outcome == "approved":
            raise
        # 拒绝和撤回不依赖旧审批元数据；保留缺失标记，不能伪装历史完整。
        events, repaired = [], True
    origin = origin if isinstance(origin, str) and origin in ORIGINS else "unknown"
    source = source if isinstance(source, str) and source in SOURCES else "unknown"
    event = {"v": 1, "id": uuid.uuid4().hex[:24], "at": at, "outcome": outcome,
             "origin": origin, "source": source,
             "scope": "once" if source == "user" else "none" if source in {"automatic_review", "system"} else "unknown",
             "tainted": item.get("tainted") is True}
    # Validate newly written data too; a bad clock cannot create a fake decision time.
    approval_events({"approval_events": [event]})
    item["approval_events"] = (events + [event])[-MAX_EVENTS:]
    if repaired or len(events) == MAX_EVENTS:
        item["approval_events_truncated"] = True
