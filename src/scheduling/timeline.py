"""动态时间线（ADR-012 阶段 A）：把常驻助理做过、正在做、等你处理的事按时间排成一条线。

Desktop 的「动态」标签与 App 的「动态」页共用这一份（各自经自己的接口读），两边看到的完全一样。
**只读汇总、零新状态**——数据都来自已有的台账：

| 种类 | 来源 |
| --- | --- |
| ``notice`` | 通知台账（推给你的每一条） |
| ``change`` | 撤销日志（我替你做了什么，可撤销的带 ``undo``） |
| ``dispatch`` | 任务单（拟定 / 下发 / 完成 / 失败） |
| ``codex`` | 给 Codex 的动作（拟定 / 批准 / 已交付） |
| ``review`` | 自动审核的结论（放行 / 拒绝 / 交给你） |
| ``paused`` | 被暂停自动推进的目标（带 ``resume``） |

每条：``{id, at, kind, title, detail, status, tainted, actions}``；``actions`` 只是界面提示，真正执行仍走
各自原有的接口与签名 / 确认。坏行、读不到的来源跳过，不让一个来源拖垮整条线。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

_MAX = 200


def _when(value: Any) -> Optional[str]:
    """各台账的时间格式不一：统一成带时区的 ISO（UTC）；读不出就丢掉这条。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / (1000 if value > 1e12 else 1), timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.astimezone(timezone.utc).isoformat()


def _item(item_id: str, at: Any, kind: str, title: str, *, detail: str = "", status: str = "",
          tainted: bool = False, actions: Optional[List[dict]] = None) -> Optional[dict]:
    moment = _when(at)
    if moment is None:
        return None
    return {"id": f"{kind}:{item_id}", "at": moment, "kind": kind, "title": " ".join(str(title).split())[:160],
            "detail": str(detail or "")[:400], "status": status, "tainted": bool(tainted), "actions": actions or []}


def _identity(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def _latest(rows: list, limit: int) -> list:
    return sorted((row for row in rows if isinstance(row, dict)),
                  key=lambda row: _when(row.get("updated_at") or row.get("created_at")) or "", reverse=True)[:limit]


def _notices(root: str, limit: int) -> List[dict]:
    from src.scheduling.notices import load_notices
    out = []
    for row in load_notices(root, limit=_MAX, strict=True):
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "")
        first, _, rest = text.partition("\n")
        out.append(_item(_identity(row), row.get("ts"), "notice", first, detail=rest,
                         status={"assistant": "助理", "scheduler": "定时通知", "goal-advancer": "目标推进"}.get(str(row.get("source")), "通知")))
    return [item for item in out if item][:limit]


def _changes(root: str, limit: int) -> List[dict]:
    from src.agents import undo_log
    out = []
    for op in undo_log.recent(root, limit=limit):
        actions = [{"type": "undo", "id": op.get("id")}] if op.get("status") == "done" else []
        out.append(_item(str(op.get("id")), op.get("ts"), "change", str(op.get("summary") or op.get("action") or ""),
                         detail="；".join(filter(None, [str(op.get("undo_detail") or ""), *map(str, op.get("not_recallable") or [])])),
                         status={"done": "可撤销", "undoing": "正在撤销", "undone": "已撤销", "failed": "撤销没成功，请核对"}.get(str(op.get("status")), "待核对"),
                         tainted=bool(op.get("tainted")), actions=actions))
    return [item for item in out if item]


_DISPATCH_TEXT = {"proposed": "等你下发", "sending": "正在下发", "sent": "已下发", "running": "执行中", "waiting": "等你回答",
                  "done": "已完成", "failed": "失败", "cancelled": "已撤回", "send_failed": "下发失败",
                  "submission_unknown": "下发结果待核对", "stopped": "已停止"}


def _dispatches(root: str, limit: int) -> List[dict]:
    from src.workflow.tasks import assistant_dispatch
    out = []
    for entry in _latest(assistant_dispatch.list_dispatches(root), limit):
        status = str(entry.get("status") or "")
        actions = [{"type": "decide_dispatch", "id": entry.get("id")}] if status == "proposed" else []
        link = entry.get("goal_link")
        goal = link.get("objective") if isinstance(link, dict) else None
        out.append(_item(str(entry.get("id")), entry.get("updated_at") or entry.get("created_at"), "dispatch",
                         f"任务单 · {entry.get('project') or ''}：{entry.get('title') or ''}",
                         detail=(f"为目标「{goal}」" if goal else "") + (f"｜{entry.get('error')}" if entry.get("error") else ""),
                         status=_DISPATCH_TEXT.get(status, status), tainted=bool(entry.get("tainted")), actions=actions))
    return [item for item in out if item]


