"""Derive actionable Hook failures from bounded session activity history.

Hook execution remains fail-open for crashes/timeouts, but those outcomes must
not disappear inside a conversation timeline.  This module projects terminal
Hook lifecycle events into a small acknowledgement queue without copying tool
arguments, command output, prompts, or source code.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Iterable

_ATTENTION = frozenset({"failed", "timed_out", "blocked"})
_MAX_ITEMS = 50
_MAX_ACKS_PER_SESSION = 300
_MAX_SESSIONS = 100
_LOCK = threading.Lock()


def _duration(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def hook_issue_snapshot(
    activities: Iterable[Any],
    acknowledgements: Iterable[Any] = (),
    *,
    limit: int = _MAX_ITEMS,
) -> dict[str, Any]:
    """Return the latest unresolved terminal Hook outcomes, newest first."""
    acknowledged = {str(value) for value in acknowledgements if str(value or "").strip()}
    latest: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, raw in enumerate(activities or ()):
        if not isinstance(raw, dict) or raw.get("type") != "agent_hook":
            continue
        issue_id = str(raw.get("id") or "").strip()[:160]
        if not issue_id:
            continue
        latest[issue_id] = (index, raw)

    items = []
    for issue_id, (index, raw) in latest.items():
        status = str(raw.get("status") or "").strip().lower()
        if status not in _ATTENTION or issue_id in acknowledged:
            continue
        items.append((index, {
            "id": issue_id,
            "name": str(raw.get("name") or "hook")[:160],
            "event": str(raw.get("event") or "unknown")[:160],
            "tool": str(raw.get("tool") or "")[:160],
            "status": status,
            "summary": str(raw.get("summary") or f"Hook {status}")[:500],
            "message": str(raw.get("message") or "")[:1_000],
            "error": str(raw.get("error") or "")[:1_000],
            "duration_ms": _duration(raw.get("duration_ms")),
            "created": str(raw.get("recorded_at") or "")[:80],
        }))
    bounded = max(1, min(int(limit or 1), _MAX_ITEMS))
    ordered = [item for _index, item in sorted(items, key=lambda pair: pair[0], reverse=True)]
    visible = ordered[:bounded]
    return {
        "count": len(ordered),
        "latest": visible[0] if visible else None,
        "items": visible,
        "truncated": len(ordered) > len(visible),
    }


def has_hook_issue(activities: Iterable[Any], acknowledgements: Iterable[Any], issue_id: str) -> bool:
    target = str(issue_id or "").strip()
    if not target:
        return False
    return any(item["id"] == target for item in hook_issue_snapshot(
        activities, acknowledgements, limit=_MAX_ITEMS,
    )["items"])


def _repo_key(repo_root: str) -> str:
    return hashlib.sha256(str(Path(repo_root).resolve()).encode("utf-8")).hexdigest()[:24]


def _store_path(repo_root: str) -> Path:
    override = os.getenv("VORTOCODE_HOOK_ISSUE_STORE", "").strip()
    if override:
        target = Path(override).expanduser()
        return target if target.suffix else target / f"{_repo_key(repo_root)}.json"
    if os.name == "posix" and Path.home().joinpath("Library").is_dir():
        base = Path.home() / "Library" / "Application Support" / "VortoCode" / "hook-issues"
    else:
        base = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "vortocode" / "hook-issues"
    return base / f"{_repo_key(repo_root)}.json"


def _load_ack_store(repo_root: str) -> dict[str, list[str]]:
    try:
        payload = json.loads(_store_path(repo_root).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    raw = payload.get("acks") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {}
    output: dict[str, list[str]] = {}
    for session, values in list(raw.items())[-_MAX_SESSIONS:]:
        if not isinstance(values, list):
            continue
        output[str(session)[:160]] = [str(value)[:160] for value in values
                                      if str(value or "").strip()][-_MAX_ACKS_PER_SESSION:]
    return output


def load_hook_issue_acks(repo_root: str, session: str) -> list[str]:
    """Read acknowledgements from user-owned state outside the repository."""
    key = str(session or "").strip()[:160]
    if not key:
        return []
    with _LOCK:
        return list(_load_ack_store(repo_root).get(key) or [])


def load_hook_issue_acknowledgements(repo_root: str) -> dict[str, list[str]]:
    """Read the bounded acknowledgement map once for Dashboard projections."""
    with _LOCK:
        return {session: list(values) for session, values in _load_ack_store(repo_root).items()}


def acknowledge_hook_issue(
    repo_root: str,
    session: str,
    activities: Iterable[Any],
    issue_id: str,
) -> bool:
    """Acknowledge one real unresolved issue in user-owned, bounded state."""
    key = str(session or "").strip()[:160]
    target = str(issue_id or "").strip()[:160]
    if not key or not target:
        return False
    with _LOCK:
        store = _load_ack_store(repo_root)
        acknowledgements = list(store.get(key) or [])
        if not has_hook_issue(activities, acknowledgements, target):
            return False
        if target not in acknowledgements:
            acknowledgements.append(target)
        store[key] = acknowledgements[-_MAX_ACKS_PER_SESSION:]
        store = dict(list(store.items())[-_MAX_SESSIONS:])
        path = _store_path(repo_root)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps({"version": 1, "acks": store}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            temporary.replace(path)
            return True
        except (OSError, TypeError, ValueError):
            return False


def forget_hook_issue_session(repo_root: str, session: str) -> bool:
    """Remove one deleted session's acknowledgement state, if present."""
    key = str(session or "").strip()[:160]
    if not key:
        return False
    with _LOCK:
        store = _load_ack_store(repo_root)
        if key not in store:
            return False
        store.pop(key, None)
        path = _store_path(repo_root)
        try:
            if not store:
                path.unlink(missing_ok=True)
                return True
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps({"version": 1, "acks": store}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            temporary.replace(path)
            return True
        except (OSError, TypeError, ValueError):
            return False
