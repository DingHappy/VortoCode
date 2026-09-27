"""Durable, read-only review evidence for conversational dev_isolated results."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from src.gateway.audit import sanitize_audit_value
from src.gateway.git_review import normalize_git_path
from src.utils.state_dir import ensure_state_gitignore

_MAX_FILES = 2000
_MAX_DIFF = 2_000_000


def _git(root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=30)


def _root(repo_root: str) -> str:
    result = _git(repo_root, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise ValueError("当前 Runtime 目录不是 Git 仓库")
    return str(Path(result.stdout.strip()).resolve())


def _store(root: str) -> Path:
    return Path(root) / ".vortocode" / "isolated_deliveries"


def _redact_lines(value: object, limit: int) -> str:
    return "\n".join(str(sanitize_audit_value(line)) for line in str(value or "")[-limit:].splitlines())


def record_isolated_delivery(repo_root: str, *, branch: str, description: str,
                             base_oid: str, verification: dict, attempts: int) -> dict:
    """Only the successful implementation path calls this, after branch creation."""
    root = _root(repo_root)
    if not branch.startswith("vorto/") or not base_oid:
        raise ValueError("隔离交付缺少受控分支或基线")
    head = _git(root, "rev-parse", "--verify", f"refs/heads/{branch}")
    base = _git(root, "rev-parse", "--verify", f"{base_oid}^{{commit}}")
    if head.returncode != 0 or base.returncode != 0:
        raise ValueError("隔离交付分支或基线不存在")
    head_oid = head.stdout.strip()
    record = {
        "id": head_oid,
        "branch": branch,
        "base_oid": base.stdout.strip(),
        "head_oid": head_oid,
        "description": _redact_lines(description, 500),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attempts": max(1, int(attempts)),
        "verification": {
            "ok": bool(verification.get("ok")),
            "skipped": bool(verification.get("skipped")),
            "cmd": _redact_lines(verification.get("cmd"), 500),
            "output": _redact_lines(verification.get("output"), 8000),
        },
    }
    ensure_state_gitignore(root)
    folder = _store(root)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{head_oid}.json"
    temporary = folder / f"{head_oid}.json.tmp"
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return record


def _records(root: str) -> list[dict]:
    records = []
    for path in _store(root).glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(item, dict) and item.get("id") == path.stem:
            records.append(item)
    return sorted(records, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def _record(root: str, delivery_id: str) -> dict:
    if len(delivery_id) != 40 or any(char not in "0123456789abcdef" for char in delivery_id):
        raise ValueError("无效的隔离交付 ID")
    try:
        item = json.loads((_store(root) / f"{delivery_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("隔离交付不存在") from error
    if not isinstance(item, dict) or item.get("id") != delivery_id:
        raise ValueError("隔离交付记录无效")
    return item


def _current(root: str, item: dict) -> tuple[str, bool]:
    branch = str(item.get("branch") or "")
    if not branch.startswith("vorto/"):
        raise ValueError("隔离交付分支无效")
    head = _git(root, "rev-parse", "--verify", f"refs/heads/{branch}")
    if head.returncode != 0:
        return "", False
    oid = head.stdout.strip()
    return oid, oid == item.get("head_oid")


def list_isolated_deliveries(repo_root: str, limit: int = 30) -> list[dict]:
    root = _root(repo_root)
    result = []
    for item in _records(root)[:max(1, min(limit, 100))]:
        head, unchanged = _current(root, item)
        result.append({**item, "current_head": head, "unchanged": unchanged})
    return result


def isolated_delivery_snapshot(repo_root: str, delivery_id: str) -> dict:
    root = _root(repo_root)
    item = _record(root, delivery_id)
    head, unchanged = _current(root, item)
    if not head:
        raise ValueError("隔离交付分支已不存在")
    base_oid = str(item.get("base_oid") or "")
    if _git(root, "cat-file", "-e", f"{base_oid}^{{commit}}").returncode != 0:
        raise ValueError("隔离交付基线已不存在")
    diff = _git(root, "diff", "--name-status", "-z", "--find-renames", f"{base_oid}..{head}")
    if diff.returncode != 0:
        raise ValueError("读取隔离分支文件失败")
    tokens = [token for token in diff.stdout.split("\0") if token]
    files = []
    index = 0
    while index < len(tokens) and len(files) < _MAX_FILES:
        status = tokens[index]
        index += 1
        if status.startswith(("R", "C")):
            if index + 1 >= len(tokens):
                break
            original = normalize_git_path(tokens[index])
            path = normalize_git_path(tokens[index + 1])
            index += 2
        else:
            if index >= len(tokens):
                break
            original = ""
            path = normalize_git_path(tokens[index])
            index += 1
        files.append({"path": path, "original_path": original, "status": status})
    return {**item, "current_head": head, "unchanged": unchanged,
            "files": files, "truncated": index < len(tokens)}


def isolated_delivery_diff(repo_root: str, delivery_id: str, path: str) -> dict:
    snapshot = isolated_delivery_snapshot(repo_root, delivery_id)
    path = normalize_git_path(path)
    if not any(item["path"] == path for item in snapshot["files"]):
        raise ValueError("文件已不在隔离交付改动列表中，请刷新")
    result = _git(_root(repo_root), "diff", "--no-ext-diff", "--unified=3", "--find-renames",
                  f"{snapshot['base_oid']}..{snapshot['current_head']}", "--", path)
    if result.returncode != 0:
        raise ValueError("读取隔离交付 diff 失败")
    if len(result.stdout) > _MAX_DIFF:
        raise ValueError("单文件 diff 超过 2,000,000 字符，请在外部 Git 工具审查")
    return {"ok": True, "path": path, "diff": result.stdout, "head": snapshot["current_head"],
            "unchanged": snapshot["unchanged"]}
