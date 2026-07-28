"""技能（SKILL.md）：登记表 + use_skill / save_skill。

save_skill 是写操作，走注入的 confirm 门；args/read_only 与 TUI 版逐字一致（三端契约，
见 tests/unit/test_three_end_parity.py）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.agents.tool import Tool


@dataclass
class LoadedSkill:
    """一个已加载的技能：元数据 + 正文指令（progressive disclosure 的"完整指令"部分）。"""

    name: str
    description: str
    instructions: str


class SkillRegistry:
    """SKILL.md 技能注册表（复用 src.skills.parser.SkillParser；整合 OpenClaw/Claude Code 的技能思路）。

    渐进式披露：只把 name+description 放进系统提示（catalog），完整正文等 use_skill 时才加载。
    """

    def __init__(self, skill_dirs: list[str]) -> None:
        self._dirs = list(skill_dirs)
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
                meta, instructions, _args, _hooks = SkillParser.parse_file(path)
            except Exception:  # noqa: BLE001
                continue
            name = (meta.name or path.parent.name).strip()
            if name:
                self.skills[name] = LoadedSkill(name, (meta.description or "").strip(), instructions)
        return self

    def catalog(self) -> str:
        """给系统提示用的技能清单（仅 name+description）。无技能则空串。"""
        return "\n".join(f"- {s.name}：{s.description}" for s in self.skills.values())

    def get(self, name: str) -> Optional[LoadedSkill]:
        return self.skills.get((name or "").strip())


def _skill_registry_for(repo_root: str):
    from pathlib import Path as _P
    return SkillRegistry([str(_P(repo_root) / "skills"),
                          str(_P(repo_root) / ".vortocode" / "skills")]).load()


def skill_catalog(repo_root: str) -> str:
    """技能目录（name — description 多行串），供 CLI/Web 注入系统提示——让模型知道有哪些技能可 use_skill。"""
    try:
        return _skill_registry_for(repo_root).catalog()
    except Exception:  # noqa: BLE001
        return ""


def build_skill_tools(repo_root: str, confirm) -> list[Tool]:
    """UI 无关的技能工具（use_skill / save_skill）——三端同源。

    共享一个 registry 实例：save_skill 写盘后重扫，同回合 use_skill 立刻能加载到。save_skill 是写操作，
    走注入的 async confirm 门（同 run_command/open_pr）。args/read_only 与 TUI 版逐字一致（三端契约）。
    """
    import re as _re
    registry = _skill_registry_for(repo_root)

    async def _use_skill(args: dict) -> str:
        name = str(args.get("name") or args.get("skill") or "").strip()
        sk = registry.get(name)
        if not sk:
            avail = "、".join(registry.skills) or "（无）"
            return f"没有名为 {name} 的技能。可用：{avail}"
        return (f"【技能「{sk.name}」完整指令】请据此执行（用你的其它工具完成），"
                f"不要原样复述给用户：\n\n{sk.instructions}")

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
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\n{instr}\n", encoding="utf-8")
        registry.load()                              # 原地重扫：当前 agent 立刻能 use_skill 到它
        return f"已保存技能 {name} 到 .vortocode/skills/{name}/SKILL.md。"

    return [Tool("use_skill", "加载某个技能(SKILL.md)的完整指令到上下文，然后据此执行",
                 {"name": "技能名"}, _use_skill, read_only=True),
            Tool("save_skill", "把一套可复用流程保存成新技能(SKILL.md)到用户技能目录；写操作，需确认，仅 build",
                 {"name": "技能名", "description": "一句话描述", "instructions": "技能正文（自然语言步骤）"},
                 _save_skill, read_only=False)]
