"""Durable line-level review threads for the Desktop Git workbench."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from src.gateway.git_review import normalize_git_path, review_diff

_VALID_SCOPES = frozenset({"working", "staged"})
_VALID_SIDES = frozenset({"new", "old"})
_VALID_STATUSES = frozenset({"open", "sent", "resolved"})
_MAX_THREADS = 500
_MAX_BODY = 2_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ReviewConflict(ValueError):
    """The diff no longer matches the exact hunk reviewed by the user."""


class ReviewThreadStore:
    def __init__(self, repo_root: str):
        self.repo_root = str(repo_root)
        self.path = Path(repo_root) / ".vortocode" / "review_threads.json"

    @staticmethod
    def _clean_id(thread_id: str) -> str:
        value = str(thread_id or "")
        return value if re.fullmatch(r"review-[A-Za-z0-9_-]+", value) else ""

    def _load(self) -> list[Dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            items = payload.get("threads") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                return []
            return [item for item in items if isinstance(item, dict) and self._clean_id(item.get("id", ""))]
        except (OSError, TypeError, ValueError):
            return []

    def _save(self, items: list[Dict[str, Any]]) -> None:
        from src.agents.dev_plan import ensure_state_gitignore

        ensure_state_gitignore(self.repo_root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"threads": items[-_MAX_THREADS:]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def list(self) -> list[Dict[str, Any]]:
        return list(reversed(self._load()))

    def create(
        self,
        *,
        path: str,
        scope: str,
        hunk_id: str,
        expected_sha256: str,
        line: int,
        side: str,
        body: str,
    ) -> Dict[str, Any]:
        path = normalize_git_path(path)
        scope = str(scope or "working").strip().lower()
        side = str(side or "").strip().lower()
        hunk_id = str(hunk_id or "").strip()
        expected_sha256 = str(expected_sha256 or "").strip().lower()
        body = str(body or "").strip()
        try:
            line = int(line)
        except (TypeError, ValueError):
            raise ValueError("审查行号必须是整数") from None
        if scope not in _VALID_SCOPES:
            raise ValueError("审查 scope 只支持 working / staged")
        if side not in _VALID_SIDES:
            raise ValueError("审查 side 只支持 new / old")
        if line < 1:
            raise ValueError("审查行号必须大于 0")
        if not body or len(body) > _MAX_BODY or "\x00" in body:
            raise ValueError(f"审查评论不能为空且不能超过 {_MAX_BODY} 字符")
        if not hunk_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", hunk_id):
            raise ValueError("审查评论缺少有效 hunk id")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError("审查评论缺少当前 hunk 哈希")

        payload = review_diff(self.repo_root, path, scope=scope)
        hunk = next((item for item in payload["hunks"] if item["id"] == hunk_id), None)
        if hunk is None or hunk["sha256"] != expected_sha256:
            raise ReviewConflict("Diff 已变化，请刷新后重新添加评论")
        line_key = "new_line" if side == "new" else "old_line"
        if not any(item.get(line_key) == line for item in hunk["lines"]):
            raise ReviewConflict("评论行已不在当前 hunk 中，请刷新")

        now = _now()
        thread = {
            "id": "review-" + uuid.uuid4().hex[:12],
            "path": path,
            "scope": scope,
            "hunkId": hunk_id,
            "hunkSha256": expected_sha256,
            "baseline": str(payload.get("baseline") or ""),
            "line": line,
            "side": side,
            "body": body,
            "status": "open",
            "created": now,
            "updated": now,
            "sentAt": "",
            "resolvedAt": "",
        }
        items = self._load()
        items.append(thread)
        self._save(items)
        return thread

    def update_status(self, thread_id: str, status: str) -> Dict[str, Any] | None:
        clean = self._clean_id(thread_id)
        status = str(status or "").strip().lower()
        if not clean:
            return None
        if status not in _VALID_STATUSES:
            raise ValueError("审查状态只支持 open / sent / resolved")
        items = self._load()
        thread = next((item for item in items if item.get("id") == clean), None)
        if thread is None:
            return None
        now = _now()
        thread["status"] = status
        thread["updated"] = now
        if status == "sent":
            thread["sentAt"] = now
            thread["resolvedAt"] = ""
        elif status == "resolved":
            thread["resolvedAt"] = now
        else:
            thread["resolvedAt"] = ""
        self._save(items)
        return thread

    def delete(self, thread_id: str) -> bool:
        clean = self._clean_id(thread_id)
        if not clean:
            return False
        items = self._load()
        remaining = [item for item in items if item.get("id") != clean]
        if len(remaining) == len(items):
            return False
        self._save(remaining)
        return True
