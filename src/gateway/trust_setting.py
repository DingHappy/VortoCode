"""每个工作区的授权档位——用户选的那一档存在哪、怎么读。

档位的**语义**在 `src/agents/trust.py`（档位表 + 能力档案上限），这里只管"这个工作区当前选了
哪一档"的读写与落盘。分开是因为前者是安全内核（被 mypy 圈住、无 IO），后者是一段普通状态。

落盘在 `.vortocode/trust.json`（gitignored）：换句话说**档位跟着工作区走，不跟着会话走**——
用户在 Desktop 里给"我自己的项目"选了完全信任，不该因为开了个新会话又退回每次确认。

读取永远兜到最严：文件不存在 / 坏了 / 写着不认识的值 → ASK。写坏一个字不会把门开大。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src.agents.trust import ASK, LEVELS, normalize

_FILENAME = "trust.json"


def _path(repo_root: str) -> Path:
    return Path(repo_root) / ".vortocode" / _FILENAME


def get_trust_level(repo_root: str) -> str:
    """这个工作区当前的授权档位；读不到或不认识一律 ASK。"""
    try:
        data = json.loads(_path(repo_root).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 —— 文件缺失/损坏/无权限都按最严处理
        return ASK
    if not isinstance(data, dict):
        return ASK
    return normalize(data.get("level"))


def set_trust_level(repo_root: str, level: Optional[str]) -> str:
    """写入档位并返回**实际生效**的那一档（非法值收敛成 ASK）。"""
    value = normalize(level)
    target = _path(repo_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    from src.utils.state_dir import ensure_state_gitignore

    ensure_state_gitignore(repo_root)
    target.write_text(json.dumps({"level": value}, ensure_ascii=False), encoding="utf-8")
    return value


def available_levels() -> tuple[str, ...]:
    return LEVELS
