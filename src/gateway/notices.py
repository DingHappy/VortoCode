"""通知台账 —— 无人值守产出里"人该看到"的那一路（cron 例行作业 / heartbeat 值班）。

例行产出**不进任何人类会话历史**（run lane 规则，见 `cron.py`）：需要人知道的事落这条 JSONL 台账
（`.vortocode/logs/notices.jsonl`，`logs/` 已在 `.vortocode` 自忽略清单内），再由 GET /api/notices
查询、WS `notice` 广播、IM 推 owner 三路投递。**台账是唯一有持久保证的一路**，另两路 best-effort。

放在 gateway 而不是留在 web 路由里：CLI（`vc cron run`）与常驻调度循环都要写它，
不该为了记一条通知去导入整个 FastAPI 层。`src/web/routers/tasks.py` 从这里再导出，保持旧入口可用。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

MAX_NOTICES = 500


def _path(repo_root: str) -> Path:
    return Path(repo_root) / ".vortocode" / "logs" / "notices.jsonl"


def record_notice(repo_root: str, text: str, *, source: str = "scheduler") -> None:
    """往通知台账追加一条（JSONL，尾部截断到 MAX_NOTICES）。best-effort，出错不抛。"""
    p = _path(repo_root)
    try:
        from src.agents.dev_plan import ensure_state_gitignore
        ensure_state_gitignore(repo_root)
        p.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "source": str(source)[:120], "text": str(text)[:2000]}, ensure_ascii=False)
        lines = []
        if p.is_file():
            lines = p.read_text(encoding="utf-8").splitlines()[-(MAX_NOTICES - 1):]
        lines.append(entry)
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(p)
    except (OSError, TypeError, ValueError):
        pass


def load_notices(repo_root: str, limit: int = 50) -> List[Dict[str, Any]]:
    """读通知台账尾部 N 条（新的在前）。无文件/坏行 → 尽量返回能解析的。"""
    p = _path(repo_root)
    if not p.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    except OSError:
        return []
    return list(reversed(out[-limit:]))
