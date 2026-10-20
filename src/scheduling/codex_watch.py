"""常驻助理这边：收 Mac 上 Codex 观察员报来的进度，存下来、判断要不要提醒主人、给助理一个查进度的工具。

马上推手机的只有 Codex 卡在你身上的事，每件只推一次：
- 等批准 / 等回答：持续超过 ``WAIT_GRACE``（人在电脑前多半已经顺手处理了，别刷屏）；
- 服务出错（systemError）。
做完 / 失败 / 被中断的一轮记成事件，由半小时一次的监管（``codex_supervisor``）汇总进进度里，不一轮推一次。
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
_EVENTS_KEEP = 50       # 结束的轮次，等监管汇总


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
                "shell_writes": int(turn.get("shell_writes") or 0),
                "shell_write_paths": [str(path)[:200] for path in (turn.get("shell_write_paths") or [])[:10]],
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
    """存下最新进度、记下在眼前结束的轮次，返回这次要马上推给主人的提醒（每件事只推一次）。"""
    now = now or _now()
    threads = _clean_threads(payload)
    previous = load_state(root)
    before = {thread["id"]: thread for thread in previous.get("threads") or []}
    marks: Dict[str, Dict[str, Any]] = previous.get("marks") or {}
    notices: List[str] = []
    events: List[dict] = [event for event in previous.get("events") or [] if isinstance(event, dict)]
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
                events.append({"thread_id": thread["id"], "name": thread["name"], "turn_id": turn["id"],
                               "status": turn["status"], "file_count": turn.get("file_count") or 0,
                               "reply": (turn.get("reply") or "")[-300:], "error": (turn.get("error") or "")[:200],
                               "at": now.isoformat()})
            mark["reported_turn"] = turn["id"]
        if thread.get("status") == "systemError" and old.get("status") != "systemError":
            notices.append(f"⚠️ {label} 出错了（Codex 服务报 systemError），去 Codex 窗口看一下。")
        marks[thread["id"]] = mark
    live = {thread["id"] for thread in threads}
    state = {"threads": threads, "received_at": now.isoformat(), "host": str((payload or {}).get("host") or "")[:60],
             "marks": {key: value for key, value in marks.items() if key in live}, "events": events[-_EVENTS_KEEP:]}
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


# ── ③ 代你接管：助理拟一条给 Codex 的消息（或叫停），你批准后由 Mac 上的观察员执行 ──────────────
#
# 状态：proposed（等你批准）→ approved（等 Mac 取走）→ sending（Mac 认领，正在交给 Codex）→ sent / failed；
#       你拒绝 → cancelled。批准只能由人做（手机按钮 / Desktop 卡片），模型只能提议。
# 认领（sending）在执行之前：Mac 报结果失败时停在 sending 等人核对，不会因重试把同一句话发两遍。

_ACTION_TEXT_MAX = 2000
_ACTIONS_KEEP = 100


def _actions_path(root: str) -> Path:
    return Path(root) / ".vortocode" / "codex" / "actions.json"


def load_actions(root: str) -> List[dict]:
    try:
        data = json.loads(_actions_path(root).read_text(encoding="utf-8"))
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_actions(root: str, items: List[dict]) -> None:
    path = _actions_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(items[-_ACTIONS_KEEP:], handle, ensure_ascii=False)
        temp = Path(handle.name)
    temp.replace(path)


class CodexActionError(ValueError):
    """动作不合法，或当前状态不允许这个操作。"""


def _find_thread(root: str, wanted: str) -> dict:
    wanted = str(wanted or "").strip()
    threads = load_state(root).get("threads") or []
    for thread in threads:
        if thread.get("id") == wanted or str(thread.get("name") or "") == wanted:
            return thread
    matches = [thread for thread in threads if wanted and wanted in str(thread.get("name") or "")]
    if len(matches) == 1:
        return matches[0]
    names = "、".join(f"「{thread.get('name')}」" for thread in threads) or "（现在没有打开的 Codex 会话）"
    raise CodexActionError(f"找不到这个 Codex 会话：{wanted}。打开着的有：{names}")


def propose_action(root: str, *, thread: str, kind: str, text: str = "", session: str = "",
                   tainted: bool = False) -> dict:
    target = _find_thread(root, thread)
    text = str(text or "").strip()
    if kind not in {"message", "interrupt"}:
        raise CodexActionError("只能拟「发消息」或「叫停」")
    if kind == "message" and not text:
        raise CodexActionError("要发给 Codex 的话不能是空的")
    if kind == "interrupt" and target.get("status") != "active":
        raise CodexActionError(f"「{target.get('name')}」现在没在做事，不需要叫停")
    import uuid
    now = _now().isoformat()
    action = {"id": f"cdx-{uuid.uuid4().hex[:10]}", "thread_id": target["id"], "thread_name": target.get("name") or "",
              "kind": kind, "text": text[:_ACTION_TEXT_MAX], "status": "proposed", "tainted": bool(tainted),
              "session": str(session or "")[:80], "turn_id": (target.get("turn") or {}).get("id") or "",
              "created_at": now, "updated_at": now, "error": ""}
    items = load_actions(root)
    items.append(action)
    _save_actions(root, items)
    return action


def _change(root: str, action_id: str, allowed: set, **updates) -> dict:
    items = load_actions(root)
    for item in items:
        if item.get("id") == action_id:
            if item.get("status") not in allowed:
                raise CodexActionError({"proposed": "这条还在等你批准", "approved": "已经批准，等 Mac 执行",
                                        "sending": "正在交给 Codex", "sent": "已经发给 Codex 了",
                                        "failed": "这条已经失败了", "cancelled": "这条已经撤回了"}
                                       .get(item.get("status"), "状态不允许这个操作"))
            item.update(updates, updated_at=_now().isoformat())
            _save_actions(root, items)
            return dict(item)
    raise CodexActionError(f"没有这条 Codex 动作：{action_id}")


def decide_action(root: str, action_id: str, approved: bool) -> dict:
    return _change(root, action_id, {"proposed"}, status="approved" if approved else "cancelled")


def claim_action(root: str, action_id: str) -> dict:
    return _change(root, action_id, {"approved"}, status="sending")


def finish_action(root: str, action_id: str, *, ok: bool, error: str = "") -> dict:
    return _change(root, action_id, {"sending"}, status="sent" if ok else "failed", error=str(error or "")[:300])


def action_notice(action: dict) -> str:
    label = f"Codex「{action.get('thread_name')}」"
    warning = "\n⚠ 助理是在读过外部内容后拟的，发之前看清楚。" if action.get("tainted") else ""
    if action.get("kind") == "interrupt":
        return f"🤖 助理建议叫停 {label} 正在做的这一轮。{warning}\n点「批准」叫停，「拒绝」不动。"
    return f"🤖 助理想替你给 {label} 发：\n{action.get('text')}{warning}\n点「批准」发送，「拒绝」不发。"


def action_result_notice(action: dict) -> str:
    label = f"Codex「{action.get('thread_name')}」"
    if action.get("status") == "sent":
        return f"✅ 已{'叫停' if action.get('kind') == 'interrupt' else '发给'} {label}。"
    return f"❌ 没能{'叫停' if action.get('kind') == 'interrupt' else '发给'} {label}：{action.get('error') or '原因不明'}"


def build_codex_action_tools(root: str, *, is_tainted=lambda: False, session=None) -> list:
    """助理替你给 Codex 发话 / 叫停：只能提议，批准由人做（手机按钮或 Desktop 卡片）。"""
    from src.agents.tool import Tool

    async def _propose(kind: str, args: dict) -> str:
        try:
            action = propose_action(root, thread=str(args.get("thread") or ""), kind=kind, text=str(args.get("text") or ""),
                                    session=str((session() if callable(session) else session) or ""), tainted=is_tainted())
        except CodexActionError as error:
            return f"没有拟好：{error}"
        try:
            from src.scheduling.notices import make_notifier
            await make_notifier(root, source="codex")(action_notice(action), confirm_id=action["id"])
        except Exception:  # noqa: BLE001 —— 推送失败不影响 Desktop 卡片上批准
            pass
        return (f"已拟好（{action['id']}），等用户在手机通知或 Desktop 卡片上批准；**还没有发给 Codex**，"
                "不要声称已经发了。")

    async def _message(args: dict) -> str:
        return await _propose("message", args)

    async def _interrupt(args: dict) -> str:
        return await _propose("interrupt", args)

    return [
        Tool("propose_codex_message",
             "替用户给 Mac 上某个 Codex 会话拟一句话（下一步指令、补充要求、回答它的问题）。只是提议，"
             "用户批准后才发：Codex 空闲就开始新的一轮，正在做就作为补充插进当前这一轮。先用 codex_status 看清会话。",
             {"thread": "会话名（或 id），只能是 codex_status 里列出的", "text": "要发给 Codex 的话，写清楚、具体"},
             _message, read_only=False, serial=True),
        Tool("propose_codex_interrupt",
             "建议叫停某个 Codex 会话正在做的这一轮（方向错了、在做危险的事、卡住了）。只是提议，用户批准后才叫停。",
             {"thread": "会话名（或 id）"}, _interrupt, read_only=False, serial=True),
    ]
