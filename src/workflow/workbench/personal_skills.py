"""Version-approved instruction skills in the assistant's managed data directory.

Loading instructions is not script execution, task completion or extra authority.
No project discovery, scheduler or execution ledger is introduced here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from contextlib import contextmanager
from itertools import islice
from pathlib import Path

import yaml

from src.skills.skill import SkillMetadata
from src.utils.state_dir import ensure_state_gitignore

MAX_BYTES = 64 * 1024
MAX_SKILLS = 100
_ID = re.compile(r"[a-z][a-z0-9_-]{0,47}")


class SkillConflict(ValueError):
    pass


class PersonalSkills:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.base = self.root / ".vortocode" / "personal-skills"

    def _path(self, skill_id: str, leaf: str = "SKILL.md") -> Path:
        if not isinstance(skill_id, str) or not _ID.fullmatch(skill_id):
            raise ValueError("技能 ID 只支持小写字母、数字、下划线或短横线，最长 48 字符")
        path = self.base / skill_id / leaf
        for part in (self.base.parent, self.base, path.parent, path):
            if part.is_symlink():
                raise ValueError("个人技能路径不可使用符号链接")
        return path

    def _read(self, skill_id: str, leaf: str = "SKILL.md", limit: int = MAX_BYTES) -> bytes:
        path = self._path(skill_id, leaf)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise ValueError("技能文件格式无效或超过大小限制")
            raw = handle.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("技能文件超过大小限制")
            return raw

    @contextmanager
    def _lock(self, skill_id: str):
        path = self._path(skill_id, ".lock")
        ensure_state_gitignore(str(self.root))
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "r+b") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("技能锁文件无效")
            if sys.platform == "win32":
                import msvcrt
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"\0"); handle.flush()
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    raise SkillConflict("技能正在更新，请刷新后重试") from None
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise SkillConflict("技能正在更新，请刷新后重试") from None
            yield  # Closing releases the stable lock inode; never unlink it.

    def _write(self, skill_id: str, leaf: str, raw: bytes):
        path = self._path(skill_id, leaf)
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw); handle.flush(); os.fsync(handle.fileno())
            self._path(skill_id, leaf)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def get(self, skill_id: str, *, preview: bool = False) -> dict:
        raw = self._read(skill_id)
        revision = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8")
        header = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.S)
        fields = (yaml.safe_load(header[1]) or {}) if header else {}
        if not isinstance(fields, dict):
            raise ValueError("技能元数据格式无效")
        metadata = SkillMetadata(name=fields.get("name", skill_id), description=fields.get("description", ""),
                                 capabilities=fields.get("capabilities", []),
                                 disable_model_invocation=fields.get("disable-model-invocation", False))
        instructions = (text[header.end():] if header else text).strip()
        if not instructions.strip():
            raise ValueError("技能正文不能为空")
        try:
            approval = json.loads(self._read(skill_id, "approval.json", 2048))
            enabled = isinstance(approval, dict) and approval.get("enabled") is True and approval.get("revision") == revision
        except (OSError, ValueError):
            enabled = False
        from src.memory.write_policy import redact_secret_like
        def safe(value, limit):
            return redact_secret_like(value.replace("\0", ""))[0].strip()[:limit]
        item = {"id": skill_id, "name": safe(metadata.name, 80),
                "description": safe(metadata.description, 300), "revision": revision,
                "enabled": enabled, "source_scope": "personal", "status": "enabled" if enabled else "needs_review",
                "capabilities": [safe(value, 60) for value in metadata.capabilities[:20]],
                "model_invocable": not metadata.disable_model_invocation,
                "detail": "仅加载指令；不执行 Hook 或脚本，不增加工具权限。"}
        if preview:
            item["instructions"] = instructions
        return item

    def list(self) -> dict:
        self._path("check")  # Check the managed ancestors even when the directory is empty.
        items, issues = [], []
        paths = list(islice(self.base.iterdir(), MAX_SKILLS + 1)) if self.base.exists() else []
        for path in paths[:MAX_SKILLS]:
            if not _ID.fullmatch(path.name):
                continue
            try:
                items.append(self.get(path.name))
            except FileNotFoundError:
                continue
            except Exception:  # Invalid local files must not expose parser errors or paths.
                issues.append("部分个人技能无法读取，请检查格式、文件大小和符号链接。")
        return {"items": sorted(items, key=lambda item: item["id"]),
                "issues": list(dict.fromkeys(issues)), "truncated": len(paths) > MAX_SKILLS}

    def save(self, skill_id: str, body: dict) -> dict:
        if set(body) - {"name", "description", "instructions", "revision"}:
            raise ValueError("不支持的技能字段")
        values = {}
        for field, limit in (("name", 80), ("description", 300), ("instructions", 60000)):
            value = body.get(field, "")
            if not isinstance(value, str) or len(value) > limit or "\0" in value:
                raise ValueError("技能文本无效或超过长度限制")
            values[field] = value.strip()
        if not values["name"] or not values["instructions"]:
            raise ValueError("技能名称和正文不能为空")
        header = yaml.safe_dump({"name": values["name"], "description": values["description"]}, allow_unicode=True, sort_keys=False)
        raw = f"---\n{header}---\n\n{values['instructions']}\n".encode("utf-8")
        if len(raw) > MAX_BYTES:
            raise ValueError("技能文件不能超过 64 KiB")
        with self._lock(skill_id):
            try:
                current = hashlib.sha256(self._read(skill_id)).hexdigest()
            except FileNotFoundError:
                current = None
            if body.get("revision") != current:
                raise SkillConflict("技能已变化，请刷新并重新审阅")
            if current is None and len(list(islice(self.base.iterdir(), MAX_SKILLS + 1))) > MAX_SKILLS:
                raise ValueError("个人技能数量已达上限")
            # Even saving identical content requires fresh approval; disable first.
            self._write(skill_id, "approval.json", b'{"enabled": false}')
            self._write(skill_id, "SKILL.md", raw)
            return self.get(skill_id)

    def set_enabled(self, skill_id: str, *, enabled: bool, revision: str) -> dict:
        if type(enabled) is not bool or not isinstance(revision, str):
            raise ValueError("需要明确的启停状态和审阅版本")
        with self._lock(skill_id):
            item = self.get(skill_id)
            if item["revision"] != revision:
                raise SkillConflict("技能已变化，请刷新并重新审阅")
            self._write(skill_id, "approval.json", json.dumps({"enabled": enabled, "revision": revision}).encode())
            return self.get(skill_id)

    def load_instructions(self, skill_id: str) -> dict:
        # Serialize with UI mutations; each invocation rechecks approval and content.
        self.get(skill_id)  # A missing ID must not create directories or lock files.
        with self._lock(skill_id):
            item = self.get(skill_id, preview=True)
            if not item["enabled"] or not item["model_invocable"]:
                raise ValueError("技能未启用、版本已变化或禁止模型调用，请由用户审阅")
            return item
