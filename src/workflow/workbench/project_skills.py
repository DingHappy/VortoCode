"""Desktop 插件页的项目技能管理：在 Desktop 里新建、查看、编辑、删除项目的 SKILL.md。

两个位置，与 agent 加载技能的目录一致（``agents/tools/skills.py:_skill_registry_for``）：
- ``shared``：``skills/<id>/SKILL.md``，随仓库提交、团队共享（改动会出现在 git 里）；
- ``local``：``.vortocode/skills/<id>/SKILL.md``，只在本机、不入库（``save_skill`` 也写这里）。

编辑只换 name / description / 正文，frontmatter 里的其它字段（version、arguments、tools…）原样保留。
写入带版本号：文件在别处被改过就拒绝并要求刷新。删除只删 SKILL.md，同目录的脚本等文件不动。
技能只是指令文本：不执行脚本、不增加工具权限；已经开着的会话要新建会话或重载技能才看得到改动。
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

MAX_BYTES = 64 * 1024
MAX_SKILLS = 200
SCOPES = {"shared": ("skills",), "local": (".vortocode", "skills")}
_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}")
_HEADER = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.S)
_LOCK = threading.Lock()


class SkillError(ValueError):
    """技能不合法、不存在或状态不允许当前操作；文件未改动。"""


class SkillConflict(SkillError):
    """文件在别处被改过（版本号对不上）。"""


def _base(root: str, scope: str) -> Path:
    if scope not in SCOPES:
        raise SkillError("位置只能是 shared（随仓库）或 local（只在本机）")
    return Path(root).resolve().joinpath(*SCOPES[scope])


def _path(root: str, scope: str, skill_id: str) -> Path:
    if not isinstance(skill_id, str) or not _ID.fullmatch(skill_id):
        raise SkillError("技能 ID 只能用小写字母、数字、下划线或短横线，以字母开头，最长 48 个字符")
    base = _base(root, scope)
    path = base / skill_id / "SKILL.md"
    for part in (base, path.parent, path):
        if part.is_symlink():
            raise SkillError("技能路径不能是符号链接")
    return path


def _read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise SkillError("技能文件无效或超过 64KB")
        return handle.read(MAX_BYTES + 1)


def _split(text: str) -> tuple[Dict[str, Any], str]:
    match = _HEADER.match(text)
    if not match:
        return {}, text.strip()
    fields = yaml.safe_load(match.group(1)) or {}
    if not isinstance(fields, dict):
        raise SkillError("技能的 frontmatter 不是键值对")
    return fields, text[match.end():].strip()


def _item(root: str, scope: str, skill_id: str, *, preview: bool = False) -> Dict[str, Any]:
    raw = _read(_path(root, scope, skill_id))
    fields, body = _split(raw.decode("utf-8"))
    item = {"id": skill_id, "scope": scope, "name": str(fields.get("name") or skill_id)[:80],
            "description": str(fields.get("description") or "")[:300],
            "revision": hashlib.sha256(raw).hexdigest(),
            "model_invocable": not fields.get("disable-model-invocation", False),
            "extra_fields": sorted(key for key in fields if key not in ("name", "description"))}
    if preview:
        item["instructions"] = body
    return item


def list_skills(root: str) -> Dict[str, Any]:
    items, issues = [], []
    for scope in SCOPES:
        base = _base(root, scope)
        if not base.is_dir() or base.is_symlink():
            continue
        for directory in sorted(base.iterdir())[:MAX_SKILLS]:
            if not (directory / "SKILL.md").exists():
                continue
            if not _ID.fullmatch(directory.name):
                issues.append(f"{'/'.join(SCOPES[scope])}/{directory.name}：目录名不符合规则，只能手动编辑")
                continue
            try:
                items.append(_item(root, scope, directory.name))
            except (SkillError, OSError, ValueError, UnicodeDecodeError):
                issues.append(f"{'/'.join(SCOPES[scope])}/{directory.name}：读不了（格式、大小或符号链接）")
    return {"items": items, "issues": issues,
            "locations": {scope: "/".join(parts) for scope, parts in SCOPES.items()}}


def get_skill(root: str, scope: str, skill_id: str) -> Dict[str, Any]:
    try:
        return _item(root, scope, skill_id, preview=True)
    except FileNotFoundError:
        raise SkillError(f"没有这个技能：{skill_id}") from None


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_skill(root: str, scope: str, skill_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """新建（revision 为 None）或更新（revision 必须等于当前版本）。"""
    if set(body) - {"name", "description", "instructions", "revision"}:
        raise SkillError("不支持的字段")
    values = {}
    for field, limit in (("name", 80), ("description", 300), ("instructions", 60000)):
        value = body.get(field, "")
        if not isinstance(value, str) or len(value) > limit or "\0" in value:
            raise SkillError("技能文本无效或超过长度限制")
        values[field] = value.strip()
    if not values["name"] or not values["instructions"]:
        raise SkillError("技能名称和正文不能为空")
    path = _path(root, scope, skill_id)
    if scope == "local":
        from src.utils.state_dir import ensure_state_gitignore
        ensure_state_gitignore(root)
    with _LOCK:
        try:
            current: Optional[bytes] = _read(path)
        except FileNotFoundError:
            current = None
        revision = hashlib.sha256(current).hexdigest() if current is not None else None
        if body.get("revision") != revision:
            raise SkillConflict("技能已在别处改过（或已经存在），请刷新后再改")
        fields = _split(current.decode("utf-8"))[0] if current is not None else {}
        fields = {**fields, "name": values["name"], "description": values["description"]}
        header = yaml.safe_dump(fields, allow_unicode=True, sort_keys=False)
        raw = f"---\n{header}---\n\n{values['instructions']}\n".encode("utf-8")
        if len(raw) > MAX_BYTES:
            raise SkillError("技能文件不能超过 64KB")
        _atomic_write(path, raw)
    return get_skill(root, scope, skill_id)


def delete_skill(root: str, scope: str, skill_id: str, revision: str) -> None:
    path = _path(root, scope, skill_id)
    with _LOCK:
        try:
            current = _read(path)
        except FileNotFoundError:
            raise SkillError(f"没有这个技能：{skill_id}") from None
        if hashlib.sha256(current).hexdigest() != revision:
            raise SkillConflict("技能已在别处改过，请刷新后再删")
        path.unlink()
        try:
            path.parent.rmdir()                     # 目录里还有脚本等文件就留着
        except OSError:
            pass
