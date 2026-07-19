"""User-owned provenance ledger for Git review hunks.

The ledger deliberately lives outside the repository: checked-in or generated
project files must not be able to claim that an edit came from VortoCode's
Agent.  Only hashes of changed lines are retained; source code and prompts are
never copied into the ledger.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

_HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@(?P<section>.*)")
_MAX_RECORDS = 500
_LOCK = threading.Lock()
_VALID_SOURCES = frozenset({"agent", "user", "hook"})
_MAX_SIDE_EFFECT_FILES = 200
_MAX_TEXT_BYTES = 2 * 1024 * 1024


def stable_hunk_id(path: str, section: str, lines: Iterable[str]) -> str:
    """Content identity that excludes positional hunk-header line numbers."""
    body = "\n".join(str(line) for line in lines if not str(line).startswith("@@ "))
    material = f"{path}\0{section.strip()}\0{body}".encode("utf-8", errors="replace")
    return "h-" + hashlib.sha256(material).hexdigest()[:24]


def changed_line_tokens(lines: Iterable[str]) -> list[str]:
    """Return privacy-preserving identities for added/removed lines."""
    tokens = []
    for raw in lines:
        line = str(raw)
        if line.startswith(("+++", "---")) or not line.startswith(("+", "-")):
            continue
        tokens.append(hashlib.sha256(line.encode("utf-8", errors="replace")).hexdigest()[:24])
    return sorted(set(tokens))


def _repo_key(repo_root: str) -> str:
    root = str(Path(repo_root).resolve())
    return hashlib.sha256(root.encode("utf-8")).hexdigest()[:24]


def _store_path(repo_root: str) -> Path:
    override = os.getenv("VORTOCODE_CHANGE_SOURCE_STORE", "").strip()
    if override:
        target = Path(override).expanduser()
        return target if target.suffix else target / f"{_repo_key(repo_root)}.json"
    if os.name == "posix" and Path.home().joinpath("Library").is_dir():
        base = Path.home() / "Library" / "Application Support" / "VortoCode" / "change-sources"
    else:
        base = Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "vortocode" / "change-sources"
    return base / f"{_repo_key(repo_root)}.json"


def change_source_revision(repo_root: str) -> str:
    """Return a privacy-safe token that changes when provenance evidence changes."""
    path = _store_path(repo_root)
    try:
        stat = path.stat()
        material = f"{stat.st_mtime_ns}\0{stat.st_size}".encode("ascii")
    except OSError:
        material = b"missing"
    return hashlib.sha256(material).hexdigest()


def _normal_path(path: str) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    candidate = PurePosixPath(raw)
    if not raw or candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("change source path must be a repository-relative path")
    return candidate.as_posix()


def _git(repo_root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args], capture_output=True, timeout=20, check=False,
    )


def _status_paths(repo_root: str) -> list[str]:
    result = _git(repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if result.returncode != 0:
        return []
    chunks = result.stdout.split(b"\0")
    paths: list[str] = []
    index = 0
    while index < len(chunks):
        raw = chunks[index]
        index += 1
        if len(raw) < 4:
            continue
        status = raw[:2].decode("ascii", errors="ignore")
        path = raw[3:].decode("utf-8", errors="surrogateescape")
        if (status[0] in {"R", "C"} or status[1] in {"R", "C"}) and index < len(chunks):
            original = chunks[index].decode("utf-8", errors="surrogateescape")
            index += 1
            if original:
                paths.append(original)
        if path:
            paths.append(path)
        if len(paths) >= _MAX_SIDE_EFFECT_FILES:
            break
    output = []
    for value in paths:
        try:
            clean = _normal_path(value)
        except ValueError:
            continue
        if clean == ".vortocode" or clean.startswith(".vortocode/"):
            continue
        if clean not in output:
            output.append(clean)
    return output[:_MAX_SIDE_EFFECT_FILES]


def _read_text(path: Path) -> str | None:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > _MAX_TEXT_BYTES:
            return None
        raw = path.read_bytes()
        if b"\0" in raw:
            return None
        return raw.decode("utf-8")
    except (OSError, UnicodeError):
        return None


def capture_workspace_state(repo_root: str) -> dict[str, Any] | None:
    """Capture a bounded in-memory pre-command state without persisting source text."""
    try:
        requested = Path(repo_root).resolve(strict=True)
        top = _git(str(requested), "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            return None
        root = Path(top.stdout.decode().strip()).resolve(strict=True)
        tree = _git(str(root), "write-tree")
        tree_oid = tree.stdout.decode().strip() if tree.returncode == 0 else ""
        paths = _status_paths(str(root))
        return {
            "root": str(root),
            "tree": tree_oid,
            "files": {path: _read_text(root / path) for path in paths},
        }
    except (OSError, RuntimeError, UnicodeError):
        return None


def _tree_text(repo_root: str, tree: str, path: str) -> str | None:
    if not tree:
        return None
    result = _git(repo_root, "show", f"{tree}:{path}")
    if result.returncode != 0 or len(result.stdout) > _MAX_TEXT_BYTES or b"\0" in result.stdout:
        return None
    try:
        return result.stdout.decode("utf-8")
    except UnicodeError:
        return None


def record_workspace_side_effects(
    repo_root: str,
    before_state: dict[str, Any] | None,
    *,
    source: str,
    session: str = "",
    turn: str = "",
    tool: str = "",
) -> int:
    """Attribute text-file changes made by a command or lifecycle hook."""
    if not before_state or source not in _VALID_SOURCES:
        return 0
    try:
        requested = str(Path(repo_root).resolve(strict=True))
        top = _git(requested, "rev-parse", "--show-toplevel")
        root = str(Path(str(before_state.get("root") or "")).resolve(strict=True))
        if top.returncode != 0 or str(Path(top.stdout.decode().strip()).resolve()) != root:
            return 0
        before_files = before_state.get("files") if isinstance(before_state.get("files"), dict) else {}
        paths = list(dict.fromkeys([*before_files, *_status_paths(root)]))[:_MAX_SIDE_EFFECT_FILES]
        recorded = 0
        for path in paths:
            prior = before_files.get(path) if path in before_files else _tree_text(
                root, str(before_state.get("tree") or ""), path,
            )
            current = _read_text(Path(root) / path)
            # None may mean deleted, binary, oversized, or unreadable. Only treat it as
            # deletion when the path truly no longer exists; otherwise skip attribution.
            if prior is None and path in before_files:
                continue
            if current is None and (Path(root) / path).exists():
                continue
            old_text = prior or ""
            new_text = current or ""
            if old_text == new_text:
                continue
            record_change_source(
                root, path, old_text, new_text, source=source,
                session=session, turn=turn, tool=tool,
            )
            recorded += 1
        return recorded
    except Exception:  # noqa: BLE001 - provenance remains best-effort
        return 0


def _load(repo_root: str) -> list[dict[str, Any]]:
    path = _store_path(repo_root)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload.get("records") if isinstance(payload, dict) else None
        return [item for item in records if isinstance(item, dict)][-_MAX_RECORDS:] if isinstance(records, list) else []
    except (OSError, TypeError, ValueError):
        return []


def _save(repo_root: str, records: list[dict[str, Any]]) -> None:
    path = _store_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"version": 1, "records": records[-_MAX_RECORDS:]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _diff_hunks(path: str, before: str, after: str) -> list[dict[str, Any]]:
    diff = list(difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
    ))
    hunks: list[dict[str, Any]] = []
    current: list[str] = []
    section = ""
    for line in diff:
        if line.startswith("@@ "):
            if current:
                hunks.append({"section": section, "lines": current})
            match = _HUNK_RE.match(line)
            section = match.group("section").strip() if match else ""
            current = [line]
        elif current:
            current.append(line)
    if current:
        hunks.append({"section": section, "lines": current})
    return hunks


def record_change_source(
    repo_root: str,
    path: str,
    before: str,
    after: str,
    *,
    source: str,
    session: str = "",
    turn: str = "",
    tool: str = "",
) -> None:
    """Record one successful text edit. Failures are intentionally best-effort."""
    if before == after or source not in _VALID_SOURCES:
        return
    try:
        clean_path = _normal_path(path)
        hunks = _diff_hunks(clean_path, before, after)
        if not hunks:
            return
        record = {
            "path": clean_path,
            "source": source,
            "session": str(session or "")[:128],
            "turn": str(turn or "")[:128],
            "tool": str(tool or "")[:80],
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "after_sha256": hashlib.sha256(after.encode("utf-8")).hexdigest(),
            "hunks": [{
                "id": stable_hunk_id(clean_path, item["section"], item["lines"]),
                "tokens": changed_line_tokens(item["lines"]),
            } for item in hunks],
        }
        with _LOCK:
            records = _load(repo_root)
            records.append(record)
            _save(repo_root, records)
    except Exception:  # noqa: BLE001 - provenance must never break the edit
        return


def attribute_hunks(repo_root: str, path: str, hunks: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Classify current hunks as Agent, user, mixed, or untracked external edits."""
    clean_path = _normal_path(path)
    records = [item for item in reversed(_load(repo_root)) if item.get("path") == clean_path]
    output: list[dict[str, str]] = []
    for hunk in hunks:
        hunk_id = str(hunk.get("id") or "")
        current_tokens = set(changed_line_tokens(hunk.get("raw_lines") or []))
        exact: tuple[dict[str, Any], dict[str, Any]] | None = None
        matched: dict[str, set[str]] = {"agent": set(), "user": set(), "hook": set()}
        newest: dict[str, dict[str, Any]] = {}
        for record in records:
            for saved in record.get("hunks") or []:
                if not isinstance(saved, dict):
                    continue
                saved_tokens = set(saved.get("tokens") or [])
                overlap = current_tokens & saved_tokens
                if saved.get("id") == hunk_id and exact is None:
                    exact = (record, saved)
                if not overlap:
                    continue
                record_source = str(record.get("source") or "")
                if record_source in matched:
                    matched[record_source].update(overlap)
                    newest.setdefault(record_source, record)
        chosen: dict[str, Any] | None
        if exact is not None:
            source = str(exact[0].get("source") or "external")
            chosen = exact[0]
        else:
            active_sources = [name for name, tokens in matched.items() if tokens]
            if len(active_sources) == 1:
                only = active_sources[0]
                source = only if current_tokens and matched[only] == current_tokens else "mixed"
                chosen = newest.get(only)
            elif len(active_sources) > 1:
                source, chosen = "mixed", newest.get(active_sources[0])
            else:
                source, chosen = "external", None
        output.append({
            "source": source,
            "source_session": str((chosen or {}).get("session") or ""),
            "source_turn": str((chosen or {}).get("turn") or ""),
            "source_tool": str((chosen or {}).get("tool") or ""),
            "source_at": str((chosen or {}).get("at") or ""),
        })
    return output
