"""Deterministic, recoverable project journal assembled from durable ledgers."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_MAX_TIMELINE = 200
_MAX_NOTES = 100
_MAX_WEEK_DAYS = 31
_SUMMARY_FIELDS = (
    "tasks_done", "tasks_active", "tasks_failed", "tasks_paused",
    "goals_achieved", "goals_blocked", "goals_active",
    "evidence_passed", "evidence_failed", "runs_passed", "runs_failed",
    "tools", "decisions_allowed", "decisions_denied", "notes",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().astimezone().date().isoformat()


def normalize_day(value: str = "") -> str:
    raw = str(value or today()).strip()
    if not _DAY.fullmatch(raw):
        raise ValueError("Journal 日期必须是 YYYY-MM-DD")
    try:
        date.fromisoformat(raw)
    except ValueError as error:
        raise ValueError("Journal 日期无效") from error
    return raw


def _parse_time(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed
    except ValueError:
        return None


def _on_day(value: Any, day: str) -> bool:
    parsed = _parse_time(value)
    return parsed is not None and parsed.astimezone().date().isoformat() == day


def _safe_text(value: Any, limit: int = 1000) -> str:
    from src.memory.write_policy import redact_secret_like

    redacted, _reasons = redact_secret_like(str(value or ""))
    redacted = redacted.strip()
    return redacted if len(redacted) <= limit else redacted[:limit] + "…"


def _event(
    event_id: str,
    kind: str,
    timestamp: str,
    title: str,
    detail: str = "",
    *,
    status: str = "",
    target_id: str = "",
) -> dict[str, Any]:
    return {
        "id": _safe_text(event_id, 160),
        "kind": _safe_text(kind, 40),
        "ts": _safe_text(timestamp, 80),
        "title": _safe_text(title, 240),
        "detail": _safe_text(detail, 1200),
        "status": _safe_text(status, 40),
        "target_id": _safe_text(target_id, 160),
    }


class JournalStore:
    def __init__(self, repo_root: str):
        self.repo_root = str(repo_root)
        self.directory = Path(repo_root) / ".vortocode" / "journal"

    def _path(self, day: str) -> Path:
        return self.directory / f"{normalize_day(day)}.json"

    def load(self, day: str) -> Optional[Dict[str, Any]]:
        path = self._path(day)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except (OSError, TypeError, ValueError):
            return None

    def save(self, journal: Dict[str, Any]) -> bool:
        day = normalize_day(str(journal.get("date") or ""))
        payload = dict(journal)
        payload["date"] = day
        payload["stored_at"] = _now()
        try:
            from src.agents.dev_plan import ensure_state_gitignore

            ensure_state_gitignore(self.repo_root)
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self._path(day)
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            temporary.replace(path)
            journal["stored_at"] = payload["stored_at"]
            return True
        except (OSError, TypeError, ValueError):
            return False

    def list_days(self) -> list[Dict[str, Any]]:
        if not self.directory.is_dir():
            return []
        output = []
        for path in sorted(self.directory.glob("*.json"), reverse=True):
            if not _DAY.fullmatch(path.stem):
                continue
            payload = self.load(path.stem)
            if payload is None:
                continue
            output.append({
                "date": path.stem,
                "headline": _safe_text(payload.get("headline") or "", 240),
                "stored_at": str(payload.get("stored_at") or "")[:80],
                "summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else {},
            })
        return output[:90]


def _count(items: Iterable[Any], predicate) -> int:
    return sum(1 for item in items if predicate(item))


def _summary_count(journal: Dict[str, Any], field: str) -> int:
    try:
        return max(0, int((journal.get("summary") or {}).get(field) or 0))
    except (AttributeError, TypeError, ValueError):
        return 0


def build_daily_journal(repo_root: str, day: str = "") -> Dict[str, Any]:
    """Build a live journal view from durable audit/task/run/goal sources."""
    from src.gateway.audit import list_audit
    from src.gateway.goals import GoalLedger
    from src.gateway.runs import RunLedger
    from src.gateway.tasks import TaskLedger
    from src.gateway.worktree_sessions import task_session_view

    selected_day = normalize_day(day)
    store = JournalStore(repo_root)
    stored = store.load(selected_day) or {}
    notes = [
        {
            "id": _safe_text(note.get("id") or "journal-note", 160),
            "text": _safe_text(note.get("text") or "", 2000),
            "created": _safe_text(note.get("created") or "", 80),
        }
        for note in (stored.get("notes") or [])
        if isinstance(note, dict) and str(note.get("text") or "").strip()
    ][-_MAX_NOTES:]
    all_tasks = TaskLedger(repo_root).list()
    all_goals = GoalLedger(repo_root).list()
    all_runs = RunLedger(repo_root).list()
    audit = [entry for entry in list_audit(repo_root, 500) if _on_day(entry.get("ts"), selected_day)]
    tasks = [task for task in all_tasks if _on_day(task.created, selected_day) or _on_day(task.updated, selected_day)]
    goals = [goal for goal in all_goals if _on_day(goal.created, selected_day) or _on_day(goal.updated, selected_day)]
    runs = [run for run in all_runs if _on_day(run.created, selected_day) or _on_day(run.updated, selected_day)]
    evidence = [
        (goal, item)
        for goal in all_goals
        for item in goal.evidence
        if _on_day(item.created, selected_day)
    ]

    from src.gateway.worktree_sessions import list_worktree_sessions
    live_worktrees = list_worktree_sessions(repo_root)
    task_views = [task_session_view(repo_root, task, worktrees=live_worktrees) for task in tasks]
    handoffs = [
        {
            "task_id": view["id"],
            "status": view.get("status") or "",
            "title": _safe_text(view.get("prompt") or "后台开发任务", 240),
            "branch": _safe_text(view.get("branch") or "", 180),
            "next_action": _safe_text((view.get("handoff") or {}).get("next_action") or "", 300),
            "completed": [_safe_text(item, 240) for item in (view.get("handoff") or {}).get("completed", [])[:12]],
            "remaining": [_safe_text(item, 240) for item in (view.get("handoff") or {}).get("remaining", [])[:12]],
            "text": _safe_text((view.get("handoff") or {}).get("text") or "", 2000),
        }
        for view in task_views[:20]
    ]

    evidence_view = []
    for goal, item in evidence:
        criterion = goal.criterion(item.criterion_id)
        evidence_view.append({
            "id": item.id,
            "goal_id": goal.id,
            "objective": _safe_text(goal.objective, 240),
            "criterion_id": item.criterion_id,
            "criterion": _safe_text(criterion.text if criterion else "目标级证据", 300),
            "kind": _safe_text(item.kind, 80),
            "summary": _safe_text(item.summary, 1000),
            "passed": bool(item.passed),
            "source": _safe_text(item.source, 80),
            "created": item.created,
        })

    summary = {
        "tasks_done": _count(tasks, lambda item: item.status == "done"),
        "tasks_active": _count(tasks, lambda item: item.status in {"queued", "running"}),
        "tasks_failed": _count(tasks, lambda item: item.status in {"failed", "interrupted"}),
        "tasks_paused": _count(tasks, lambda item: item.status == "paused"),
        "goals_achieved": _count(goals, lambda item: item.status == "achieved"),
        "goals_blocked": _count(goals, lambda item: item.status == "blocked"),
        "goals_active": _count(goals, lambda item: item.status in {"draft", "active"}),
        "evidence_passed": _count(evidence, lambda pair: pair[1].passed),
        "evidence_failed": _count(evidence, lambda pair: not pair[1].passed),
        "runs_passed": _count(runs, lambda item: item.status == "done" and item.code == 0),
        "runs_failed": _count(runs, lambda item: item.status in {"failed", "interrupted"} or item.code not in {None, 0}),
        "tools": _count(audit, lambda item: item.get("category") == "tool"),
        "decisions_allowed": _count(audit, lambda item: item.get("category") == "decision" and item.get("decision") == "allowed"),
        "decisions_denied": _count(audit, lambda item: item.get("category") == "decision" and item.get("decision") == "denied"),
        "notes": len(notes),
    }
    pending = summary["goals_blocked"] + summary["tasks_failed"] + summary["tasks_paused"] + summary["runs_failed"]
    headline = (
        f"{selected_day}：完成 {summary['tasks_done']} 个任务，"
        f"{summary['goals_achieved']} 个目标通过验收，记录 {summary['evidence_passed']} 条通过证据；"
        f"当前有 {pending} 项需要继续处理。"
    )

    timeline = []
    for entry in audit:
        if entry.get("category") == "tool":
            timeline.append(_event(
                entry["id"], "tool", entry.get("ts") or "",
                f"工具 · {entry.get('tool') or 'unknown'}",
                json.dumps(entry.get("args") or {}, ensure_ascii=False),
                status="done", target_id=entry.get("session") or "",
            ))
        elif entry.get("category") == "decision":
            timeline.append(_event(
                entry["id"], "decision", entry.get("ts") or "",
                "允许操作" if entry.get("decision") == "allowed" else "拒绝操作",
                entry.get("operation") or "",
                status=entry.get("decision") or "", target_id=entry.get("session") or "",
            ))
        else:
            timeline.append(_event(
                entry["id"], "event", entry.get("ts") or "",
                f"事件 · {entry.get('event') or 'runtime'}",
                json.dumps(entry.get("data") or {}, ensure_ascii=False), status="done",
            ))
    for task in tasks:
        timeline.append(_event(
            f"journal-task-{task.id}", "task", task.updated or task.created,
            f"任务 · {task.status}", task.prompt, status=task.status, target_id=task.id,
        ))
    for goal in goals:
        timeline.append(_event(
            f"journal-goal-{goal.id}", "goal", goal.updated or goal.created,
            f"目标 · {goal.status}", goal.objective, status=goal.status, target_id=goal.id,
        ))
    for goal, item in evidence:
        timeline.append(_event(
            f"journal-evidence-{item.id}", "evidence", item.created,
            "验收证据通过" if item.passed else "验收证据失败",
            f"{goal.objective} · {item.summary}", status="passed" if item.passed else "failed",
            target_id=goal.id,
        ))
    for run in runs:
        timeline.append(_event(
            f"journal-run-{run.id}", "run", run.updated or run.created,
            f"{run.kind} · {run.status}", run.error or run.command,
            status=run.status, target_id=run.id,
        ))
    for note in notes:
        timeline.append(_event(
            str(note.get("id") or "journal-note"), "note", str(note.get("created") or ""),
            "手工记录", str(note.get("text") or ""), status="noted",
        ))
    timeline.sort(key=lambda item: item.get("ts") or "", reverse=True)
    timeline = timeline[:_MAX_TIMELINE]

    highlights = [
        item for item in timeline
        if item["kind"] in {"note", "evidence"}
        or item["status"] in {"done", "failed", "interrupted", "blocked", "achieved"}
    ][:16]
    next_actions = []
    if selected_day == today():
        from src.gateway.decisions import DecisionStore, build_decision_queue

        decisions = build_decision_queue(
            goals=all_goals,
            tasks=all_tasks,
            runs=all_runs,
            dismissed=DecisionStore(repo_root).dismissed(),
            limit=30,
        )
        next_actions = [
            {
                "kind": item["kind"],
                "target_id": item["target_id"],
                "action": item["action"],
                "title": _safe_text(item["title"], 240),
                "detail": _safe_text(item["detail"], 600),
            }
            for item in decisions
            if item["kind"] in {"goal", "task", "run"}
        ]

    journal: Dict[str, Any] = {
        "date": selected_day,
        "generated_at": _now(),
        "stored_at": stored.get("stored_at") or "",
        "frozen": selected_day != today() and bool(stored),
        "headline": headline,
        "summary": summary,
        "source_counts": {
            "audit": len(audit), "tasks": len(tasks), "goals": len(goals),
            "runs": len(runs), "evidence": len(evidence),
        },
        "highlights": highlights,
        "timeline": timeline,
        "handoffs": handoffs,
        "evidence": evidence_view,
        "notes": notes,
        "next_actions": next_actions[:30],
    }
    digest_payload = dict(journal)
    digest_payload.pop("generated_at", None)
    digest_payload.pop("stored_at", None)
    journal["digest"] = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return journal


def get_daily_journal(repo_root: str, day: str = "") -> Dict[str, Any]:
    selected_day = normalize_day(day)
    stored = JournalStore(repo_root).load(selected_day)
    if selected_day != today() and stored is not None:
        payload = dict(stored)
        payload["frozen"] = True
        return payload
    return build_daily_journal(repo_root, selected_day)


def snapshot_daily_journal(repo_root: str, day: str = "") -> Dict[str, Any]:
    selected_day = normalize_day(day)
    if selected_day != today():
        raise ValueError("只能刷新今天的 Journal；历史快照保持冻结")
    journal = build_daily_journal(repo_root, selected_day)
    stored = JournalStore(repo_root).load(selected_day)
    if stored is not None and stored.get("digest") == journal.get("digest"):
        journal["stored_at"] = stored.get("stored_at") or ""
        return journal
    if not JournalStore(repo_root).save(journal):
        raise OSError("Journal 快照保存失败")
    journal["frozen"] = journal["date"] != today()
    return journal


def add_journal_note(repo_root: str, text: str, day: str = "") -> Dict[str, Any]:
    selected_day = normalize_day(day)
    if selected_day != today():
        raise ValueError("只能给今天的 Journal 添加记录")
    clean = _safe_text(text, 2000)
    if not clean:
        raise ValueError("Journal 记录不能为空")
    journal = build_daily_journal(repo_root, selected_day)
    notes = [note for note in journal.get("notes", []) if isinstance(note, dict)][-(_MAX_NOTES - 1):]
    notes.append({
        "id": "note-" + uuid.uuid4().hex[:12],
        "text": clean,
        "created": _now(),
    })
    journal["notes"] = notes
    if not JournalStore(repo_root).save(journal):
        raise OSError("Journal 记录保存失败")
    rebuilt = build_daily_journal(repo_root, selected_day)
    if not JournalStore(repo_root).save(rebuilt):
        raise OSError("Journal 记录保存失败")
    return rebuilt


def build_journal_continuation(repo_root: str, from_day: str) -> Dict[str, Any]:
    """Reconcile historical action hints with the current actionable queue.

    Historical snapshots are evidence, not commands.  Only targets that still
    exist in today's deterministic decision queue are returned as resumable.
    """
    selected_day = normalize_day(from_day)
    if selected_day >= today():
        raise ValueError("只能从历史 Journal 恢复下一步")

    from src.gateway.decisions import DecisionStore, build_decision_queue
    from src.gateway.goals import GoalLedger
    from src.gateway.runs import RunLedger
    from src.gateway.tasks import TaskLedger

    source = get_daily_journal(repo_root, selected_day)
    source_keys: list[tuple[str, str]] = []
    for item in source.get("next_actions") or []:
        if not isinstance(item, dict):
            continue
        key = (_safe_text(item.get("kind"), 40), _safe_text(item.get("target_id"), 160))
        if key[0] in {"goal", "task", "run"} and key[1] and key not in source_keys:
            source_keys.append(key)
    for handoff in source.get("handoffs") or []:
        if not isinstance(handoff, dict):
            continue
        key = ("task", _safe_text(handoff.get("task_id"), 160))
        if key[1] and key not in source_keys:
            source_keys.append(key)

    current = build_decision_queue(
        goals=GoalLedger(repo_root).list(),
        tasks=TaskLedger(repo_root).list(),
        runs=RunLedger(repo_root).list(),
        dismissed=DecisionStore(repo_root).dismissed(),
        limit=200,
    )
    current_by_key = {
        (str(item.get("kind") or ""), str(item.get("target_id") or "")): item
        for item in current
        if item.get("kind") in {"goal", "task", "run"} and item.get("target_id")
    }
    actions = []
    for key in source_keys:
        item = current_by_key.get(key)
        if item is None:
            continue
        actions.append({
            "kind": key[0],
            "target_id": key[1],
            "action": _safe_text(item.get("action"), 80),
            "title": _safe_text(item.get("title"), 240),
            "detail": _safe_text(item.get("detail"), 600),
        })

    stale_count = len(source_keys) - len(actions)
    return {
        "from_date": selected_day,
        "generated_at": _now(),
        "source_stored": bool(source.get("stored_at")),
        "headline": (
            f"从 {selected_day} 找到 {len(actions)} 项当前仍可继续的工作；"
            f"{stale_count} 项历史线索已完成、已忽略或不再存在。"
        ),
        "actions": actions,
        "stale_count": stale_count,
    }


def build_weekly_journal(
    repo_root: str, end_day: str = "", days: int = 7,
) -> Dict[str, Any]:
    """Aggregate daily journals without mutating or re-freezing history."""
    selected_end = normalize_day(end_day)
    try:
        selected_days = int(days)
    except (TypeError, ValueError) as error:
        raise ValueError("Journal 周报天数必须是整数") from error
    if not 1 <= selected_days <= _MAX_WEEK_DAYS:
        raise ValueError(f"Journal 周报天数必须在 1 到 {_MAX_WEEK_DAYS} 之间")
    if selected_end > today():
        raise ValueError("Journal 周报结束日期不能晚于今天")

    end_date = date.fromisoformat(selected_end)
    start_date = end_date - timedelta(days=selected_days - 1)
    journals = [
        get_daily_journal(repo_root, (start_date + timedelta(days=offset)).isoformat())
        for offset in range(selected_days)
    ]
    summary = {
        field: sum(_summary_count(journal, field) for journal in journals)
        for field in _SUMMARY_FIELDS
    }
    day_views = [
        {
            "date": journal.get("date") or "",
            "headline": _safe_text(journal.get("headline"), 240),
            "summary": {
                field: _summary_count(journal, field)
                for field in _SUMMARY_FIELDS
            },
            "stored": bool(journal.get("stored_at")),
            "frozen": bool(journal.get("frozen")),
        }
        for journal in journals
    ]
    highlights = []
    for journal in reversed(journals):
        for item in journal.get("highlights") or []:
            if not isinstance(item, dict):
                continue
            highlights.append({**item, "date": journal.get("date") or ""})
            if len(highlights) >= 30:
                break
        if len(highlights) >= 30:
            break

    latest = journals[-1]
    latest_actions = latest.get("next_actions") or []
    if selected_end < today():
        latest_actions = build_journal_continuation(repo_root, selected_end)["actions"]
    carryovers = [
        {
            "kind": _safe_text(item.get("kind"), 40),
            "target_id": _safe_text(item.get("target_id"), 160),
            "action": _safe_text(item.get("action"), 80),
            "title": _safe_text(item.get("title"), 240),
            "detail": _safe_text(item.get("detail"), 600),
        }
        for item in latest_actions
        if isinstance(item, dict) and item.get("target_id")
    ][:30]
    pending = (
        summary["goals_blocked"] + summary["tasks_failed"]
        + summary["tasks_paused"] + summary["runs_failed"]
    )
    return {
        "start_date": start_date.isoformat(),
        "end_date": selected_end,
        "days_count": selected_days,
        "generated_at": _now(),
        "headline": (
            f"{start_date.isoformat()} 至 {selected_end}：完成 {summary['tasks_done']} 个任务，"
            f"{summary['goals_achieved']} 个目标通过验收，积累 {summary['evidence_passed']} 条通过证据；"
            f"期间记录 {pending} 项阻塞或失败信号。"
        ),
        "summary": summary,
        "days": day_views,
        "highlights": highlights,
        "carryovers": carryovers,
    }
