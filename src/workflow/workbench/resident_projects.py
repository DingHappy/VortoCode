"""常驻电脑上的项目登记表：这台服务器上有哪些项目、各自的运行时听在哪个端口。

Desktop 工作台的「在哪做：本机 / 常驻电脑」靠它找到同名项目在服务器上的运行时；之后助理
派活也从这里查目标项目。登记表由运维（装服务的人）写在 ``~/.vortocode/resident_projects.json``：

    {"projects": [{"name": "VortoCode", "repo_root": "/home/me/projects/VortoCode", "port": 8082}]}

只读、只做格式校验：不探测端口、不读项目内容。条目不合法就跳过，不让一条坏数据拖垮整张表。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

_MAX_PROJECTS = 50


def registry_path() -> Path:
    override = os.getenv("VORTOCODE_RESIDENT_PROJECTS", "").strip()
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
    return {"name": name, "repo_root": repo_root.rstrip("/") or "/", "port": port}


def load_resident_projects() -> List[Dict[str, Any]]:
    """读登记表；文件不存在、不是 JSON 或结构不对都当作「没有项目」。同名只留第一条。"""
    try:
        data = json.loads(registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("projects") if isinstance(data, dict) else None
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