def _codex(root: str, limit: int) -> List[dict]:
    from src.scheduling import codex_watch
    out = []
    for action in _latest(codex_watch.load_actions(root, strict=True), limit):
        status = str(action.get("status") or "")
        title = ("叫停 Codex" if action.get("kind") == "interrupt" else "给 Codex 的话") + \
            f" · {codex_watch.thread_label({'name': action.get('thread_name'), 'cwd': action.get('thread_cwd'), 'id': action.get('thread_id')})}"
        actions = [{"type": "decide_codex", "id": action.get("id")}] if status == "proposed" else []
        out.append(_item(str(action.get("id")), action.get("updated_at") or action.get("created_at"), "codex", title,
                         detail=str(action.get("text") or ""), status=codex_watch.ACTION_STATUS_TEXT.get(status, status),
                         tainted=bool(action.get("tainted")), actions=actions))
    return [item for item in out if item]


def _codex_progress(root: str, now: datetime) -> List[dict]:
    from src.scheduling import codex_watch
    snapshot = codex_watch.progress_snapshot(root, strict=True)
    received = _when(snapshot.get("received_at"))
    if not received:
        return []
    fresh = timedelta(0) <= now - datetime.fromisoformat(received) <= codex_watch.STALE_AFTER
    out = []
    for thread in snapshot["threads"]:
        turn = thread.get("turn") or {}
        current = fresh and codex_watch.execution_is_current(thread, now)
        status = codex_watch.STATUS_TEXT.get(thread.get("status"), "状态待核对") if current else "状态待核对（上次观察）"
        if current and turn.get("status") == "completed":
            status = "本轮结束，任务待验收"
        elif current and turn.get("status") in ("failed", "interrupted"):
            status = "本轮失败" if turn["status"] == "failed" else "本轮已中断"
        if current and "waitingOnApproval" in (thread.get("flags") or []):
            status = "等你批准"
        elif current and "waitingOnUserInput" in (thread.get("flags") or []):
            status = "等你回答"
        detail = str(turn.get("error") or turn.get("plan") or turn.get("request") or "")
        if turn.get("reply"):
            detail += "\nCodex 回复：" + str(turn["reply"])
        out.append(_item("progress:" + thread["id"], turn.get("completed_at") or thread.get("updated_at") or received,
                         "codex", "Codex 进展 · " + codex_watch.thread_label(thread), detail=detail, status=status))
    return [item for item in out if item]


_VERDICT_TEXT = {"approve": "审核放行", "deny": "审核拒绝", "escalate": "交给你"}


def _reviews(root: str, limit: int) -> List[dict]:
    from src.scheduling.auto_review import recent_reviews
    out = []
    for row in recent_reviews(root, limit):
        kind = "派活" if row.get("kind") == "dispatch" else "改目标的下一步"
        out.append(_item(_identity(row), row.get("at"), "review", f"自动审核 · {kind}：{row.get('summary') or ''}",
                         detail=str(row.get("reason") or ""), status=_VERDICT_TEXT.get(str(row.get("verdict")), "")))
    return [item for item in out if item]


def _paused(root: str, now: datetime) -> List[dict]:
    from src.scheduling.goal_advancer import load_paused
    from src.workflow.workbench.personal_goals import PersonalGoals
    goals = {goal["id"]: goal for goal in PersonalGoals(root).list()}
    out = []
    for goal_id, until in load_paused(root).items():
        if (_when(until) or "") <= now.astimezone(timezone.utc).isoformat():
            continue
        goal = goals.get(goal_id)
        if not goal or goal.get("status") not in ("active", "blocked"):
            continue
        objective = str(goal.get("objective") or goal_id)
        out.append(_item(goal_id, (datetime.fromisoformat(_when(until)) - timedelta(hours=24)).isoformat(), "paused", f"「{objective[:60]}」暂停自动推进",
                         detail=f"自动审核拒绝了为它拟的任务单；{_when(until)} 到期后自动恢复，也可以现在恢复。",
                         status="暂停中", actions=[{"type": "resume_goal", "id": goal_id}]))
    return [item for item in out if item]


def build(root: str, *, limit: int = 60, now: Optional[datetime] = None) -> Dict[str, Any]:
    """汇总成一条线（新的在前）。``limit`` 1–200。读不到的来源记进 ``unavailable``，不报错。"""
    limit = max(1, min(_MAX, int(limit)))
    now = now or datetime.now(timezone.utc)
    items: List[dict] = []
    unavailable: List[str] = []
    for name, reader in (("notices", lambda: _notices(root, limit)), ("changes", lambda: _changes(root, limit)),
                         ("dispatches", lambda: _dispatches(root, limit)), ("codex", lambda: _codex(root, limit)),
                         ("codex_progress", lambda: _codex_progress(root, now)),
                         ("reviews", lambda: _reviews(root, limit)), ("paused", lambda: _paused(root, now))):
        try:
            items.extend(reader())
        except Exception:  # noqa: BLE001 —— 一个来源坏了只标出来，不拖垮整条线
            unavailable.append(name)
    items = list({item["id"]: item for item in items}.values())
    pinned = sorted((item for item in items if item["kind"] == "paused"), key=lambda item: item["at"], reverse=True)
    rest = sorted((item for item in items if item["kind"] != "paused"), key=lambda item: item["at"], reverse=True)
    return {"items": (pinned + rest)[:limit], "unavailable": unavailable, "generated_at": now.isoformat()}
