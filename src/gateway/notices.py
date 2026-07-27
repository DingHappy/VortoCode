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


def make_notifier(repo_root: str):
    """造三路通知投递器：①持久台账 ②WS 广播 ③IM 推 owner。每路 best-effort，一路挂不拖另两路。

    **凡是"跑完要让人知道"的路径都必须用它**，别只调 `record_notice`——那只写盘，人手机上什么
    都收不到。真机 2026-07-27 就栽在这儿：`cron_run` 工具手动触发时没传 notify，于是作业跑完
    结果只落台账，主人在钉钉等了半天没等到，而工具的注释还写着"announce 推 IM"。
    调度循环（scheduler_loop）传了、REST 触发路由传了，唯独我新加的那个工具漏了——
    **同一件事有三个调用方，第三个总会漏**，所以投递器上收到这里一份。

    从 web 路由上移到 gateway：agents 层的 cron 工具要用它，不该为了推一条通知去导入 FastAPI 层。
    `src/web/routers/tasks.py` 保留同名再导出，旧入口不变。
    """
    async def _notify(text) -> None:
        record_notice(repo_root, str(text))            # ① 持久台账（唯一有持久保证的一路）
        try:
            from src.web import task_events
            task_events.broadcast_notice(str(text))    # ② WS 广播给连着的客户端
        except Exception:  # noqa: BLE001
            pass
        try:
            from src.gateway.im_runtime import notify_owner
            await notify_owner(f"🔔 {text}")           # ③ IM 推已配对 owner（没内嵌 bridge 即 no-op）
        except Exception:  # noqa: BLE001
            pass
    return _notify


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
