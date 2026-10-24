"""会话 Dashboard 快照：磁盘会话 + 进程内实时会话合并成一张按「需要处理优先」排序的状态表。"""
from typing import Any, Callable, Dict, Mapping


_SESSION_STATUS_ORDER = {
    "needs_input": 0,
    "failed": 1,
    "working": 2,
    "queued": 3,
    "idle": 4,
    "inactive": 5,
    "completed": 6,
}


def _session_activity(sess: Dict[str, Any]) -> str:
    """取当前运行活动；没有 running 事件时退回最近一次工具/阶段说明。"""
    activities = sess.get("activities")
    if not isinstance(activities, list):
        return ""
    fallback = ""
    for event in reversed(activities):
        if not isinstance(event, dict):
            continue
        label = str(event.get("summary") or event.get("label") or event.get("name") or "").strip()
        if not label:
            continue
        if not fallback:
            fallback = label
        if event.get("status") == "running":
            return label
    return fallback


def session_dashboard_snapshot(repo_root: str, *, live_sessions: Mapping[str, Any],
                               is_busy: Callable[[str], bool],
                               pending_for: Callable[[str], list],
                               running_for: Callable[[str], Any]) -> list[Dict[str, Any]]:
    """把已落盘会话和当前 runtime 的 live actor 合并成一份可排序状态表。

    实时状态由宿主（Web 的会话表/actor/确认队列）注入，这里不持有任何进程内全局。

    状态优先级参考 grok-build Agent Dashboard：需要用户处理的会话先于工作中、空闲与仅
    存盘会话。VortoCode 额外保留 ``queued``，表示用户停止后仍有持久输入等待恢复。
    """
    from pathlib import Path
    from src.workflow.tasks.dashboard import (
        agent_context_summary,
        background_tasks_by_session,
        project_dashboard_context,
    )
    from src.gateway.session_store import list_sessions, title_from_transcript, session_has_content

    resolved_root = str(Path(repo_root).resolve())
    project = project_dashboard_context(resolved_root)
    task_groups = background_tasks_by_session(resolved_root)
    from src.workflow.workbench.hook_issues import (
        hook_issue_snapshot, load_hook_issue_acknowledgements,
    )
    hook_acknowledgements = load_hook_issue_acknowledgements(resolved_root)
    empty_tasks = {
        "total": 0, "active": 0, "attention": 0, "completed": 0,
        "latest_status": "", "latest_prompt": "", "branch": "",
        "latest_task_id": "", "active_task_id": "", "attention_task_id": "",
        "plan_id": "", "worktree_count": 0,
    }
    empty_hook_issues = {"count": 0, "latest": None, "items": [], "truncated": False}
    rows = {}
    for item in list_sessions(repo_root):
        sid = item["sid"]
        tasks = {**empty_tasks, **task_groups.get(sid, {})}
        if item.get("has_content") is False and not tasks["total"]:
            continue
        hook_issues = item.get("hook_issues") if isinstance(item.get("hook_issues"), dict) \
            else empty_hook_issues
        status = "failed" if tasks["attention"] or hook_issues["count"] \
            else "working" if tasks["active"] else "queued" if item.get("queue_count") else "inactive"
        rows[sid] = {
            **item,
            **project,
            "status": status,
            "queue_count": item.get("queue_count", 0),
            "pending_input": False,
            "pending_input_count": 0,
            "running_prompt": "",
            "activity": "",
            "mode": None,
            "background_tasks": tasks,
            "hook_issues": hook_issues,
            "context": item.get("context") if isinstance(item.get("context"), dict) else {},
        }

    for key, sess in list(live_sessions.items()):
        if not key.startswith("sid-") or not isinstance(sess, dict):
            continue
        session_root = sess.get("repo_root")
        if session_root and str(Path(str(session_root)).resolve()) != resolved_root:
            continue
        sid = key[len("sid-"):]
        # 旧进程内条目没有 repo_root 时，只在它已有同根磁盘记录时合并，避免跨项目串会话。
        if not session_root and sid not in rows:
            continue

        transcript = sess.get("transcript") if isinstance(sess.get("transcript"), list) else []
        queue = sess.get("prompt_queue") if isinstance(sess.get("prompt_queue"), list) else []
        queue_count = sum(1 for item in queue if isinstance(item, dict) and item.get("id"))
        working = is_busy(key)
        pending = pending_for(key)
        running = running_for(key)
        tasks = {**empty_tasks, **task_groups.get(sid, {})}
        hook_issues = hook_issue_snapshot(
            sess.get("activities") if isinstance(sess.get("activities"), list) else [],
            hook_acknowledgements.get(key) or [],
        )
        if pending:
            status = "needs_input"
        elif tasks["attention"] or hook_issues["count"]:
            status = "failed"
        elif working or tasks["active"]:
            status = "working"
        elif queue_count:
            status = "queued"
        else:
            status = "idle"

        disk = rows.get(sid, {})
        agent = sess.get("agent")
        if not (sid in rows or working or pending or queue_count or running or tasks["total"]
                or session_has_content({**sess, "history": getattr(agent, "history", []),
                                        "plan": getattr(agent, "plan", [])})):
            continue
        mode = (running or {}).get("mode") if isinstance(running, dict) else None
        context = agent_context_summary(sess.get("agent"), mode or "plan")
        rows[sid] = {
            **project,
            "sid": sid,
            "title": disk.get("title") or title_from_transcript(transcript),
            "messages": len(transcript),
            "updated": float(sess.get("updated") or disk.get("updated") or 0),
            "status": status,
            "queue_count": queue_count,
            "pending_input": bool(pending),
            "pending_input_count": len(pending),
            "running_prompt": str((running or {}).get("text") or "")[:160],
            "activity": _session_activity(sess)[:160],
            "mode": mode,
            "background_tasks": tasks,
            "hook_issues": hook_issues,
            "context": context or (disk.get("context") if isinstance(disk.get("context"), dict) else {}),
        }

    return sorted(
        rows.values(),
        key=lambda item: (_SESSION_STATUS_ORDER.get(str(item.get("status")), 99),
                          -float(item.get("updated") or 0)),
    )
