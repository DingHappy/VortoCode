"""常驻电脑上的项目登记表：这台服务器上有哪些项目、各自的运行时听在哪个端口。

Desktop 工作台的「在哪做：本机 / 常驻电脑」靠它找到同名项目在服务器上的运行时；之后助理
派活也从这里查目标项目。登记表由运维（装服务的人）写在 ``~/.vortocode/resident_projects.json``：

    {"projects": [{"name": "VortoOne", "repo_root": "/home/me/projects/VortoCode", "port": 8082}]}

只读，不探测端口或读项目内容。旧条目保留格式兼容；带 instance_id 的固定目标要求私有宿主
文件且无歧义，配置不可信时拒绝，不降级成按名字猜测。见 docs/RESIDENT_EXECUTION_IDENTITY.md。
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, List

_MAX_PROJECTS = 50


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate resident configuration key")
        result[key] = value
    return result


def registry_path() -> Path:
    override = os.getenv("VORTOONE_RESIDENT_PROJECTS", "").strip()
    return Path(override) if override else Path.home() / ".vortocode" / "resident_projects.json"


def _entry(raw: Any) -> Dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    repo_root = str(raw.get("repo_root") or "").strip()
    port = raw.get("port")
    if not name or len(name) > 100 or not repo_root.startswith("/"):
        return None
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        return None
    entry = {"name": name, "repo_root": repo_root.rstrip("/") or "/", "port": port}
    if "instance_id" in raw:
        value = raw["instance_id"]
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
            return None
        entry["instance_id"] = value
    return entry


def load_resident_projects() -> List[Dict[str, Any]]:
    """读登记表；文件不存在、不是 JSON 或结构不对都当作「没有项目」。同名只留第一条。"""
    try:
        if registry_path().stat().st_size > 256 * 1024:
            return []
        data = json.loads(registry_path().read_text(encoding="utf-8"), object_pairs_hook=_object)
    except (OSError, ValueError):
        return []
    items = data.get("projects") if isinstance(data, dict) else None
    if isinstance(items, list) and any(isinstance(row, dict) and "instance_id" in row for row in items):
        # Pinned execution targets must come from private host configuration.
        path = registry_path()
        try:
            info = path.lstat()
            if (not path.is_absolute() or not stat.S_ISREG(info.st_mode) or path.parent.is_symlink()
                    or (os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077
                                            or path.parent.stat().st_mode & 0o022))
                    or any(isinstance(row, dict) and isinstance(row.get("repo_root"), str)
                           and Path(row["repo_root"]).resolve() in path.resolve().parents for row in items)):
                return []
        except OSError:
            return []
        names = [str(row.get("name", "")).strip().lower() for row in items if isinstance(row, dict)]
        identities = [row["instance_id"] for row in items if isinstance(row, dict) and isinstance(row.get("instance_id"), str)]
        if len(names) != len(set(names)) or len(identities) != len(set(identities)):
            return []  # No first-name-wins selection for explicitly pinned targets.
    projects: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in items if isinstance(items, list) else []:
        entry = _entry(raw)
        if entry and entry["name"].lower() not in seen:
            seen.add(entry["name"].lower())
            projects.append(entry)
        if len(projects) >= _MAX_PROJECTS:
            break
    return projects
