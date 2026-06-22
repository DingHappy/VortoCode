"""项目级 agent 指令文件（对标 Claude Code 的 CLAUDE.md / opencode 的 AGENTS.md）。

在仓库根放一个 `AGENTS.md`（或 `CLAUDE.md`/`VORTO.md`），里面写项目约定、风格、注意事项；
主 agent 启动时把它读进系统提示，于是每一轮都"记得"项目规矩——无需用户每次重复。

UI 无关，TUI/网页/CLI 三端共用：各自在搭主 agent 时取一次拼进 extra_system。
"""

from pathlib import Path
from typing import Optional

# 按优先级找；取第一个存在且非空的（AGENTS.md = opencode 标准，CLAUDE.md = CC，VORTO.md = 本项目原生）。
# `.vortocode/AGENTS.md` 是本地（gitignored）私有指令，可放不想提交的约定。
_CANDIDATES = ["AGENTS.md", "CLAUDE.md", "VORTO.md", ".vortocode/AGENTS.md"]

_MAX_CHARS = 8000   # 截断防把上下文挤爆（项目指令该精炼，不是塞整本手册）


def find_instructions_file(repo_root: str) -> Optional[Path]:
    """返回按优先级找到的第一个存在且非空的项目指令文件路径；没有则 None。"""
    base = Path(repo_root)
    for name in _CANDIDATES:
        p = base / name
        try:
            if p.is_file() and p.read_text(encoding="utf-8", errors="ignore").strip():
                return p
        except OSError:
            continue
    return None


def load_project_instructions(repo_root: str) -> str:
    """读项目指令文件、拼成可直接追加到系统提示的一段；没有则空串。

    超长截断（_MAX_CHARS），结尾标注被截断，避免悄悄丢内容又不撑爆上下文。
    """
    p = find_instructions_file(repo_root)
    if p is None:
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""
    if not text:
        return ""
    truncated = ""
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS]
        truncated = "\n…(项目指令过长已截断)"
    return (f"【项目指令】(来自 {p.name}，请遵循其中的约定/风格/注意事项)\n"
            f"{text}{truncated}")
