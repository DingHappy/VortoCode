"""常驻助理这边：收 Mac 上 Codex 观察员报来的进度，存下来、判断要不要提醒主人、给助理一个查进度的工具。

提醒（推手机）只在状态变化时发，而且每件事只发一次：
- 等批准 / 等回答：持续超过 ``WAIT_GRACE``（人在电脑前多半已经顺手处理了，别刷屏）；
- 做完一轮：附 Codex 最后一句说明和改动文件数；失败 / 被中断 / 服务出错：附原因。
观察员只发精简进度（不含完整对话和 diff），这里也只存最新一份。
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

WAIT_GRACE = timedelta(minutes=2)
STALE_AFTER = timedelta(minutes=30)     # 这么久没收到报告：观察员不在了（Mac 合盖、Desktop 关了）
_MAX_THREADS = 12


def _state_path(root: str) -> Path:
    return Path(root) / ".vortocode" / "codex" / "state.json"


def load_state(root: str) -> Dict[str, Any]:
    try:
        data = json.loads(_state_path(root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(root: str, state: Dict[str, Any]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(state, handle, ensure_ascii=False)
        temp = Path(handle.name)
    temp.replace(path)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _clean_threads(payload: Any) -> List[dict]:
    """只收预期的字段和长度：报告来自本人 Mac，但照样按不可信输入处理。"""
    threads = payload.get("threads") if isinstance(payload, dict) else None
    if not isinstance(threads, list):
        raise ValueError("报告缺少 threads")
    out = []
    for raw in threads[:_MAX_THREADS]:
        if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
            continue
        turn = raw.get("turn") if isinstance(raw.get("turn"), dict) else None
        out.append({
            "id": str(raw["id"])[:80], "name": str(raw.get("name") or "")[:80], "cwd": str(raw.get("cwd") or "")[:200],
            "status": str(raw.get("status") or "unknown")[:20],
            "flags": [str(flag)[:40] for flag in (raw.get("flags") or [])[:4]],
            "updated_at": raw.get("updated_at") if isinstance(raw.get("updated_at"), (int, float)) else None,
            "turn": None if turn is None else {
                "id": str(turn.get("id") or "")[:80], "status": str(turn.get("status") or "")[:20],
                "started_at": turn.get("started_at"), "completed_at": turn.get("completed_at"),
                "duration_ms": turn.get("duration_ms"),
                "request": str(turn.get("request") or "")[:500], "reply": str(turn.get("reply") or "")[:1000],
                "plan": str(turn.get("plan") or "")[:800], "commands": int(turn.get("commands") or 0),
                "last_command": str(turn.get("last_command") or "")[:300],
                "running_command": str(turn.get("running_command") or "")[:300],
                "files": [str(path)[:200] for path in (turn.get("files") or [])[:20]],
                "file_count": int(turn.get("file_count") or 0), "error": str(turn.get("error") or "")[:400],
            },
        })
    return out


def _waiting(thread: dict) -> str:
    flags = thread.get("flags") or []
    if "waitingOnApproval" in flags:
        return "approval"
    if "waitingOnUserInput" in flags:
        return "input"
    return ""


def record_report(root: str, payload: Any, *, now: Optional[datetime] = None) -> List[str]:
    """存下最新进度，返回这次要推给主人的提醒（每件事只推一次）。"""
    now = now or _now()
    threads = _clean_threads(payload)
    previous = load_state(root)
    before = {thread["id"]: thread for thread in previous.get("threads") or []}
    marks: Dict[str, Dict[str, Any]] = previous.get("marks") or {}
    notices: List[str] = []
    for thread in threads:
        old = before.get(thread["id"]) or {}
        mark = dict(marks.get(thread["id"]) or {})
        label = f"Codex「{thread['name']}」"
        waiting = _waiting(thread)
        if waiting:
            since = _parse(mark.get("waiting_since")) if mark.get("waiting_kind") == waiting else None
            since = since or now
            mark.update(waiting_kind=waiting, waiting_since=since.isoformat())
            if now - since >= WAIT_GRACE and not mark.get("waiting_notified"):
                turn = thread.get("turn") or {}
                if waiting == "approval":
                    command = turn.get("running_command") or turn.get("last_command") or ""
                    notices.append(f"⏸ {label} 在等你批准，已经 {int((now - since).total_seconds() // 60)} 分钟了"
                                   + (f"：\n$ {command}" if command else "。"))
                else:
                    notices.append(f"❓ {label} 在问你问题：\n{(turn.get('reply') or '（看 Codex 窗口）')[-400:]}")
                mark["waiting_notified"] = True
        else:
            mark.pop("waiting_kind", None)
            mark.pop("waiting_since", None)
            mark.pop("waiting_notified", None)
        turn, old_turn = thread.get("turn") or {}, old.get("turn") or {}
        finished = turn.get("id") and turn.get("status") in {"completed", "failed", "interrupted"}
        if finished and mark.get("reported_turn") != turn["id"]:
            # 第一次看到这个会话时已经结束的那轮不推（只推「在我眼前结束」的）
            seen_running = old_turn.get("id") == turn["id"] and old_turn.get("status") == "inProgress"
            newer_turn = bool(old_turn) and old_turn.get("id") != turn["id"]   # 两次报告之间开始又结束的一轮
            if seen_running or newer_turn:
                files = f"，改了 {turn['file_count']} 个文件" if turn.get("file_count") else ""
                if turn["status"] == "completed":
                    reply = (turn.get("reply") or "").strip()
                    notices.append(f"✅ {label} 做完一轮{files}。" + (f"\n{reply[-300:]}" if reply else ""))
                else:
                    reason = turn.get("error") or ("被中断" if turn["status"] == "interrupted" else "没有错误信息")
                    notices.append(f"❌ {label} 这一轮{'被中断' if turn['status'] == 'interrupted' else '失败'}{files}：{reason[:200]}")
            mark["reported_turn"] = turn["id"]
        if thread.get("status") == "systemError" and old.get("status") != "systemError":
            notices.append(f"⚠️ {label} 出错了（Codex 服务报 systemError），去 Codex 窗口看一下。")
        marks[thread["id"]] = mark
    live = {thread["id"] for thread in threads}
    state = {"threads": threads, "received_at": now.isoformat(), "host": str((payload or {}).get("host") or "")[:60],
             "marks": {key: value for key, value in marks.items() if key in live}}
    _save_state(root, state)
    return notices


def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "刚刚"
    if minutes < 60:
        return f"{minutes} 分钟前"
    return f"{minutes // 60} 小时前"


STATUS_TEXT = {"active": "正在做", "idle": "空闲", "systemError": "出错", "notLoaded": "未打开"}


def overview(root: str, *, now: Optional[datetime] = None) -> str:
    """给助理和早晚报用的一段 Codex 进度。没有报告就说明为什么看不到。"""
    now = now or _now()
    state = load_state(root)
    received = _parse(state.get("received_at"))
    if not received:
        return "还没收到过 Codex 的进度：需要在 Mac 上开着 VortoCode Desktop（设置 → 能力里开启「让助理查看 Codex」），并且 Codex 在运行。"
    lines = []
    stale = now - received > STALE_AFTER
    lines.append(f"Codex 进度（{_ago((now - received).total_seconds())}更新"
                 + ("，Mac 可能合盖或 Desktop 没开，可能不是最新" if stale else "") + "）：")
    threads = state.get("threads") or []
    if not threads:
        lines.append("- 现在没有打开的 Codex 会话。")
    for thread in threads:
        turn = thread.get("turn") or {}
        status = STATUS_TEXT.get(thread.get("status"), thread.get("status"))
        waiting = _waiting(thread)
        if waiting:
            status = "在等你批准" if waiting == "approval" else "在问你问题"
        line = f"- 「{thread.get('name')}」（{thread.get('cwd')}）：{status}"
        if turn.get("request"):
            line += f"；这一轮的要求：{turn['request'][:120]}"
        if turn.get("running_command"):
            line += f"；正在跑：{turn['running_command'][:120]}"
        if turn.get("file_count"):
            line += f"；改了 {turn['file_count']} 个文件（{'、'.join(turn.get('files', [])[:5])}）"
        if turn.get("reply"):
            line += f"；最后说：{turn['reply'][-240:]}"
        if turn.get("error"):
            line += f"；错误：{turn['error'][:160]}"
        lines.append(line)
    return "\n".join(lines)


def brief(root: str, now: Optional[datetime] = None) -> str:
    """早晚报末尾的一段 Codex：在等你的、正在做的、最近做完的。半天没报告就不提。"""
    now = now or _now()
    state = load_state(root)
    received = _parse(state.get("received_at"))
    if not received or now - received > timedelta(hours=12):
        return ""
    lines = []
    for thread in state.get("threads") or []:
        waiting = _waiting(thread)
        if waiting:
            lines.append(f"· 「{thread.get('name')}」{'在等你批准' if waiting == 'approval' else '在问你问题'}")
        elif thread.get("status") == "active":
            lines.append(f"· 「{thread.get('name')}」正在做")
    return ("Codex：\n" + "\n".join(lines[:5])) if lines else ""


def build_codex_tools(root: str) -> list:
    """常驻助理查 Codex 进度的工具。内容来自用户自己的 Mac，但仍是工具输出：按外部内容对待。"""
    from src.agents.tool import Tool

    async def _status(_args: dict) -> str:
        return overview(root)

    return [Tool("codex_status",
                 "查看用户 Mac 上 Codex（OpenAI 的编码助手）在做什么：每个打开的会话的任务、状态"
                 "（正在做 / 空闲 / 在等批准 / 在问问题）、跑的命令、改的文件、最后的说明。"
                 "用户问 Codex 进度、或让你检查 / 提醒 Codex 的活时先调它。",
                 {}, _status, untrusted_source=True)]
