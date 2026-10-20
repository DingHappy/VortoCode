"""派活的真实成绩：派了多少、做成多少、第一次就成的比例、重试几次才成、问了你几次、多久做完。

只读台账（含归档），不调模型、不碰项目运行时。这是以后调派活机制的依据：改了什么，看这些数有没有变好。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, List, Optional

from src.workflow.tasks import assistant_dispatch


def _when(value: Any) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _all_items(root: str) -> List[Dict[str, Any]]:
    items = list(assistant_dispatch.list_dispatches(root))
    seen = {item["id"] for item in items}
    archive = assistant_dispatch._ledger_path(root).parent / "archive"
    for path in sorted(archive.glob("*.json")) if archive.is_dir() else []:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"] not in seen:
            seen.add(item["id"])
            items.append(item)
    return items


def dispatch_stats(root: str, now: datetime, days: int = 30) -> Dict[str, Any]:
    """最近 ``days`` 天下发过的任务单的成绩。按「一件事」（一串重试算一件）和「一张单」两种口径数。"""
    now = now if now.tzinfo else now.astimezone()
    since = now - timedelta(days=days)
    items = _all_items(root)
    by_id = {item["id"]: item for item in items}
    sent = [item for item in items if (_when(item.get("sent_at")) or since - timedelta(seconds=1)) >= since]
    counts: Dict[str, int] = {}
    for item in sent:
        counts[item.get("status", "")] = counts.get(item.get("status", ""), 0) + 1

    def origin(item: Dict[str, Any]) -> str:
        hops = 0
        while item.get("retry_of") and item["retry_of"] in by_id and hops < 10:
            item, hops = by_id[item["retry_of"]], hops + 1
        return item["id"]
    chains: Dict[str, List[Dict[str, Any]]] = {}
    for item in sent:
        chains.setdefault(origin(item), []).append(item)
    finished_chains = {key: rows for key, rows in chains.items()
                       if any(row.get("status") in {"done", "failed", "stopped"} for row in rows)}
    succeeded = {key: rows for key, rows in finished_chains.items() if any(row.get("status") == "done" for row in rows)}
    attempts_to_success = [min(int(row.get("attempt") or 1) for row in rows if row.get("status") == "done")
                           for rows in succeeded.values()]
    durations = []
    for item in sent:
        start, end = _when(item.get("sent_at")), _when(item.get("finished_at") or item.get("updated_at"))
        if item.get("status") == "done" and start and end and end >= start:
            durations.append((end - start).total_seconds() / 60)
    criteria = [row for item in sent if isinstance(item.get("outcome"), dict)
                and item["outcome"].get("kind") == "acceptance" for row in item["outcome"].get("criteria") or []]
    never_sent = [item for item in items if not item.get("sent_at") and item.get("status") == "cancelled"
                  and (_when(item.get("created_at")) or since - timedelta(seconds=1)) >= since]
    return {
        "days": days,
        "sent": len(sent),
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0),
        "stopped": counts.get("stopped", 0),
        "in_flight": sum(counts.get(key, 0) for key in ("running", "waiting", "sending", "submission_unknown")),
        "things": len(chains),
        "things_finished": len(finished_chains),
        "things_succeeded": len(succeeded),
        "first_try": sum(1 for value in attempts_to_success if value == 1),
        "retried_to_success": sum(1 for value in attempts_to_success if value > 1),
        "asked": sum(1 for item in sent if item.get("answers")),
        "answers": sum(len(item.get("answers") or []) for item in sent),
        "median_minutes": round(median(durations)) if durations else None,
        "criteria_met": sum(row.get("verdict") == "met" for row in criteria),
        "criteria_checked": len(criteria),
        "prs": sum(1 for item in sent if item.get("pr_url")),
        "goal_confirmed": sum(1 for item in sent if item.get("goal_confirmed_at")),
        "withdrawn": len(never_sent),
    }


def _percent(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "—"


def stats_text(stats: Dict[str, Any], *, title: str = "") -> str:
    """给人看的几行。没下发过就一句话。"""
    head = title or f"最近 {stats['days']} 天派活"
    if not stats["sent"]:
        return f"{head}：没有下发过任务单。" + (f"（{stats['withdrawn']} 张写好后撤回）" if stats["withdrawn"] else "")
    lines = [f"{head}：下发 {stats['sent']} 张，{stats['things']} 件事。",
             f"· 做成 {stats['things_succeeded']}/{stats['things_finished']} 件（{_percent(stats['things_succeeded'], stats['things_finished'])}），"
             f"其中第一次就成 {stats['first_try']} 件、重试后成 {stats['retried_to_success']} 件"
             + (f"；还在做 {stats['in_flight']} 张" if stats["in_flight"] else "")]
    if stats["median_minutes"] is not None:
        lines.append(f"· 做完一张通常要 {stats['median_minutes']} 分钟（中位数）")
    if stats["asked"]:
        lines.append(f"· 有 {stats['asked']} 张中途问了你，共 {stats['answers']} 个问题")
    if stats["criteria_checked"]:
        lines.append(f"· 验收标准对照：{stats['criteria_met']}/{stats['criteria_checked']} 条对上（助理判断）")
    extras = [f"开了 {stats['prs']} 个 draft PR" if stats["prs"] else "",
              f"确认达成 {stats['goal_confirmed']} 条目标标准" if stats["goal_confirmed"] else "",
              f"{stats['withdrawn']} 张写好后撤回" if stats["withdrawn"] else ""]
    if any(extras):
        lines.append("· " + "，".join(part for part in extras if part))
    return "\n".join(lines)
