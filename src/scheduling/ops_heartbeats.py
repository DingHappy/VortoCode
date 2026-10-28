"""运维心跳：家里其它机器上的定时活（备份同步、恢复演练……）向常驻助理报到。

两种情况推到手机：
- **报失败**：脚本自己发现出错，立刻推；之后第一次报成功时推一条「已恢复」。
- **该报没报**：超过约定间隔没有报到——机器挂了、cron 没跑、脚本卡住。这正是「每天照样打印完成、
  其实已经 12 天没有新备份」那类静默故障（2026-10-18）。每次断档只推一次，恢复报到后重新计时。

状态存在 ``.vortocode/ops_heartbeats.json``。报到经 ``/api/ops/heartbeat``，用专用令牌，
只能写这里的状态和推一条通知，碰不到别的接口。
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_SOURCE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_MAX_SOURCES = 20
_MAX_TEXT = 300
_MAX_EXPECT_HOURS = 24 * 40
_LOCK = threading.Lock()


class HeartbeatError(ValueError):
    """报到内容不合法。"""


def _path(root: str) -> Path:
    return Path(root) / ".vortocode" / "ops_heartbeats.json"


def _load(root: str) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(root: str, data: Dict[str, Dict[str, Any]]) -> None:
    from src.utils.state_dir import ensure_state_gitignore
    ensure_state_gitignore(root)
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def record(root: str, source: str, *, ok: bool, text: str = "", expect_hours: float = 26,
           now: Optional[datetime] = None, event: bool = False) -> Optional[str]:
    """记一次报到；返回要推给主人的话（失败、或失败后第一次恢复），平安无事返回 None。

    ``event=True``：事件型报到（如「中转站刚发布」），只记发生时间，不约定间隔、不会因为没报而提醒，也不进早报运维摘要。"""
    source = str(source or "").strip()
    if not _SOURCE.fullmatch(source):
        raise HeartbeatError("source 只能用小写字母、数字和 -，最长 40 个字符")
    try:
        expect = float(expect_hours)
    except (TypeError, ValueError):
        raise HeartbeatError("expect_hours 需要是数字") from None
    if not 1 <= expect <= _MAX_EXPECT_HOURS:
        raise HeartbeatError(f"expect_hours 需要在 1 到 {_MAX_EXPECT_HOURS} 之间")
    from src.memory.write_policy import redact_secret_like
    text = redact_secret_like(" ".join(str(text or "").split()))[0][:_MAX_TEXT]
    now = now or datetime.now(timezone.utc)
    with _LOCK:
        data = _load(root)
        if source not in data and len(data) >= _MAX_SOURCES:
            raise HeartbeatError("报到来源太多了，先清理旧的")
        entry = data.get(source, {})
        was_failing = entry.get("status") == "failed" or bool(entry.get("overdue_alerted_at"))
        entry.update(status="ok" if ok else "failed", text=text, expect_hours=expect, last_report=_iso(now))
        if event:
            entry["event"] = True
        if ok:
            entry["last_ok"] = _iso(now)
            entry.pop("overdue_alerted_at", None)
        data[source] = entry
        _save(root, data)
    if not ok:
        return f"🔴 {source} 失败：{text or '没有附带原因'}"
    if was_failing:
        return f"✅ {source} 已恢复：{text or '报到正常'}"
    return None


def sources(root: str) -> List[str]:
    """报到过的来源名；没有就说明这台机器上没配运维心跳。"""
    return sorted(_load(root))


def overdue(root: str, now: Optional[datetime] = None) -> List[str]:
    """超过约定间隔还没报成功的来源；每次断档只返回一次（记下已提醒）。"""
    now = now or datetime.now(timezone.utc)
    messages: List[str] = []
    with _LOCK:
        data = _load(root)
        changed = False
        for source, entry in data.items():
            if entry.get("overdue_alerted_at") or entry.get("event"):
                continue
            last = _parse(entry.get("last_ok") or entry.get("last_report"))
            expect = float(entry.get("expect_hours") or 26)
            if last is None or now - last <= timedelta(hours=expect):
                continue
            hours = int((now - last).total_seconds() // 3600)
            messages.append(f"🔺 {source} 已经 {hours} 小时没有报到成功（约定每 {expect:g} 小时内一次），"
                            f"可能机器停了或定时任务没跑，需要人工看一下")
            entry["overdue_alerted_at"] = _iso(now)
            changed = True
        if changed:
            _save(root, data)
    return messages


_NOTICE_DAILY_LIMIT = 60
_notice_times: List[datetime] = []


def notice_text(source: str, text: str, now: Optional[datetime] = None) -> str:
    """家里机器转来的一条运维消息（比如「有人付款了」）：校验来源、抹掉疑似凭据、截断，并限每天条数。

    令牌泄露时也只能每天推有限几条、碰不到别的接口。返回要推给主人的话。
    """
    source = str(source or "").strip()
    if not _SOURCE.fullmatch(source):
        raise HeartbeatError("source 只能用小写字母、数字和 -，最长 40 个字符")
    from src.memory.write_policy import redact_secret_like
    body = redact_secret_like(" ".join(str(text or "").split()))[0][:_MAX_TEXT]
    if not body:
        raise HeartbeatError("text 不能为空")
    now = now or datetime.now(timezone.utc)
    with _LOCK:
        _notice_times[:] = [moment for moment in _notice_times if now - moment < timedelta(days=1)]
        if len(_notice_times) >= _NOTICE_DAILY_LIMIT:
            raise HeartbeatError("今天的运维消息已经太多了")
        _notice_times.append(now)
    return f"📣 {source}：{body}"


def brief_line(root: str, now: Optional[datetime] = None) -> str:
    """早报末尾的一行运维摘要。没有任何报到来源（没配运维心跳的机器）就是空串，早报里完全不出现。"""
    now = now or datetime.now(timezone.utc)
    data = {source: entry for source, entry in _load(root).items() if not entry.get("event")}
    if not data:
        return ""
    healthy, problems = [], []
    for source, entry in sorted(data.items()):
        last = _parse(entry.get("last_ok"))
        expect = float(entry.get("expect_hours") or 26)
        if entry.get("status") == "failed":
            problems.append(f"{source} 失败（{entry.get('text') or '没有附带原因'}）"[:120])
        elif last is None or now - last > timedelta(hours=expect):
            problems.append(f"{source} 超时没报到")
        else:
            healthy.append(source)
    if not problems:
        return f"运维：{len(healthy)} 项正常（{'、'.join(healthy)}）"
    head = f"运维：{len(problems)} 项异常" + (f"，{len(healthy)} 项正常" if healthy else "")
    return head + "\n" + "\n".join(f"· {item}" for item in problems)


def status_rows(root: str, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """每个报到来源的当前状态，给 Desktop 今日概览读：ok / failed（最近一次报失败）/ overdue（超过约定间隔
    没报到成功）。和 brief_line 同一套判断，但不写状态、不记「已提醒」——读多少次都不改变任何东西。
    事件型报到不约定间隔，不在其中。"""
    now = now or datetime.now(timezone.utc)
    rows: List[Dict[str, Any]] = []
    for source, entry in sorted(_load(root).items()):
        if entry.get("event"):
            continue
        last_ok = _parse(entry.get("last_ok"))
        expect = float(entry.get("expect_hours") or 26)
        if entry.get("status") == "failed":
            status = "failed"
        elif last_ok is None or now - last_ok > timedelta(hours=expect):
            status = "overdue"
        else:
            status = "ok"
        rows.append({"source": source, "status": status, "text": str(entry.get("text") or "")[:_MAX_TEXT],
                     "last_ok": entry.get("last_ok") or "", "last_report": entry.get("last_report") or "",
                     "expect_hours": expect})
    return rows


def snapshot(root: str) -> Dict[str, Dict[str, Any]]:
    return _load(root)
