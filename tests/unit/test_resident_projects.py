"""常驻电脑的项目登记表：只做格式校验，坏条目跳过、坏文件当作空表。"""
from __future__ import annotations

import json

from src.gateway.workspace_scope import GENERAL, http_workspace_requirement
from src.workflow.workbench.resident_projects import load_resident_projects


def _write(tmp_path, monkeypatch, data):
    path = tmp_path / "resident_projects.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(path))


def test_valid_entries_kept_bad_ones_skipped(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"projects": [
        {"name": "VortoCode", "repo_root": "/home/me/projects/VortoCode/", "port": 8082},
        {"name": "relative", "repo_root": "projects/x", "port": 8083},       # 不是绝对路径
        {"name": "lowport", "repo_root": "/srv/x", "port": 22},              # 端口越界
        {"name": "boolport", "repo_root": "/srv/x", "port": True},
        {"name": "vortocode", "repo_root": "/srv/dup", "port": 8090},        # 同名（不分大小写）只留第一条
        "not-a-dict",
        {"name": "Notes", "repo_root": "/srv/notes", "port": 8084},
    ]})
    assert load_resident_projects() == [
        {"name": "VortoCode", "repo_root": "/home/me/projects/VortoCode", "port": 8082},
        {"name": "Notes", "repo_root": "/srv/notes", "port": 8084},
    ]


def test_missing_or_broken_registry_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(tmp_path / "absent.json"))
    assert load_resident_projects() == []
    _write(tmp_path, monkeypatch, "{broken")
    assert load_resident_projects() == []
    _write(tmp_path, monkeypatch, {"projects": "nope"})
    assert load_resident_projects() == []


def test_registry_readable_from_general_scope():
    # 常驻助理跑在通用范围：读登记表不需要项目工作区
    assert http_workspace_requirement("/api/resident/projects", GENERAL) is None
