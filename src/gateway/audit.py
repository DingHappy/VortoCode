"""Shared, redacted audit ledger for every VortoCode client.

The ledger intentionally stores metadata, not tool payloads or results.  Desktop,
Web, TUI, and later remote clients may render the same JSONL stream without
turning it into a second secret store.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

_MAX_READ_BYTES = 2 * 1024 * 1024
_MAX_ENTRIES = 500
_MAX_TEXT = 360
_LOCK = threading.Lock()
_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|token|authorization|password|passwd|secret|cookie|credential)",
    re.IGNORECASE,
)
_CONTENT_KEY = re.compile(
    r"(?:content|new_content|old_text|new_text|patch|diff|body|image|audio|data|result|prompt|text)",
    re.IGNORECASE,
)
_INLINE_SECRET = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|PASSWORD|PASSWD|SECRET|COOKIE|AUTHORIZATION))"
    r"\s*([=:])\s*([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clip_text(value: Any, limit: int = _MAX_TEXT) -> str:
    text = str(value or "")
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _INLINE_SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    return text if len(text) <= limit else text[:limit] + "…"


def sanitize_audit_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Return a bounded JSON value with secret/content fields removed."""
    if _SECRET_KEY.search(str(key)):
        return "[REDACTED]"
    if _CONTENT_KEY.fullmatch(str(key)):
        if isinstance(value, str) and re.fullmatch(r"<\d+ chars>", value):
            return value
        try:
            size = len(value)
        except TypeError:
            size = len(str(value or ""))
        return f"<{size} chars>"
    if depth >= 3:
        return "<nested>"
    if isinstance(value, dict):
        return {
            _clip_text(name, 80): sanitize_audit_value(item, key=str(name), depth=depth + 1)
            for name, item in list(value.items())[:30]
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize_audit_value(item, depth=depth + 1) for item in list(value)[:20]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _clip_text(value)


def append_audit(repo_root: str, record: Dict[str, Any]) -> bool:
    """Append one bounded record. Audit failures never block product work."""
    payload = dict(record or {})
    payload.setdefault("id", "audit-" + uuid.uuid4().hex[:12])
    payload.setdefault("ts", _now())
    path = Path(repo_root) / ".vortocode" / "audit.log"
    try:
        from src.utils.state_dir import ensure_state_gitignore

        ensure_state_gitignore(str(repo_root))
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with _LOCK, path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        return True
    except (OSError, TypeError, ValueError):
        return False


def record_tool_audit(
    repo_root: str,
    *,
    session: str,
    mode: str,
    name: str,
    args: Dict[str, Any] | None,
    result: Any,
) -> bool:
    return append_audit(repo_root, {
        "category": "tool",
        "session": _clip_text(session, 120),
        "mode": _clip_text(mode, 32),
        "tool": _clip_text(name, 120),
        "args": sanitize_audit_value(args or {}),
        "result_len": len(str(result or "")),
    })


def record_decision_audit(
    repo_root: str,
    *,
    session: str,
    mode: str,
    operation: str,
    decision: bool,
    tainted: bool,
) -> bool:
    return append_audit(repo_root, {
        "category": "decision",
        "session": _clip_text(session, 120),
        "mode": _clip_text(mode, 32),
        "operation": _clip_text(operation, 1000),
        "decision": "allowed" if decision else "denied",
        "tainted": bool(tainted),
    })


def record_event_audit(
    repo_root: str,
    *,
    session: str,
    mode: str,
    event: str,
    data: Dict[str, Any] | None = None,
) -> bool:
    return append_audit(repo_root, {
        "category": "event",
        "session": _clip_text(session, 120),
        "mode": _clip_text(mode, 32),
        "event": _clip_text(event, 120),
        "data": sanitize_audit_value(data or {}),
    })


def _tail_lines(path: Path) -> list[str]:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        start = max(0, size - _MAX_READ_BYTES)
        handle.seek(start)
        raw = handle.read(_MAX_READ_BYTES)
    if start:
        split = raw.find(b"\n")
        raw = raw[split + 1:] if split >= 0 else b""
    return raw.decode("utf-8", errors="replace").splitlines()


def _normalize(record: Dict[str, Any], line_number: int) -> Dict[str, Any]:
    category = str(record.get("category") or ("tool" if record.get("tool") else "event"))
    raw_id = str(record.get("id") or "")
    if not raw_id:
        digest = hashlib.sha256(
            json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8", errors="replace")
        ).hexdigest()[:12]
        raw_id = f"legacy-{line_number}-{digest}"
    item: Dict[str, Any] = {
        "id": _clip_text(raw_id, 80),
        "ts": _clip_text(record.get("ts") or record.get("timestamp") or "", 80),
        "category": category if category in {"tool", "decision", "event", "hook"} else "event",
        "session": _clip_text(record.get("session") or "", 120),
        "mode": _clip_text(record.get("mode") or "", 32),
    }
    if item["category"] == "tool":
        item.update(
            tool=_clip_text(record.get("tool") or "unknown", 120),
            args=sanitize_audit_value(record.get("args") or {}),
            result_len=max(0, int(record.get("result_len") or 0)),
        )
    elif item["category"] == "decision":
        decision = str(record.get("decision") or "denied")
        item.update(
            operation=_clip_text(record.get("operation") or "", 1000),
            decision=decision if decision in {"allowed", "denied"} else "denied",
            tainted=bool(record.get("tainted")),
        )
    else:
        item.update(
            event=_clip_text(record.get("event") or record.get("name") or "event", 120),
            data=sanitize_audit_value(record.get("data") or {}),
        )
    return item


def list_audit(repo_root: str, limit: int = 100) -> list[Dict[str, Any]]:
    """Return newest-first, normalized records; malformed legacy lines are skipped."""
    path = Path(repo_root) / ".vortocode" / "audit.log"
    if not path.is_file():
        return []
    bounded = max(1, min(int(limit), _MAX_ENTRIES))
    output: list[Dict[str, Any]] = []
    try:
        lines = _tail_lines(path)
    except OSError:
        return []
    for line_number, line in reversed(list(enumerate(lines))):
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                continue
            output.append(_normalize(record, line_number))
        except (TypeError, ValueError):
            continue
        if len(output) >= bounded:
            break
    return output
