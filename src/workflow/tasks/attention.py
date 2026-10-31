"""「等你处理」：所有入口共用的一份注意力清单。

早晚报、问小智「有什么要我处理的」都读这里，Desktop 的决策中心读的是同一个决策队列——
同一件事不该在一个入口是「等你处理」、在另一个入口看不见。

来源：决策队列（目标阻塞、子 Agent 提问、委派待验收、后台任务失败、最新一次仍失败的运行）
+ 常驻助理任务单里等你的（待确认下发、在等你回答、下发失败、提交结果待核对）
+ 助理替你拟给 Codex、等你批准的话，以及仍未核对的结果不明动作。
不含实时操作确认：那只存在于正在进行的会话里，要在那个会话当场回答；早晚报和语音里提它也已过期。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import PurePath
from typing import Any, Dict, List, Optional

_SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_DISPATCH = {
    "waiting": ("high", "任务在等你回答"),
    "send_failed": ("high", "任务单下发失败"),
    "submission_unknown": ("high", "任务单提交结果待核对"),
    "proposed": ("medium", "任务单等你确认下发"),
}


def dispatch_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    output = []
    for item in items:
        status = str(item.get("status") or "")
        if status not in _DISPATCH:
            continue
        severity, label = _DISPATCH[status]
        output.append({
            "id": f"dispatch:{item.get('id')}:{status}", "kind": "dispatch", "severity": severity,
            "title": f"{label} · {item.get('title')}", "detail": str(item.get("error") or item.get("goal") or "")[:300],
            "created": str(item.get("updated_at") or item.get("created_at") or ""),
            "target_id": str(item.get("id") or ""), "action": "open_dispatch",
            "tainted": bool(item.get("tainted")), "can_dismiss": False,
        })
    return output

_CODEX = {
    "proposed": ("medium", "等你批准给 Codex 发话"),
    "unknown": ("high", "给 Codex 的话不确定发没发出去"),
}


def codex_items(actions: List[Dict[str, Any]], *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """助理拟给 Codex 的动作里要主人动手的。真机 2026-10-29 晚报：等批的那条不在这里，
    模型只好拿白天对话里「待批准」的旧结局去说，报了 3 条其实早已发出的。"""
    from src.scheduling.codex_watch import action_outcome
    output = []
    for item in actions:
        status = action_outcome(item)
        if status not in _CODEX:
            continue
        updated = str(item.get("updated_at") or item.get("created_at") or "")
        severity, label = _CODEX[status]
        if status == "proposed" and item.get("kind") == "interrupt":
            label = "等你批准叫停 Codex"
        where = PurePath(str(item.get("thread_cwd") or "")).name
        name = f"「{item.get('thread_name') or '未命名'}」"
        output.append({
            "id": f"codex:{item.get('id')}:{status}", "kind": "codex", "severity": severity,
            "title": f"{label} · {where}{name}", "detail": str(item.get("text") or item.get("error") or "")[:300],
            "created": updated, "target_id": str(item.get("id") or ""), "action": "open_codex_action",
            "tainted": bool(item.get("tainted")), "can_dismiss": False,
        })
    return output


def attention_items(root: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    """按紧急程度、再按时间新旧排好的「等你处理」清单。任何一个来源读坏了都不拖垮其它来源。"""
    from src.scheduling.codex_watch import load_actions
    from src.workflow.tasks import assistant_dispatch
    from src.workflow.tasks.decisions import DecisionStore, build_decision_queue
    from src.workflow.tasks.tasks import TaskLedger
    from src.workflow.workbench.goals import GoalLedger
    from src.workflow.workbench.runs import RunLedger

    def read(loader):
        try:
            return loader()
        except Exception:  # noqa: BLE001 —— 一个台账坏了，其余照样报
            return []

    output = build_decision_queue(
        goals=read(lambda: GoalLedger(root).list()), tasks=read(lambda: TaskLedger(root).list(limit=200)),
        runs=read(lambda: RunLedger(root).list()), dismissed=read(lambda: DecisionStore(root).dismissed()) or set(),
        limit=200)
    output += dispatch_items(read(lambda: assistant_dispatch.list_dispatches(root)))
    output += codex_items(read(lambda: load_actions(root)))
    output.sort(key=lambda item: str(item.get("created") or ""), reverse=True)
    output.sort(key=lambda item: _SEVERITY.get(str(item.get("severity")), 9))
    return output[:max(1, limit)]
