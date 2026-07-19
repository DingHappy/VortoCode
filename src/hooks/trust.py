"""User-owned trust store for repository lifecycle hooks.

Repository hook files can execute commands or call HTTP endpoints.  Trust must
therefore live outside the repository: a malicious checkout must not be able to
commit its own approval next to ``.vortocode/hooks.yaml``.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def trust_store_path() -> Path:
    override = os.getenv("VORTOCODE_HOOK_TRUST_STORE", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "posix" and Path.home().joinpath("Library").is_dir():
        return Path.home() / "Library" / "Application Support" / "VortoCode" / "trusted-folders.json"
    return Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "vortocode" / "trusted-folders.json"


def _canonical(repo_root: str) -> str:
    try:
        return str(Path(repo_root).expanduser().resolve(strict=True))
    except (OSError, RuntimeError):
        return ""


def _load() -> Dict[str, Any]:
    path = trust_store_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"version": _VERSION, "folders": {}}
    folders = value.get("folders") if isinstance(value, dict) else None
    if not isinstance(folders, dict):
        folders = {}
    return {"version": _VERSION, "folders": folders}


def is_project_trusted(repo_root: str) -> bool:
    root = _canonical(repo_root)
    return bool(root and root in _load()["folders"])


def set_project_trusted(repo_root: str, trusted: bool) -> bool:
    """Grant or revoke trust for one existing Git worktree using an atomic user-owned file."""
    root = _canonical(repo_root)
    if not root or not (Path(root) / ".git").exists():
        return False
    state = _load()
    folders = state["folders"]
    if trusted:
        folders[root] = {"trusted_at": _now()}
    else:
        folders.pop(root, None)
    path = trust_store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(path)
        return True
    except (OSError, TypeError, ValueError):
        return False


def hook_config_status(repo_root: str) -> Dict[str, Any]:
    """Return a bounded, non-executing preview for Desktop/TUI trust review."""
    import hashlib
    import urllib.parse
    import yaml

    root = _canonical(repo_root)
    config = Path(root) / ".vortocode" / "hooks.yaml" if root else Path()
    configured = bool(root and config.is_file())
    trusted = bool(configured and is_project_trusted(root))
    hooks = []
    digest = ""
    error = ""
    if configured:
        try:
            raw = config.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()[:16]
            value = yaml.safe_load(raw.decode("utf-8")) or {}
            entries = value.get("hooks") if isinstance(value, dict) else []
            if not isinstance(entries, list):
                raise ValueError("hooks 必须是列表")
            for index, entry in enumerate(entries[:50]):
                if not isinstance(entry, dict):
                    continue
                kind = str(entry.get("type") or "command")[:20]
                target = str(entry.get("command") or entry.get("url") or "")
                raw_events = entry.get("event_types") or []
                if isinstance(raw_events, str):
                    raw_events = [raw_events]
                elif not isinstance(raw_events, list):
                    raw_events = []
                try:
                    timeout = max(1, min(int(entry.get("timeout") or 5), 60))
                except (TypeError, ValueError):
                    timeout = 5
                from src.hooks.hook import HookCapability, normalize_hook_capabilities
                requested = normalize_hook_capabilities(entry.get("capabilities"))
                allowed = {HookCapability.OBSERVE_EVENT, HookCapability.EMIT_ANNOTATION}
                if "pre_tool_use" in {str(item) for item in raw_events}:
                    allowed.add(HookCapability.BLOCK_TOOL)
                action = {
                    "command": HookCapability.RUN_COMMAND,
                    "http": HookCapability.SEND_HTTP,
                    "prompt": HookCapability.REQUEST_MODEL,
                }.get(kind)
                if action is not None:
                    allowed.add(action)
                effective = allowed if requested is None else (
                    {HookCapability.OBSERVE_EVENT} | (allowed & requested)
                )
                if kind == "http" and target:
                    parsed = urllib.parse.urlsplit(target)
                    target = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
                hooks.append({
                    "name": str(entry.get("name") or f"hook-{index + 1}")[:80],
                    "type": kind,
                    "events": [str(item)[:40] for item in raw_events[:20]],
                    "matcher": str(entry.get("matcher") or "")[:160],
                    "target": target[:240],
                    "timeout": timeout,
                    "capabilities": sorted(item.value for item in effective),
                })
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            error = str(exc)[:300]
    return {
        "configured": configured,
        "trusted": trusted,
        "active": bool(configured and trusted and not error),
        "config_path": ".vortocode/hooks.yaml" if configured else "",
        "config_sha256": digest,
        "hooks": hooks,
        "error": error,
    }
