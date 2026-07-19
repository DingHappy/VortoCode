"""Durable, privacy-bounded ownership metadata for live VortoCode worktrees.

Git already owns the worktree lifecycle.  This small generated-state ledger only
records which durable task/plan/session caused a VortoCode worktree to exist, so
Desktop can reconnect that live worktree to its handoff after a refresh or a
process restart.  Prompts and source code are deliberately never copied here.
"""
from __future__ import annotations

import contextvars
import json
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

_STORE = "worktree_bindings.json"
_MAX_BINDINGS = 256
_SAFE_ID = re.compile(r"[^A-Za-z0-9._/-]")
_LOCK = threading.RLock()
_OWNER: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "vortocode_worktree_owner", default={},
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _bounded(value: Any, limit: int = 180) -> str:
    return str(value or "").strip()[:limit]


def _worktree_id(value: Any) -> str:
    clean = _SAFE_ID.sub("_", _bounded(value, 220)).strip("/._")
    return clean[:180]


def _path(repo_root: str) -> Path:
    return Path(repo_root) / ".vortocode" / _STORE


def _read_unlocked(repo_root: str) -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(_path(repo_root).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    raw = payload.get("bindings") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {}
    output: dict[str, dict[str, str]] = {}
    for key, value in list(raw.items())[-_MAX_BINDINGS:]:
        wid = _worktree_id(key)
        if not wid or not isinstance(value, dict):
            continue
        output[wid] = {
            "worktree_id": wid,
            "task_id": _bounded(value.get("task_id")),
            "owner_session": _bounded(value.get("owner_session")),
            "plan_id": _bounded(value.get("plan_id")),
            "created": _bounded(value.get("created"), 40),
            "updated": _bounded(value.get("updated"), 40),
        }
    return output


def _write_unlocked(repo_root: str, bindings: Mapping[str, Mapping[str, str]]) -> None:
    from src.agents.dev_plan import ensure_state_gitignore

    ensure_state_gitignore(repo_root)
    target = _path(repo_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    limited = dict(list(bindings.items())[-_MAX_BINDINGS:])
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"version": 1, "bindings": limited}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def current_worktree_owner() -> dict[str, str]:
    """Return a copy of the ownership inherited by the current async/thread context."""
    return dict(_OWNER.get())


@contextmanager
def bind_worktree_owner(*, task_id: str = "", owner_session: str = "", plan_id: str = "") -> Iterator[None]:
    """Attach durable ownership to every worktree created inside this context.

    ``asyncio.to_thread`` copies ContextVars, so the metadata reaches the sync Git
    creation function without adding task arguments to every orchestration layer.
    Nested contexts only replace non-empty fields.
    """
    merged = current_worktree_owner()
    for key, value in {
        "task_id": task_id,
        "owner_session": owner_session,
        "plan_id": plan_id,
    }.items():
        bounded = _bounded(value)
        if bounded:
            merged[key] = bounded
    token = _OWNER.set(merged)
    try:
        yield
    finally:
        _OWNER.reset(token)


def record_worktree_binding(repo_root: str, worktree_id: str) -> dict[str, str] | None:
    """Persist the current owner for one live worktree; no owner means no record."""
    wid = _worktree_id(worktree_id)
    owner = current_worktree_owner()
    if not wid or not any(owner.get(key) for key in ("task_id", "owner_session", "plan_id")):
        return None
    with _LOCK:
        bindings = _read_unlocked(repo_root)
        prior = bindings.pop(wid, {})
        now = _now()
        record = {
            "worktree_id": wid,
            "task_id": _bounded(owner.get("task_id")),
            "owner_session": _bounded(owner.get("owner_session")),
            "plan_id": _bounded(owner.get("plan_id")),
            "created": _bounded(prior.get("created"), 40) or now,
            "updated": now,
        }
        bindings[wid] = record
        try:
            _write_unlocked(repo_root, bindings)
        except OSError:
            return None
        return dict(record)


def forget_worktree_binding(repo_root: str, worktree_id: str) -> None:
    """Remove an ownership record when its Git worktree is removed."""
    wid = _worktree_id(worktree_id)
    if not wid:
        return
    with _LOCK:
        bindings = _read_unlocked(repo_root)
        if bindings.pop(wid, None) is not None:
            try:
                _write_unlocked(repo_root, bindings)
            except OSError:
                pass


def list_worktree_bindings(repo_root: str) -> dict[str, dict[str, str]]:
    """Read a bounded snapshot keyed by VortoCode worktree ID."""
    with _LOCK:
        return {key: dict(value) for key, value in _read_unlocked(repo_root).items()}
