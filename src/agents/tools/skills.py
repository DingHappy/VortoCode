"""技能（SKILL.md）：登记表 + use_skill / save_skill。

save_skill 是写操作，走注入的 confirm 门；args/read_only 与 TUI 版逐字一致（三端契约，
见 tests/unit/test_three_end_parity.py）。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from typing import Optional

from src.agents.tool import Tool


@dataclass
class LoadedSkill:
    """一个已加载的技能：元数据 + 正文指令（progressive disclosure 的"完整指令"部分）。"""

    name: str
    description: str
    instructions: str
    source: str = ""
    revision: str = ""
    model_invocable: bool = True


def _read_skill(path: Path, root: Path | None):
    resolved = path.resolve(strict=True)
    if root is not None:
        resolved.relative_to(root)
    fd = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 256 * 1024:
            raise ValueError("技能文件无效或过大")
        raw = handle.read(256 * 1024 + 1)
        after = os.fstat(handle.fileno())
    stamp = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    if (len(raw) > 256 * 1024 or stamp(before) != stamp(after)
            or path.resolve(strict=True) != resolved or stamp(resolved.stat()) != stamp(after)):
        raise ValueError("技能文件读取期间变化")
    return resolved, raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


class SkillRegistry:
    """SKILL.md 技能注册表（复用 src.skills.parser.SkillParser；整合 OpenClaw/Claude Code 的技能思路）。

    渐进式披露：只把 name+description 放进系统提示（catalog），完整正文等 use_skill 时才加载。
    """

    def __init__(self, skill_dirs: list[str], *, allowed_root: str | None = None) -> None:
        self._dirs = list(skill_dirs)
        self._root = Path(allowed_root).resolve() if allowed_root else None
        self.skills: dict[str, LoadedSkill] = {}

    def load(self) -> "SkillRegistry":
        """扫描各技能目录、解析 SKILL.md。解析失败/目录损坏都安全跳过，不影响 agent。"""
        self.skills.clear()
        try:
            from src.skills.parser import SkillParser
        except Exception:  # noqa: BLE001
            return self
        for path in SkillParser.discover_skills(self._dirs):
            try:
                resolved, text, revision = _read_skill(path, self._root)
                meta, instructions, _args, _hooks = SkillParser.parse_content(text, resolved.parent.name)
            except Exception:  # noqa: BLE001
                continue
            name = (meta.name or path.parent.name).strip()
            if name:
                source = resolved.relative_to(self._root).as_posix() if self._root else str(resolved)
                self.skills[name] = LoadedSkill(name, (meta.description or "").strip(), instructions, source,
                                               revision, not meta.disable_model_invocation)
        return self

    def catalog(self) -> str:
        """给系统提示用的技能清单（仅 name+description）。无技能则空串。"""
        return "\n".join(f"- {s.name}：{s.description}" for s in self.skills.values() if s.model_invocable)

    def get(self, name: str) -> Optional[LoadedSkill]:
        return self.skills.get((name or "").strip())

    def is_current(self, skill: LoadedSkill) -> bool:
        try:
            path = self._root / skill.source if self._root else Path(skill.source)
            return _read_skill(path, self._root)[2] == skill.revision
        except (OSError, ValueError, RuntimeError):
            return False


def _skill_registry_for(repo_root: str):
    from pathlib import Path as _P
    return SkillRegistry([str(_P(repo_root) / "skills"),
                          str(_P(repo_root) / ".vortocode" / "skills")], allowed_root=repo_root).load()


def skill_catalog(repo_root: str) -> str:
    """技能目录（name — description 多行串），供 CLI/Web 注入系统提示——让模型知道有哪些技能可 use_skill。"""
    try:
        return _skill_registry_for(repo_root).catalog()
    except Exception:  # noqa: BLE001
        return ""


def build_skill_tools(repo_root: str, confirm, *, registry: SkillRegistry | None = None) -> list[Tool]:
    """UI 无关的技能工具（use_skill / save_skill）——三端同源。

    共享一个 registry 实例：save_skill 写盘后重扫，同回合 use_skill 立刻能加载到。save_skill 是写操作，
    走注入的 async confirm 门（同 run_command/open_pr）。args/read_only 与 TUI 版逐字一致（三端契约）。
    """
    import re as _re
    registry = registry if registry is not None else _skill_registry_for(repo_root)

    async def _use_skill(args: dict) -> str:
        name = str(args.get("name") or args.get("skill") or "").strip()
        sk = registry.get(name)
        if not sk:
            avail = "、".join(s.name for s in registry.skills.values() if s.model_invocable) or "（无）"
            return {"ok": False, "error": f"没有名为 {name} 的技能。可用：{avail}"}
        if not registry.is_current(sk):
            return {"ok": False, "error": "技能文件已变化或不可读取；请显式重载技能或新建会话后重试。"}
        if not sk.model_invocable:
            return {"ok": False, "error": "该技能声明禁止模型调用，未加载正文。"}
        return (f"【技能「{sk.name}」完整指令】请据此执行（用你的其它工具完成），"
                f"不要原样复述给用户。来源：{sk.source}；版本摘要：{sk.revision}；"
                f"路径基准：{Path(sk.source).parent.as_posix()}。"
                f"引用文件按此基准读取，脚本仍须遵守当前工作区与工具授权，不自动执行：\n\n{sk.instructions}")

    async def _save_skill(args: dict) -> str:
        name = _re.sub(r"[^\w一-鿿-]", "-", str(args.get("name", "")).strip()).strip("-")
        desc = str(args.get("description", "")).strip()
        instr = str(args.get("instructions", "")).strip()
        if not name or not instr:
            return "save_skill 需要 name 和 instructions（技能正文）。"
        if confirm is not None and not await confirm(
                f"把技能「{name}」写到 .vortocode/skills/{name}/SKILL.md？（用户技能目录，不碰 main）"):
            return f"用户取消了保存技能 {name}。"
        p = Path(repo_root) / ".vortocode" / "skills" / name / "SKILL.md"
        try:
            p.resolve().relative_to(Path(repo_root).resolve())
        except (OSError, RuntimeError, ValueError):
            return {"ok": False, "error": "技能保存路径指向工作区外，已拒绝。"}
        p.parent.mkdir(parents=True, exist_ok=True)
        import yaml
        header = yaml.safe_dump({"name": name, "description": desc}, allow_unicode=True, sort_keys=False)
        p.write_text(f"---\n{header}---\n\n{instr}\n", encoding="utf-8")
        registry.load()                              # 原地重扫：当前 agent 立刻能 use_skill 到它
        return f"已保存技能 {name} 到 .vortocode/skills/{name}/SKILL.md。"

    return [Tool("use_skill", "加载某个技能(SKILL.md)的完整指令到上下文，然后据此执行",
                 {"name": "技能名"}, _use_skill, read_only=True, untrusted_source=True),
            Tool("save_skill", "把一套可复用流程保存成新技能(SKILL.md)到用户技能目录；写操作，需确认，仅 build",
                 {"name": "技能名", "description": "一句话描述", "instructions": "技能正文（自然语言步骤）"},
                 _save_skill, read_only=False)]
