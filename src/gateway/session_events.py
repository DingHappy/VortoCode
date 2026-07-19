"""Durable per-session protocol event journal.

Each segment is append-only JSONL.  Segments rotate by size and the oldest are
pruned as a bounded retention policy; a torn final line is ignored on load.
Only stable ``sid-`` sessions persist.  Ephemeral ``ws-`` connections remain
in memory and disappear with the process.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

_BAD = re.compile(r"[^A-Za-z0-9_-]")
_MAX_SEGMENT_BYTES = 2 * 1024 * 1024
_MAX_SEGMENTS = 4
_MAX_EVENT_BYTES = 1024 * 1024
_MAX_LOAD_EVENTS = 2000


def _safe_sid(key: str) -> Optional[str]:
    if not key.startswith("sid-"):
        return None
    sid = _BAD.sub("_", key[len("sid-"):]).strip("_")
    return sid or None


class SessionEventJournal:
    """Append, recover and clear one session's bounded event segments."""

    def __init__(self, repo_root: str, key: str):
        self.repo_root = str(repo_root)
        self.sid = _safe_sid(str(key))
        self.directory = (
            Path(repo_root) / ".vortocode" / "session_events" / self.sid
            if self.sid else None
        )

    def _segments(self) -> list[Path]:
        if self.directory is None or not self.directory.is_dir():
            return []
        return sorted(self.directory.glob("*.jsonl"))

    def load(self, limit: int = 500) -> list[tuple[int, Dict[str, Any]]]:
        """Return valid events ordered by sequence, keeping the requested tail."""
        if self.directory is None:
            return []
        by_seq: dict[int, Dict[str, Any]] = {}
        for path in self._segments():
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    payload = json.loads(line)
                    seq = payload.get("seq")
                    event = payload.get("event")
                    event_shape_ok = (
                        isinstance(event, dict)
                        and (isinstance(event.get("type"), str) or event.get("cursor_only") is True)
                    )
                    if (not isinstance(seq, int) or isinstance(seq, bool) or seq < 1
                            or not event_shape_ok):
                        continue
                    by_seq[seq] = event
                except (TypeError, ValueError):
                    continue
        ordered = sorted(by_seq.items())
        bounded = max(1, min(int(limit or 1), _MAX_LOAD_EVENTS))
        return ordered[-bounded:]

    def append(self, seq: int, event: Dict[str, Any]) -> bool:
        if self.directory is None or seq < 1 or not isinstance(event, dict):
            return False
        record = json.dumps(
            {"seq": int(seq), "event": event}, ensure_ascii=False, separators=(",", ":"),
        ) + "\n"
        encoded = record.encode("utf-8")
        if len(encoded) > _MAX_EVENT_BYTES:
            return False
        try:
            from src.agents.dev_plan import ensure_state_gitignore

            ensure_state_gitignore(self.repo_root)
            self.directory.mkdir(parents=True, exist_ok=True)
            segments = self._segments()
            target = segments[-1] if segments else self.directory / f"{seq:020d}.jsonl"
            if target.is_file() and target.stat().st_size + len(encoded) > _MAX_SEGMENT_BYTES:
                target = self.directory / f"{seq:020d}.jsonl"
            with target.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
            segments = self._segments()
            for stale in segments[:-_MAX_SEGMENTS]:
                stale.unlink(missing_ok=True)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def clear(self) -> bool:
        if self.directory is None or not self.directory.exists():
            return False
        changed = False
        try:
            for path in self._segments():
                path.unlink(missing_ok=True)
                changed = True
            self.directory.rmdir()
            parent = self.directory.parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
            return changed
        except OSError:
            return changed
