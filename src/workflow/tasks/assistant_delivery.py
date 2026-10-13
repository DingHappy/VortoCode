"""Bounded, commit-bound projection of the existing project review snapshot."""
from __future__ import annotations

import re
from datetime import datetime, timezone

_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def delivery_evidence(snapshot: dict, *, task_id: str, branch: str) -> dict:
    if (not isinstance(snapshot, dict) or snapshot.get("ok") is not True
            or snapshot.get("target") != "task" or snapshot.get("task_id") != task_id
            or snapshot.get("branch") != branch or not isinstance(snapshot.get("head_oid"), str)
            or not _OID.fullmatch(snapshot["head_oid"])):
        raise ValueError("交付审查快照与当前任务、分支或提交不匹配")
    head = snapshot["head_oid"]
    review = snapshot.get("review") if isinstance(snapshot.get("review"), dict) else {}
    verification = review.get("verification") if isinstance(review.get("verification"), dict) else {}
    verified_head = verification.get("head") if isinstance(verification.get("head"), str) else ""
    verified_at = verification.get("at") if isinstance(verification.get("at"), str) else ""
    try:
        dated = datetime.fromisoformat(verified_at.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        dated = False
    if review.get("verification_stale") is True or (verified_head and verified_head != head):
        status = "stale"
    elif type(verification.get("ok")) is bool and verified_head == head and dated:
        status = "passed" if verification["ok"] else "failed"
    else:
        status = "unknown"
    coverage = review.get("coverage") if isinstance(review.get("coverage"), dict) else {}
    total, accepted = coverage.get("total_hunks"), coverage.get("accepted_hunks")
    known = (coverage.get("known") is True and coverage.get("truncated") is False
             and coverage.get("head") == head and type(total) is int and type(accepted) is int
             and 0 <= accepted <= total)
    return {"head": head, "verification": status,
            "verified_head": verified_head if _OID.fullmatch(verified_head) else "",
            "verified_at": verified_at if dated else "",
            "coverage_known": known, "accepted_hunks": accepted if known else None,
            "total_hunks": total if known else None,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
