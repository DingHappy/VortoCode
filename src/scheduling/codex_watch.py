"""常驻助理这边：收 Mac 上 Codex 观察员报来的进度，存下来、判断要不要提醒主人、给助理一个查进度的工具。

马上推手机的只有 Codex 卡在你身上的事，每件只推一次：
- 等批准 / 等回答：持续超过 ``WAIT_GRACE``（人在电脑前多半已经顺手处理了，别刷屏）；
- 服务出错（systemError）。
做完 / 失败 / 被中断的一轮记成事件，由半小时一次的监管（``codex_supervisor``）汇总进进度里，不一轮推一次。
观察员只发精简进度（不含完整对话和 diff），这里也只存最新一份。
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents.backends.codex import codex_execution_state
from src.agents.backends.execution import ExecutionProgress, ExecutionSnapshot
from src.agents.backends.observation_order import ObservationConflict, ObservationCursor, ObservationOrder
from src.scheduling import codex_goal
from src.workflow.pipeline.pipeline_storage import PipelineBusy, pipeline_lock, write_json

WAIT_GRACE = timedelta(minutes=2)
STALE_AFTER = timedelta(minutes=30)     # 这么久没收到报告：观察员不在了（Mac 合盖、Desktop 关了）
_MAX_THREADS = 12
_EVENTS_KEEP = 50       # 结束的轮次，等监管汇总
_DAILY_KEEP_DAYS = 3    # 每天各会话做完几轮的小账：晚报用。事件只留 50 条，忙的一天半天就挤掉了（真机 2026-10-21）


class ObservationBusy(RuntimeError):
    """The existing report is being committed; a byte-identical retry is safe."""


@contextmanager
def _report_lock(root: str):
    directory = _state_path(root).parent
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".report.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "r+b") as handle:
        if sys.platform == "win32":
            import msvcrt
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise ObservationBusy("进度报告正在更新，请重传原报告") from error
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ObservationBusy("进度报告正在更新，请重传原报告") from error
        # Closing releases the lock; keep the stable inode across commits.
        yield


@dataclass(frozen=True)
class ReportAcceptance:
    notices: list[str]
    cursor: dict | None
    duplicate: bool = False


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
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo is not None else None
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
        if "execution" in raw:
            out[-1]["execution"] = _clean_execution(raw["execution"], out[-1])
    return out


def _clean_execution(raw: Any, thread: dict) -> dict | None:
    """A report is an observation, never a source of permissions or acceptance.

    An explicit null marks unavailable/unsupported observations. Absence alone
    identifies a legacy producer; malformed new reports never use legacy fallback.
    """
    if not isinstance(raw, dict):
        return None
    try:
        # Do not decode or retain sender-provided evidence, results or features.
        report = ExecutionSnapshot.from_dict({key: raw[key] for key in
            ("schema_version", "ref", "state", "observed_at") if key in raw})
        ref, turn = report.ref, thread.get("turn") or {}
        if (ref.backend_id != "codex" or ref.session_id != thread["id"]
                or not turn.get("id") or ref.turn_id != turn["id"] or ref.run_id != turn["id"]
                or any(len(getattr(ref, name)) > 200 for name in
                       ("instance_id", "session_id", "turn_id", "run_id", "task_id"))
                or ref.task_id
                or report.state != codex_execution_state(thread["status"], turn["status"], thread["flags"])):
            return None
        return ExecutionSnapshot(ref, report.state, report.observed_at,
            native_state=f"{thread['status']}:{turn['status']}",
            progress=ExecutionProgress(turn.get("plan") or turn.get("request") or ""),
            features=frozenset({"execution.observe"})).to_dict()
    except (ValueError, TypeError):
        return None


def progress_snapshot(root: str) -> dict:
    """Existing API projection; recheck stored observations without changing state."""
    state = load_state(root)
    threads = _clean_threads({"threads": state.get("threads") or []})
    records = load_handovers(root)
    handed = set(records)
    return {"overview": _overview({**state, "threads": threads}, _now(), handed), "threads": threads,
            "handed_over": sorted(handed & {thread["id"] for thread in threads}),
            "received_at": state.get("received_at"), "report_protocol": 1,
            "report_cursor": state.get("report_cursor"),
            "goal_supervision": _goal_supervision(root, {**state, "threads": threads}, records, _now())}


def execution_is_current(thread: dict, now: datetime) -> bool:
    """Legacy reports keep their contract; a new observation must be usable.

    The report's receive time is checked separately by consumers. A recent
    heartbeat cannot turn an old, unknown or invalid source observation current.
    """
    if "execution" not in thread:
        return True
    report = _clean_execution(thread["execution"], thread)
    if report is None or report["state"] == "unknown":
        return False
    age = now.timestamp() - report["observed_at"]
    return 0 <= age <= STALE_AFTER.total_seconds()


# Codex 自己的登录 / 用量 / 模型满载：拟句话发给 Codex 没用，得你在 Mac 上处理
_ACCOUNT_TROUBLE = (
    (re.compile(r"access token|sign in again|log ?in again|logged out|unauthori[sz]ed|\b401\b|重新登录", re.I),
     "Codex 的登录失效了，要你在 Mac 上重新登录 Codex，再让它接着做"),
    (re.compile(r"usage limit|rate limit|quota|insufficient_quota|额度|用量上限", re.I),
     "Codex 的用量到上限了，等额度恢复（或换账号）后再让它接着做"),
    (re.compile(r"at capacity|overloaded|server is busy|try a different model|满载", re.I),
     "Codex 用的模型这会儿满载，过几分钟让它重试，或在 Codex 里换个模型"),
)
ERROR_PUSH_WINDOW = timedelta(minutes=15)    # 出错提醒和失败轮次在这段时间内算同一件事


def account_trouble(error: str) -> str:
    return next((hint for pattern, hint in _ACCOUNT_TROUBLE if pattern.search(error or "")), "")


def _waiting(thread: dict) -> str:
    if "execution" in thread:
        report = thread["execution"]
        if not isinstance(report, dict) or report.get("state") != "waiting_user":
            return ""
    flags = thread.get("flags") or []
    if "waitingOnApproval" in flags:
        return "approval"
    if "waitingOnUserInput" in flags:
        return "input"
    return ""


def _waiting_scope(thread: dict, kind: str, host: str) -> list[str]:
    turn = thread.get("turn") or {}
    report = thread.get("execution") or {}
    ref = report.get("ref") or {}
    return [host, str(ref.get("instance_id") or ""), thread["id"], str(turn.get("id") or ""), kind]


def record_report(root: str, payload: Any, *, now: Optional[datetime] = None) -> List[str]:
    """存下最新进度、记下在眼前结束的轮次，返回这次要马上推给主人的提醒（每件事只推一次）。"""
    return receive_report(root, payload, now=now).notices


def receive_report(root: str, payload: Any, *, now: Optional[datetime] = None) -> ReportAcceptance:
    """Compare-and-replace the existing report under one cross-process lock."""
    threads = _clean_threads(payload)
    with _report_lock(root):
        previous = load_state(root)
        raw_cursor = previous.get("report_cursor")
        current = ObservationCursor.from_dict(raw_cursor) if raw_cursor is not None else None
        order = ObservationOrder.from_dict(payload["report_order"]) if "report_order" in payload else None
        fingerprint = None
        if order is not None:
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            if current is not None and order.cursor == current:
                if fingerprint != previous.get("report_fingerprint"):
                    raise ObservationConflict("相同序号的报告内容发生变化，未覆盖当前状态")
                return ReportAcceptance([], current.to_dict(), True)
            order.require_successor(current)
        elif current is not None:
            raise ObservationConflict("当前进度已启用顺序校验，旧格式报告未覆盖当前状态")
        notices = _record_report(root, payload, threads, previous, now=now,
                                 cursor=order.cursor if order else None, fingerprint=fingerprint)
        return ReportAcceptance(notices, order.cursor.to_dict() if order else None)


def _record_report(root: str, payload: Any, threads: List[dict], previous: dict, *,
                   now: Optional[datetime], cursor: ObservationCursor | None, fingerprint: str | None) -> List[str]:
    now = now or _now()
    host = str((payload or {}).get("host") or "")[:60]
    received = _parse(previous.get("received_at"))
    continuous = (received is not None and received.tzinfo is not None
                  and timedelta(0) <= now - received <= STALE_AFTER)
    before = {thread["id"]: thread for thread in previous.get("threads") or []}
    marks: Dict[str, Dict[str, Any]] = previous.get("marks") or {}
    notices: List[str] = []
    events: List[dict] = [event for event in previous.get("events") or [] if isinstance(event, dict)]
    daily: Dict[str, Dict[str, dict]] = previous.get("daily") if isinstance(previous.get("daily"), dict) else {}
    for thread in threads:
        old = before.get(thread["id"]) or {}
        mark = dict(marks.get(thread["id"]) or {})
        label = f"Codex「{thread['name']}」"
        waiting = _waiting(thread) if execution_is_current(thread, now) else ""
        if waiting:
            scope = _waiting_scope(thread, waiting, host)
            since = _parse(mark.get("waiting_since")) if continuous and mark.get("waiting_scope") == scope else None
            if since is None or since.tzinfo is None or since > now:
                since = now
                mark.pop("waiting_notified", None)
            mark.update(waiting_kind=waiting, waiting_scope=scope, waiting_since=since.isoformat())
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
            mark.pop("waiting_scope", None)
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
                _tally(daily, thread, turn, now)
                pushed_at = _parse(mark.get("error_pushed_at"))
                if turn["status"] == "failed" and pushed_at is not None and now - pushed_at < ERROR_PUSH_WINDOW:
                    events[-1]["pushed"] = True       # 出错提醒刚推过：这轮失败就是那件事
            mark["reported_turn"] = turn["id"]
        if thread.get("status") == "systemError" and old.get("status") != "systemError":
            # 同一次失败只推这一条，带上原因；巡视里就不再列进「要你处理」（真机 2026-10-21 推了两遍）
            recent = [event for event in events if event.get("thread_id") == thread["id"]
                      and event.get("status") == "failed" and not event.get("pushed")
                      and (_parse(event.get("at")) or now) >= now - ERROR_PUSH_WINDOW]
            error = str(turn.get("error") or (recent[-1].get("error") if recent else "") or "")
            for event in recent:
                event["pushed"] = True
            mark["error_pushed_at"] = now.isoformat()
            hint = account_trouble(error)
            if hint:
                notices.append(f"⚠️ {label} 出错了：{hint}。")
            elif error:
                notices.append(f"⚠️ {label} 出错了：{error[:160]}\n去 Codex 窗口看一下。")
            else:
                notices.append(f"⚠️ {label} 出错了（Codex 服务报 systemError），去 Codex 窗口看一下。")
        marks[thread["id"]] = mark
    live = {thread["id"] for thread in threads}
    state = {"threads": threads, "received_at": now.isoformat(), "host": host,
             "marks": {key: value for key, value in marks.items() if key in live}, "events": events[-_EVENTS_KEEP:],
             "daily": _recent_days(daily, now)}
    if cursor is not None:
        state.update(report_cursor=cursor.to_dict(), report_fingerprint=fingerprint)
    idle = (payload or {}).get("user_idle_seconds")
    if isinstance(idle, (int, float)) and not isinstance(idle, bool) and 0 <= idle < 10 ** 7:
        state["user_idle_seconds"] = int(idle)      # Mac 上键盘鼠标多久没动（旧观察员不报：当不知道）
        if (payload or {}).get("presence_reports") is True:
            state["presence_reports"] = True        # 观察员会在主人到/离 Mac 时补报（v0.2.13 起）
    _save_state(root, state)
    expire_stale_actions(root, threads)
    notices += [action_result_notice(action) for action in settle_unconfirmed_actions(root, now)]
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
    state, records, stamp = load_state(root), load_handovers(root), now or _now()
    text = _overview(state, stamp, set(records))
    for item in _goal_supervision(root, state, records, stamp):
        text += (f"\n关联目标 {item['goal_id']} → {item['thread_id']}：{item.get('objective', '')}；"
                 f"推进判断 {item.get('used', '?')}/{item.get('limit', '?')}；"
                 f"未验收项 {', '.join(item.get('pending', [])) or '无'}；{item['reason']}")
    return text


def goal_supervision(root: str, *, now: Optional[datetime] = None) -> list[dict]:
    """Project original goal/handover/supervisor records without repairing or running them."""
    return _goal_supervision(root, load_state(root), load_handovers(root), now or _now())


def _goal_supervision(root: str, state: dict, records: dict, stamp: datetime) -> list[dict]:
    from src.scheduling.codex_supervisor import goal_review_state
    handed = {tid: record for tid, record in records.items() if "goal" in record}
    if not handed:
        return []
    out = []
    try:
        rows = goal_review_state(root)
    except OSError:
        return [{"thread_id": tid, "goal_id": record["goal"]["id"], "state": "unknown",
                 "reason": "推进判断记录不可读，未重置额度，请人工核对"} for tid, record in handed.items()]
    received = _parse(state.get("received_at"))
    for tid, record in handed.items():
        link = record["goal"]
        item = {"thread_id": tid, "goal_id": link["id"], "limit": link["review_limit"],
                "used": rows.get(link["id"], {}).get("used", 0), "criterion_ids": link["criterion_ids"]}
        try:
            thread = next(t for t in state.get("threads", []) if t["id"] == tid)
            goal = codex_goal.current(root, record, thread)
            item.update(objective=goal["objective"], revision=goal["revision"], pending=goal["pending"])
            if (received is None or not timedelta(0) <= stamp - received <= STALE_AFTER
                    or not execution_is_current(thread, stamp)):
                raise ValueError("执行观测已过期，请刷新后核对")
            reason = codex_goal.stopped(rows, goal, link)
            item.update(state="confirmed" if goal["confirmed"] else "paused" if reason else "active",
                        reason=reason or "按已授权验收项跟进，下一步仍需批准")
        except (KeyError, TypeError, ValueError, OSError, StopIteration) as error:
            item.update(state="unknown", reason=f"目标状态待核对：{error}")
        out.append(item)
    return out


def _overview(state: dict, now: datetime, handed: Optional[set] = None) -> str:
    """Render one observation, so overview and details cannot mix report versions."""
    received = _parse(state.get("received_at"))
    if not received:
        return "还没收到过 Codex 的进度：需要在 Mac 上开着 VortoCode Desktop（设置 → 能力里开启「让助理查看 Codex」），并且 Codex 在运行。"
    lines = []
    stale = not timedelta(0) <= now - received <= STALE_AFTER
    age_text = _ago((now - received).total_seconds()) if received <= now else "时间待核对"
    lines.append(f"Codex 进度（{age_text}更新"
                 + ("，Mac 可能合盖或 Desktop 没开，可能不是最新" if stale else "") + "）：")
    threads = state.get("threads") or []
    if not threads:
        lines.append("- 现在没有打开的 Codex 会话。")
    for thread in threads:
        turn = thread.get("turn") or {}
        current = not stale and execution_is_current(thread, now)
        status = STATUS_TEXT.get(thread.get("status"), thread.get("status")) if current else "状态待核对"
        waiting = _waiting(thread) if current else ""
        if waiting:
            status = "在等你批准" if waiting == "approval" else "在问你问题"
        line = f"- {thread_label(thread)}{'【已交给助理】' if thread.get('id') in (handed or set()) else ''}：{status}"
        if turn.get("request"):
            line += f"；这一轮的要求：{turn['request'][:120]}"
        if turn.get("running_command"):
            line += f"；{'正在跑' if current and not stale else '上次命令'}：{turn['running_command'][:120]}"
        if turn.get("file_count"):
            line += f"；改了 {turn['file_count']} 个文件（{'、'.join(turn.get('files', [])[:5])}）"
        if turn.get("reply"):
            line += f"；最后说：{turn['reply'][-240:]}"
        if turn.get("error"):
            line += f"；错误：{turn['error'][:160]}"
        lines.append(line)
    return "\n".join(lines)


def _day(now: datetime) -> str:
    return now.astimezone().date().isoformat()


def _tally(daily: Dict[str, Dict[str, dict]], thread: dict, turn: dict, now: datetime) -> None:
    """记一轮：哪天、哪个会话、做完 / 停了 / 出错。会话名和最后的回复当场记下（会话列表只留最近几个）。"""
    sessions = daily.setdefault(_day(now), {})
    bucket = sessions.setdefault(thread["id"], {"completed": 0, "interrupted": 0, "failed": 0})
    bucket["name"] = thread["name"]
    status = turn.get("status")
    if status in ("completed", "interrupted", "failed"):
        bucket[status] = int(bucket.get(status) or 0) + 1
    if status == "completed" and turn.get("reply"):
        bucket["last_reply"] = str(turn["reply"])[:2000]
    bucket["last_at"] = now.isoformat()


def _recent_days(daily: Dict[str, Dict[str, dict]], now: datetime) -> Dict[str, Dict[str, dict]]:
    keep = {(now.astimezone().date() - timedelta(days=offset)).isoformat() for offset in range(_DAILY_KEEP_DAYS)}
    return {day: sessions for day, sessions in daily.items() if day in keep and isinstance(sessions, dict)}


def day_summary(root: str, now: Optional[datetime] = None) -> str:
    """晚报附的一段：今天 Codex 各会话做完几轮、最后说了什么。今天没做过就不写。确定性渲染，不进模型上下文。"""
    now = now or _now()
    sessions = ((load_state(root).get("daily") or {}).get(_day(now)) or {}).values()
    rows = [bucket for bucket in sessions if isinstance(bucket, dict)
            and any(int(bucket.get(key) or 0) for key in ("completed", "interrupted", "failed"))]
    if not rows:
        return ""
    from src.scheduling.codex_supervisor import _reply_head
    rows.sort(key=lambda bucket: (int(bucket.get("completed") or 0), str(bucket.get("last_at") or "")), reverse=True)
    total = sum(int(bucket.get("completed") or 0) for bucket in rows)
    lines = [f"🧑‍💻 Codex 今天：{len(rows)} 个会话，做完 {total} 轮"]
    for bucket in rows[:6]:
        extra = [f"停了 {bucket['interrupted']} 轮"] if int(bucket.get("interrupted") or 0) else []
        extra += [f"出错 {bucket['failed']} 轮"] if int(bucket.get("failed") or 0) else []
        line = f"· 「{bucket.get('name') or '未命名会话'}」做完 {int(bucket.get('completed') or 0)} 轮"
        line += f"（{'，'.join(extra)}）" if extra else ""
        head = _reply_head(str(bucket.get("last_reply") or ""), 100)
        lines.append(line + (f"\n  最后：{head}" if head else ""))
    if len(rows) > 6:
        lines.append(f"…还有 {len(rows) - 6} 个会话")
    return "\n".join(lines)


def brief(root: str, now: Optional[datetime] = None) -> str:
    """早晚报末尾的一段 Codex：在等你的、正在做的、最近做完的。半天没报告就不提。"""
    now = now or _now()
    state = load_state(root)
    received = _parse(state.get("received_at"))
    if not received or not timedelta(0) <= now - received <= timedelta(hours=12):
        return ""
    lines = []
    for thread in state.get("threads") or []:
        if not execution_is_current(thread, now):
            lines.append(f"· 「{thread.get('name')}」状态待核对（上次观察）")
            continue
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
        actions = action_overview(root)
        return overview(root) + (f"\n\n{actions}" if actions else "")

    return [Tool("codex_status",
                 "查看用户 Mac 上 Codex（OpenAI 的编码助手）在做什么：每个打开的会话的任务、状态"
                 "（正在做 / 空闲 / 在等批准 / 在问问题）、跑的命令、改的文件、最后的说明，以及哪些会话已交给助理。"
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


def thread_label(thread: dict) -> str:
    """会话的完整称呼：名字 + 目录 + id 尾号。名字常常相同（同一句任务起的名），只靠名字分不清。"""
    return f"「{thread.get('name') or '未命名'}」（{thread.get('cwd') or '目录未知'}，#{str(thread.get('id') or '')[-6:]}）"


def _find_thread(root: str, wanted: str) -> dict:
    """按 id 找；按名字只在唯一时才认。同名的不挑第一个——真机 2026-10-28：两个会话都叫
    「编写 add 函数」，一个在测试目录、一个在主人的工作仓库，挑错了就把话发进别人的活里。"""
    wanted = str(wanted or "").strip()
    threads = load_state(root).get("threads") or []
    for thread in threads:
        if wanted and thread.get("id") == wanted:
            return thread
    tail = wanted.lstrip("#")
    by_tail = [thread for thread in threads if len(tail) >= 6 and str(thread.get("id") or "").endswith(tail)]
    if len(by_tail) == 1:                 # 进度里写的是 id 尾号（#xxxxxx）
        return by_tail[0]
    matches = [thread for thread in threads if wanted and str(thread.get("name") or "") == wanted] \
        or [thread for thread in threads if wanted and wanted in str(thread.get("name") or "")]
    if len(matches) == 1:
        return matches[0]
    if matches:
        listed = "、".join(f"{thread_label(thread)} id={thread.get('id')}" for thread in matches)
        raise CodexActionError(f"有 {len(matches)} 个 Codex 会话都对得上「{wanted}」：{listed}。请用会话 id 指定，"
                               "拿不准就问主人是哪一个。")
    names = "、".join(thread_label(thread) for thread in threads) or "（现在没有打开的 Codex 会话）"
    raise CodexActionError(f"找不到这个 Codex 会话：{wanted}。打开着的有：{names}")


# ── ② 交接：默认只看，主人逐个会话交给助理后才插手（2026-10-28 定，见 DECISIONS.md ADR-010） ──────
#
# Codex 是主人自己在驾驶的工具，Mac 上打开的会话大多是主人正在亲手干的活。助理对所有会话都可以看
# （进度、失败、在等批准），但拟话 / 叫停只对交接过的会话：交接过确认门，收回不问；会话关了、
# 监管判断已经做完，就自动收回。

def _handover_path(root: str) -> Path:
    return Path(root) / ".vortocode" / "codex" / "handover.json"


def load_handovers(root: str) -> Dict[str, dict]:
    try:
        data = json.loads(_handover_path(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise CodexActionError("交接记录不可读，已停止插手，请核对原文件") from None
    if not isinstance(data, dict) or any(not isinstance(v, dict) for v in data.values()):
        raise CodexActionError("交接记录格式无效，请核对原文件")
    for record in data.values():
        if "goal" in record:
            link = record["goal"]
            if (not isinstance(link, dict) or set(link) != {"id", "contract", "revision", "review_limit", "criterion_ids"}
                    or not isinstance(link["id"], str) or not re.fullmatch(r"personal-[A-Za-z0-9_-]{1,64}", link["id"])
                    or not isinstance(link["contract"], str) or not re.fullmatch(r"[a-f0-9]{64}", link["contract"])
                    or type(link["revision"]) is not int or link["revision"] < 1
                    or type(link["review_limit"]) is not int or not 1 <= link["review_limit"] <= codex_goal.MAX_REVIEWS
                    or not isinstance(link["criterion_ids"], list) or not link["criterion_ids"]
                    or not all(isinstance(i, str) and i for i in link["criterion_ids"])
                    or len(set(link["criterion_ids"])) != len(link["criterion_ids"])
                    or not all(isinstance(record.get(k), str) and record[k] for k in ("token", "instance_id", "cwd"))):
                raise CodexActionError("目标交接记录格式无效，请核对原文件")
    return data


def _save_handovers(root: str, items: Dict[str, dict]) -> None:
    write_json(_handover_path(root), items)


@contextmanager
def _handover_lock(root: str):
    try:
        with pipeline_lock(root, "codex-handover", kind="state"):
            yield
    except PipelineBusy:
        raise CodexActionError("交接正在更新，请稍后重试") from None


def is_handed_over(root: str, thread_id: str) -> bool:
    return bool(thread_id) and thread_id in load_handovers(root)


def hand_over(root: str, thread: dict, *, now: Optional[datetime] = None, goal: Optional[dict] = None) -> dict:
    """把一个会话交给助理。调用方负责先问过主人（确认门）。"""
    with _handover_lock(root):
        return _hand_over(root, thread, now=now, goal=goal)


def _hand_over(root: str, thread: dict, *, now: Optional[datetime], goal: Optional[dict]) -> dict:
    items = load_handovers(root)
    record = {"name": str(thread.get("name") or "")[:80], "cwd": str(thread.get("cwd") or "")[:200],
              "since": (now or _now()).timestamp()}
    if goal is not None:
        latest = _find_thread(root, str(thread["id"]))
        if latest != thread or not execution_is_current(thread, now or _now()) or not thread.get("execution"):
            raise CodexActionError("执行观测已变化或不支持宿主身份，请刷新后再交接目标")
        try:
            link = codex_goal.binding(root, goal["id"], goal["revision"], goal["review_limit"], goal["criterion_ids"])
        except (KeyError, TypeError, ValueError, OSError) as error:
            raise CodexActionError(f"目标不能交接：{error}") from error
        import uuid
        record.update(goal=link, instance_id=thread["execution"]["ref"]["instance_id"], token=uuid.uuid4().hex)
        if any(key != thread["id"] and value.get("goal", {}).get("id") == link["id"] for key, value in items.items()):
            raise CodexActionError("这个目标已交给另一个会话，请先收回原交接")
        from src.scheduling.codex_supervisor import ensure_goal_allowance
        ensure_goal_allowance(root, link["id"], link["review_limit"],
                              already_bound=any(v.get("goal", {}).get("id") == link["id"] for v in items.values()))
    elif items.get(str(thread["id"]), {}).get("goal"):
        raise CodexActionError("该会话已绑定目标；请先收回，不能静默降级为无目标交接")
    items[str(thread["id"])] = record
    _save_handovers(root, items)
    return record


def take_back(root: str, thread_id: str, *, expected_token: Optional[str] = None) -> bool:
    with _handover_lock(root):
        return _take_back(root, thread_id, expected_token=expected_token)


def _take_back(root: str, thread_id: str, *, expected_token: Optional[str]) -> bool:
    items = load_handovers(root)
    if expected_token is not None and items.get(thread_id, {}).get("token") != expected_token:
        return False
    if items.pop(thread_id, None) is None:
        return False
    _save_handovers(root, items)
    return True


def prune_handovers(root: str, threads: List[dict]) -> List[dict]:
    """会话关了就收回交接（下次同名的新会话不会继承）；返回这次收回的。"""
    with _handover_lock(root):
        return _prune_handovers(root, threads)


def _prune_handovers(root: str, threads: List[dict]) -> List[dict]:
    items = load_handovers(root)
    live = {thread.get("id") for thread in threads}
    gone = [dict(value, id=key) for key, value in items.items() if key not in live]
    if gone:
        _save_handovers(root, {key: value for key, value in items.items() if key in live})
    return gone


def _epoch(value: Any) -> Optional[float]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return value / 1000 if value > 1e12 else float(value)


def owner_driving(root: str, thread: dict) -> bool:
    """主人在亲手推进这个会话：交接之后的最新一轮是主人自己在 Codex 里发起的（不是助理发的话）。
    这时助理不替它拟下一步——主人就在方向盘前。"""
    record = load_handovers(root).get(str(thread.get("id") or ""))
    turn = thread.get("turn") or {}
    started = _epoch(turn.get("started_at"))
    if record is None or started is None or started <= float(record.get("since") or 0):
        return False
    request = " ".join(str(turn.get("request") or "").split())[:200]
    ours = [" ".join(str(item.get("text") or "").split())[:200] for item in load_actions(root)
            if item.get("thread_id") == thread.get("id") and item.get("kind") == "message"
            and item.get("status") in {"sending", "sent", "unknown"}]
    return not any(text and request and (request.startswith(text[:120]) or text.startswith(request[:120]))
                   for text in ours)


def propose_action(root: str, *, thread: str, kind: str, text: str = "", session: str = "",
                   tainted: bool = False, expected_turn_id: Optional[str] = None,
                   expected_handover_token: Optional[str] = None, expected_goal_revision: Optional[int] = None) -> dict:
    target = _find_thread(root, thread)
    if not is_handed_over(root, target["id"]):
        raise CodexActionError(f"{thread_label(target)}没交给助理：默认只看不插手。要替它拟话或叫停，"
                               "先请主人把这个会话交接给你（codex_handover）。")
    if expected_turn_id is not None and (target.get("turn") or {}).get("id", "") != expected_turn_id:
        raise CodexActionError("Codex 轮次已经变化，旧建议不再适用")
    text = str(text or "").strip()
    if kind not in {"message", "interrupt"}:
        raise CodexActionError("只能拟「发消息」或「叫停」")
    if kind == "message" and not text:
        raise CodexActionError("要发给 Codex 的话不能是空的")
    if kind == "message" and not (target.get("turn") or {}).get("id"):
        # Codex 不接受外部给还没收到过第一句话的会话发消息（thread not materialized），批了也只会失败
        raise CodexActionError(f"{thread_label(target)}还没开始过（或读不到它的轮次）：Codex 要求第一句由人在它的窗口里发。"
                               "请主人先在 Codex 里说第一句，之后再拟。")
    if kind == "interrupt" and target.get("status") != "active":
        raise CodexActionError(f"「{target.get('name')}」现在没在做事，不需要叫停")
    import uuid
    now = _now().isoformat()
    action = {"id": f"cdx-{uuid.uuid4().hex[:10]}", "thread_id": target["id"], "thread_name": target.get("name") or "",
              "thread_cwd": target.get("cwd") or "",
              "kind": kind, "text": text[:_ACTION_TEXT_MAX], "status": "proposed", "tainted": bool(tainted),
              "session": str(session or "")[:80], "turn_id": (target.get("turn") or {}).get("id") or "",
              "created_at": now, "updated_at": now, "error": ""}
    record = load_handovers(root).get(target["id"], {})
    if expected_handover_token is not None and record.get("token") != expected_handover_token:
        raise CodexActionError("目标交接已更新，旧建议不再适用")
    if "goal" in record:
        try:
            goal = codex_goal.current(root, record, target)
        except (KeyError, TypeError, ValueError, OSError) as error:
            raise CodexActionError(f"目标状态待核对：{error}") from error
        if expected_goal_revision is not None and goal["revision"] != expected_goal_revision:
            raise CodexActionError("目标版本已变化，旧建议不再适用")
        action.update(handover_token=record["token"], goal_id=goal["id"], goal_revision=goal["revision"],
                      goal_criterion_ids=record["goal"]["criterion_ids"])
    items = load_actions(root)
    items.append(action)
    _save_actions(root, items)
    return action


def _change(root: str, action_id: str, allowed: set, **updates) -> dict:
    items = load_actions(root)
    for item in items:
        if item.get("id") == action_id:
            if item.get("status") not in allowed:
                if item.get("status") == "expired":
                    raise CodexActionError(f"这条已经过时了：{item.get('error') or '情况变了'}")
                raise CodexActionError({"proposed": "这条还在等你批准", "approved": "已经批准，等 Mac 执行",
                                        "sending": "正在交给 Codex", "sent": "已经发给 Codex 了",
                                        "unknown": "这条的结果无法确认，以 Codex 会话的最新状态为准",
                                        "failed": "这条已经失败了", "cancelled": "这条已经撤回了"}
                                       .get(item.get("status"), "状态不允许这个操作"))
            item.update(updates, updated_at=_now().isoformat())
            _save_actions(root, items)
            return dict(item)
    raise CodexActionError(f"没有这条 Codex 动作：{action_id}")


def stale_reason(action: dict, threads: List[dict]) -> str:
    """等你批的动作是不是已经过时：会话关了、要叫停的那一轮已经换了，或助理按上一轮拟的话 Codex 已经往下做了。

    你在对话里让助理拟的话（带会话）不因为换了一轮就作废——那是你自己要说的；叫停只认拟的那一轮，
    否则晚点一下「批准」会叫停你刚开的新一轮。"""
    thread = next((item for item in threads if item.get("id") == action.get("thread_id")), None)
    if thread is None:
        return "这个 Codex 会话已经关了"
    turn = thread.get("turn") or {}
    moved = bool(action.get("turn_id")) and bool(turn.get("id")) and turn.get("id") != action.get("turn_id")
    if action.get("kind") == "interrupt":
        if moved or not (thread.get("status") == "active" and turn.get("status") == "inProgress"):
            return "要叫停的那一轮已经结束了"
    elif moved and not action.get("session"):
        return "Codex 已经开始了新的一轮，这句是按上一轮拟的"
    return ""


def expire_stale_actions(root: str, threads: Optional[List[dict]] = None) -> List[dict]:
    """把过时的待批动作标成 expired（原因写进 error）；返回这次作废的。监管据此不再被旧草稿挡住。"""
    threads = (load_state(root).get("threads") or []) if threads is None else threads
    items = load_actions(root)
    expired = []
    for item in items:
        if item.get("status") != "proposed" and not (item.get("handover_token") and item.get("status") == "approved"):
            continue
        reason = stale_reason(item, threads)
        if not reason and item.get("handover_token"):
            try:
                _goal_action_current(root, item["id"])
            except CodexActionError as error:
                reason = str(error)
        if reason:
            item.update(status="expired", error=reason, updated_at=_now().isoformat())
            expired.append(dict(item))
    if expired:
        _save_actions(root, items)
    return expired


def _goal_action_current(root: str, action_id: str) -> None:
    action = next((a for a in load_actions(root) if a.get("id") == action_id), {})
    record = load_handovers(root).get(action.get("thread_id"), {})
    if not action.get("handover_token"):
        if record.get("goal"):
            raise CodexActionError("旧动作未绑定当前目标，请重新拟话")
        return
    try:
        if record.get("token") != action["handover_token"]:
            raise ValueError("目标交接已收回或更新")
        target = _find_thread(root, action["thread_id"])
        goal = codex_goal.current(root, record, target)
        if action.get("kind") == "message":
            from src.scheduling.codex_supervisor import goal_review_state
            if goal["id"] not in goal_review_state(root):
                raise ValueError("目标推进额度记录缺失，请人工核对")
        if (goal["id"] != action["goal_id"] or goal["revision"] != action["goal_revision"]
                or not execution_is_current(target, _now())):
            raise ValueError("目标或执行观测已变化，请重新拟话")
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise CodexActionError(str(error)) from error


def decide_action(root: str, action_id: str, approved: bool) -> dict:
    if approved:
        expire_stale_actions(root)       # 批准前按最新进度再核一次：过时的不放行
        _goal_action_current(root, action_id)
    return _change(root, action_id, {"proposed"}, status="approved" if approved else "cancelled")


def claim_action(root: str, action_id: str) -> dict:
    with _handover_lock(root):
        try:
            _goal_action_current(root, action_id)
        except CodexActionError as error:
            _change(root, action_id, {"approved"}, status="expired", error=str(error))
            raise
        return _change(root, action_id, {"approved"}, status="sending")


def finish_action(root: str, action_id: str, *, ok: bool, error: str = "", uncertain: bool = False) -> dict:
    """failed 只表示确定没发出去（被拒绝、会话不对、没开始过）。可能已送达却核对不了回执的记成 unknown：
    真机 2026-10-28，被 Codex 明确拒绝的一条也显示成「未能确认送达」，读起来像发没发都不一定。
    旧版观察员不带 uncertain，按它报的原因里「无法确认」认出来。"""
    # unknown 也收：Mac 恢复后补报的迟到回执比「无法确认」更准
    error = str(error or "")[:300]
    if not ok and (uncertain or "无法确认" in error):
        return _change(root, action_id, {"sending", "unknown"}, status="unknown", error=error or UNCONFIRMED_ERROR)
    return _change(root, action_id, {"sending", "unknown"}, status="sent" if ok else "failed", error=error)


SENDING_TIMEOUT = timedelta(minutes=10)
UNCONFIRMED_ERROR = ("交给 Codex 后一直没收到回执（Mac 断线或观察员重启）：可能已经发出，也可能没有。"
                     "不会自动重发；以 Codex 会话的最新状态为准。")


def settle_unconfirmed_actions(root: str, now: Optional[datetime] = None) -> List[dict]:
    """认领后超过 SENDING_TIMEOUT 还没回执的动作标成 unknown（结果无法确认），返回这次标的。

    不当成功也不当失败，更不重发：认领后 Mac 可能已经交给 Codex 才断的线。以前它们会永远停在
    sending，监管把这个会话当成「还有动作没办完」，再也不给它拟下一步。"""
    now = now or _now()
    items = load_actions(root)
    settled = []
    for item in items:
        if item.get("status") != "sending":
            continue
        claimed = _parse(item.get("updated_at"))
        if claimed is None or now - claimed >= SENDING_TIMEOUT:
            item.update(status="unknown", error=UNCONFIRMED_ERROR, updated_at=now.isoformat())
            settled.append(dict(item))
    if settled:
        _save_actions(root, items)
    return settled


def _action_label(action: dict) -> str:
    where = f"（{action['thread_cwd']}）" if action.get("thread_cwd") else ""
    goal = (f"〔目标 {action['goal_id']}，版本 {action.get('goal_revision')}；"
            f"本次 {', '.join(action.get('goal_criterion_ids', []))}〕" if action.get("goal_id") else "")
    return f"Codex「{action.get('thread_name')}」{where}{goal}"


ACTION_STATUS_TEXT = {"proposed": "等主人批准", "approved": "已批准，等 Mac 交给 Codex", "sending": "正在交给 Codex，结果待核对",
                       "sent": "Codex 已受理（不代表已做完）", "failed": "没发出去", "unknown": "结果无法确认",
                       "cancelled": "主人拒绝了", "expired": "已过时，没发"}


def action_overview(root: str, *, now: Optional[datetime] = None, limit: int = 4, within: timedelta = timedelta(hours=24)) -> str:
    """助理自己拟过的动作现在怎样了。没有它，助理只能猜「上一条可能还在等批」——
    真机 2026-10-29：上一条早已受理、正在跑，它两次提醒主人「按顺序批准」。只给助理工具看，不进早晚报。"""
    now = now or _now()
    recent = [item for item in load_actions(root)
              if (_parse(item.get("updated_at")) or now) >= now - within]
    if not recent:
        return ""
    threads = {thread.get("id"): thread for thread in load_state(root).get("threads") or []}
    lines = ["助理拟过的 Codex 动作（最近 24 小时，以这里的状态为准，不要猜）："]
    by_thread: Dict[str, List[dict]] = {}
    for item in recent:
        by_thread.setdefault(str(item.get("thread_id") or ""), []).append(item)
    for thread_id, items in by_thread.items():
        thread = threads.get(thread_id) or {"id": thread_id, "name": items[-1].get("thread_name"), "cwd": items[-1].get("thread_cwd")}
        waiting = sum(1 for item in items if item.get("status") == "proposed")
        lines.append(f"- {thread_label(thread)}：{'有 ' + str(waiting) + ' 条等主人批准' if waiting else '没有等批的'}")
        for item in items[-limit:]:
            verb = "叫停" if item.get("kind") == "interrupt" else f"发话「{str(item.get('text') or '')[:40]}」"
            detail = f"：{str(item.get('error') or '')[:80]}" if item.get("status") in {"failed", "expired"} and item.get("error") else ""
            lines.append(f"  · {item.get('id')} {verb} → {ACTION_STATUS_TEXT.get(item.get('status'), item.get('status'))}{detail}")
    return "\n".join(lines)


def _delivery_hint(target: dict, kind: str) -> str:
    """批准后会发生什么，按此刻观察到的状态说；批准时还会重新核对。"""
    if kind == "interrupt":
        return "批准后叫停它正在做的这一轮。"
    turn = target.get("turn") or {}
    if target.get("status") == "active" and turn.get("status") == "inProgress":
        return "它此刻正在做一轮：批准时如果还在做，这句会作为补充插进当前这一轮；做完了就开新的一轮。"
    return "它此刻空闲：批准后开始新的一轮。"


def action_notice(action: dict) -> str:
    label = _action_label(action)
    warning = "\n⚠ 助理是在读过外部内容后拟的，发之前看清楚。" if action.get("tainted") else ""
    if action.get("kind") == "interrupt":
        return f"🤖 助理建议叫停 {label} 正在做的这一轮。{warning}\n点「批准」叫停，「拒绝」不动。"
    return f"🤖 助理想替你给 {label} 发：\n{action.get('text')}{warning}\n点「批准」发送，「拒绝」不发。"


def action_result_notice(action: dict) -> str:
    label = _action_label(action)
    if action.get("status") == "unknown":
        verb = "叫停" if action.get("kind") == "interrupt" else "发话"
        return (f"⚠️ 给 {label} 的{verb}没收到回执：可能已经发出，也可能没有。"
                "去 Codex 窗口看一眼，不会自动重发。")
    if action.get("status") == "sent":
        return (f"✅ 已向 {label} 提交叫停请求；是否已停止以最新状态为准。" if action.get("kind") == "interrupt"
                else f"✅ 已发给 {label}。")
    return f"❌ 没能{'叫停' if action.get('kind') == 'interrupt' else '发给'} {label}：{action.get('error') or '原因不明'}"


def build_codex_action_tools(root: str, *, is_tainted=lambda: False, session=None, confirm=None) -> list:
    """助理替你给 Codex 发话 / 叫停：只能提议，批准由人做（手机按钮或 Desktop 卡片）。
    只对交接过的会话；交接过确认门（扩大助理能插手的范围），收回不问。"""
    from src.agents.tool import Tool

    async def _handover(args: dict) -> str:
        try:
            target = _find_thread(root, str(args.get("thread") or ""))
        except CodexActionError as error:
            return f"没有交接：{error}"
        label = thread_label(target)
        if str(args.get("take_back") or "").strip().lower() in {"1", "true", "yes", "on", "是", "收回"}:
            return f"已收回 {label}：之后只看不插手。" if take_back(root, target["id"]) else f"{label}本来就没交接。"
        if not args.get("goal_id") and any(key in args for key in ("goal_revision", "review_limit", "criterion_ids")):
            return "没有交接：范围和额度必须指定 goal_id，不能降级为无目标交接。"
        if is_handed_over(root, target["id"]) and not args.get("goal_id"):
            return f"{label}已经交给助理了。"
        goal = None
        if args.get("goal_id"):
            try:
                goal = codex_goal.binding(root, args["goal_id"], args.get("goal_revision"),
                                         args.get("review_limit", codex_goal.DEFAULT_REVIEWS),
                                         args.get("criterion_ids"))
                objective = codex_goal.snapshot(root, goal["id"])["objective"]
            except (KeyError, TypeError, ValueError, OSError) as error:
                return f"没有交接目标：{error}"
        question = (f"把 Codex 会话{label}交给助理盯？\n交接后助理会替它拟下一步、补充要求或叫停，"
                    "每一条仍要你批准；你在 Codex 里亲手发话时它不插话。会话关了或做完就自动收回。")
        if goal:
            question = question.replace("会话关了或做完就自动收回。", "会话关闭或目标逐项验收通过后结束交接，单轮结束不算目标完成。")
            question += (f"\n关联目标：{objective}（{goal['id']}，版本 {goal['revision']}）。"
                         f"本次仅推进验收项：{', '.join(goal['criterion_ids'])}；其余留待下一次交接。"
                         f"该目标累计最多 {goal['review_limit']} 次推进判断，包含失败；重交接不清零。"
                         "相同缺口连续两轮无新验收证据会暂停；目标完成仍需逐项用户验收。")
        if confirm is None or not await confirm(question):
            return f"主人没同意交接 {label}，仍然只看不插手。"
        try:
            hand_over(root, target, goal=goal)
        except (CodexActionError, OSError) as error:
            return f"没有交接：{error}"
        return f"已交接 {label}。"

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
        hint = ""
        try:
            hint = _delivery_hint(_find_thread(root, action["thread_id"]), kind)
        except CodexActionError:
            pass
        return (f"已拟好（{action['id']}），等用户在手机通知或 Desktop 卡片上批准；**还没有发给 Codex**，"
                f"不要声称已经发了。{hint}这个会话其它动作的状态用 codex_status 查，不要猜。")

    async def _message(args: dict) -> str:
        return await _propose("message", args)

    async def _interrupt(args: dict) -> str:
        return await _propose("interrupt", args)

    return [
        Tool("codex_handover",
             "把 Mac 上某个 Codex 会话交给助理盯，或收回。默认助理对 Codex 只看不插手；只有交接过的会话才能拟话或叫停。"
             "只在主人明确要求时调用；用会话 id 指定（codex_status 里有），同名时不要猜。交接要主人确认，收回直接生效。",
             {"thread": "会话 id 或进度里的尾号 #xxxxxx（会话名只在唯一时可用）", "take_back": "true = 收回交接；不填 = 交接",
              "goal_id": "可选，关联当前助理已有的 personal- 目标；先用 personal_goals 查询，不按名称猜",
              "goal_revision": "关联目标时必填，查询取得的当前 revision",
              "criterion_ids": "可选，英文逗号分隔的本次允许推进的未通过验收项ID；不填为全部未通过项。留到以后的项不要包含",
              "review_limit": "目标累计推进判断上限，默认4，最高12；含失败，收回/重交接不清零，增加需重新确认"},
             _handover, read_only=False, serial=True),
        Tool("propose_codex_message",
             "替用户给 Mac 上某个已交接的 Codex 会话拟一句话（下一步指令、补充要求、回答它的问题）。只是提议，"
             "用户批准后才发：Codex 空闲就开始新的一轮，正在做就作为补充插进当前这一轮。先用 codex_status 看清会话。",
             {"thread": "会话 id 或进度里的尾号 #xxxxxx（会话名只在唯一时可用），只能是 codex_status 里列出的", "text": "要发给 Codex 的话，写清楚、具体"},
             _message, read_only=False, serial=True),
        Tool("propose_codex_interrupt",
             "建议叫停某个 Codex 会话正在做的这一轮（方向错了、在做危险的事、卡住了）。只是提议，用户批准后才叫停。",
             {"thread": "会话 id 或进度里的尾号 #xxxxxx（会话名只在唯一时可用）"}, _interrupt, read_only=False, serial=True),
    ]
