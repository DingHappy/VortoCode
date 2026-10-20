"""助理派活台账：常驻助理把要办的事写成任务单，**你点了「下发」才**交给常驻电脑上的项目运行时执行。

分工（参考 Muse）：助理负责收、派、跟、报，不自己改代码；活由工作台（项目运行时的后台开发任务，
隔离 worktree、对外操作一律拒绝、交付一个分支）去做。

安全边界：
- 助理的工具只能**提议**（status=proposed）。下发走 Desktop 调的 HTTP 接口，由人点；助理自己的
  联网工具只能 GET 公网地址，够不到这里。污点回合提出的任务单带 ``tainted`` 标记，界面上提示。
- 目标只能是常驻电脑登记表（resident_projects）里的项目，地址固定 127.0.0.1:<登记端口>，
  令牌用本进程的 VORTOCODE_API_TOKEN（同机各服务共用）。任务单文本只作为开发任务的 prompt 交出去。
"""
from __future__ import annotations

import json
import asyncio
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.workflow.workbench.resident_projects import load_resident_projects
from src.workflow.pipeline.pipeline_storage import PipelineBusy, pipeline_lock, write_json
from src.workflow.tasks.submissions import submission_task_id
from src.utils.ids import safe_id

MAX_TITLE = 60
MAX_GOAL = 4000
MAX_CRITERIA = 10
_MAX_ENTRIES = 200
_LOCK = threading.RLock()
_TERMINAL = {"done", "failed", "cancelled"}


@contextmanager
def _transaction(root: str):
    with _LOCK:
        try:
            with pipeline_lock(root, "assistant-ledger", kind="dispatch"):
                yield
        except PipelineBusy as error:
            raise DispatchError("任务单正在更新，请稍后核对状态") from error

# 远端任务状态 → 台账状态（台账自己的：proposed / cancelled / send_failed）
_REMOTE_STATUS = {
    "queued": "running", "running": "running", "blocked": "waiting",
    "done": "done", "failed": "failed", "cancelled": "cancelled",
    "paused": "stopped", "interrupted": "stopped",
}


class DispatchError(ValueError):
    """任务单不合法或状态不允许当前操作。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ledger_path(root: str) -> Path:
    return Path(root) / ".vortocode" / "dispatches" / "ledger.json"


def _load(root: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(_ledger_path(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        raise DispatchError("任务单台账无法读取，已保留原文件；未创建或下发任务") from error
    items = data.get("dispatches") if isinstance(data, dict) else None
    if (not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get("id"), str)
                                         or safe_id(item["id"]) != item["id"] or not isinstance(item.get("status"), str)
                                         for item in items)
            or len({item["id"] for item in items}) != len(items)):
        raise DispatchError("任务单台账格式损坏，已保留原文件")
    return items


def _save(root: str, items: List[Dict[str, Any]]) -> None:
    path = _ledger_path(root)
    finished = [item for item in items if item.get("status") in _TERMINAL]
    archived = finished[:-_MAX_ENTRIES] if len(finished) > _MAX_ENTRIES else []
    for item in archived:
        # Hash the identity rather than trusting legacy filenames. Archive first:
        # a failed write never removes an entry from the live ledger.
        import hashlib
        name = hashlib.sha256(str(item["id"]).encode()).hexdigest()
        write_json(path.parent / "archive" / f"{name}.json", item)
    ids = {item["id"] for item in archived}
    write_json(path, {"dispatches": [item for item in items if item["id"] not in ids]})


def _find_project(name: str) -> Dict[str, Any]:
    wanted = str(name or "").strip().lower()
    for project in load_resident_projects():
        if project["name"].lower() == wanted:
            return project
    names = "、".join(project["name"] for project in load_resident_projects()) or "（还没有登记任何项目）"
    raise DispatchError(f"常驻电脑上没有项目「{name}」。可派活的项目：{names}")


def task_prompt(entry: Dict[str, Any]) -> str:
    """任务单 → 交给项目运行时的开发任务 prompt。"""
    lines = [entry["title"], "", "目标：", entry["goal"]]
    if entry.get("criteria"):
        lines += ["", "验收标准："] + [f"- {item}" for item in entry["criteria"]]
    known = [row for row in entry.get("known_answers") or [] if isinstance(row, dict)]
    if known:
        lines += ["", "这个项目以前的任务里问过、用户答过的（只供参考；和本任务目标冲突时以目标为准，不要因此扩大范围）："]
        lines += [f"- 问：{row.get('question')}\n  答：{row.get('answer')}" for row in known]
    return "\n".join(lines)


# ---- 答过的问题记成项目经验：以后派同一个项目的活时带上，免得同样的问题再问一次 ----
MAX_KNOWN_ANSWERS = 8
_ANSWERS_LOCK = threading.Lock()


def _answers_path(root: str) -> Path:
    return _ledger_path(root).parent / "answers.json"


def _load_answers(root: str) -> Dict[str, List[Dict[str, str]]]:
    try:
        data = json.loads(_answers_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(key): [row for row in rows if isinstance(row, dict)]
            for key, rows in (data.get("projects") or {}).items() if isinstance(rows, list)} if isinstance(data, dict) else {}


def project_answers(root: str, project: str) -> List[Dict[str, str]]:
    """这个项目记下的问答（新的在后）。"""
    return _load_answers(root).get(str(project).lower(), [])[-MAX_KNOWN_ANSWERS:]


def _remember_answer(root: str, project: str, question: str, answer: str) -> None:
    question, answer = " ".join(question.split())[:300], " ".join(answer.split())[:300]
    if not question or not answer:
        return
    with _ANSWERS_LOCK:
        data = _load_answers(root)
        key = str(project).lower()
        rows = [row for row in data.get(key, []) if row.get("question") != question]     # 同一个问题以最新回答为准
        data[key] = [*rows, {"question": question, "answer": answer, "at": _now()}][-MAX_KNOWN_ANSWERS:]
        write_json(_answers_path(root), {"projects": data})


def forget_answers(root: str, project: str, index: int = 0) -> int:
    """忘掉这个项目记下的问答：index 从 1 起数只删那一条，0 全删。返回删掉几条。"""
    with _ANSWERS_LOCK:
        data = _load_answers(root)
        key = str(project).lower()
        rows = data.get(key, [])
        if index:
            if not 1 <= index <= len(rows):
                raise DispatchError(f"没有第 {index} 条")
            removed, data[key] = 1, rows[:index - 1] + rows[index:]
        else:
            removed, data[key] = len(rows), []
        write_json(_answers_path(root), {"projects": data})
    return removed


MAX_ATTEMPTS = 3


def _retry_context(previous: Dict[str, Any]) -> str:
    """上一次没做成的经过，接在新任务单的目标后面，免得你手抄错误。只取结尾，它是任务输出。"""
    parts = [f"（这是第 {int(previous.get('attempt') or 1) + 1} 次尝试，上一次 {previous['id']} "
             f"{'失败' if previous.get('status') == 'failed' else '没做完'}。）"]
    if previous.get("error"):
        parts.append("上次的错误：\n" + _tail_lines(previous["error"], 600))
    if previous.get("result"):
        parts.append("上次的输出结尾：\n" + _tail_lines(previous["result"], 800))
    return "\n\n".join(parts)


def propose(root: str, *, project: str, title: str, goal: str, criteria: Optional[List[str]] = None,
            session: str = "", tainted: bool = False, retry_of: str = "", retry_note: str = "",
            goal_id: str = "", criterion_id: str = "") -> Dict[str, Any]:
    """助理提出一张任务单（只记账，不执行）。``retry_of``：接着一张没做成的单子再来一次，自动带上它的错误。

    ``retry_note``：助理复盘上次失败后拟的「这次换什么做法」，会写进目标，也单独显示在确认通知里。
    ``goal_id`` / ``criterion_id``：这件活是为哪个个人目标（的哪条验收标准）做的；做完后问你算不算达成。"""
    target = _find_project(project)
    title = " ".join(str(title or "").split())[:MAX_TITLE]
    goal = str(goal or "").strip()
    if not title or not goal:
        raise DispatchError("任务单需要标题和目标")
    link = _goal_link(root, goal_id, criterion_id) if goal_id else None
    brief = goal[:MAX_GOAL]           # 不含重试上下文的目标本身：再重试时从它起头，别一层套一层
    retry_note = " ".join(str(retry_note or "").split())[:600]
    if retry_note:
        goal = goal + "\n\n这次换个做法：" + retry_note
    attempt = 1
    if retry_of:
        previous = next((item for item in _load(root) if item.get("id") == retry_of), None)
        if previous is None:
            raise DispatchError(f"没有任务单 {retry_of}")
        if previous.get("status") not in {"failed", "stopped", "send_failed"}:
            raise DispatchError("只能接着失败或没做完的任务单重派")
        attempt = int(previous.get("attempt") or 1) + 1
        if attempt > MAX_ATTEMPTS:
            raise DispatchError(f"这件事已经试了 {MAX_ATTEMPTS} 次，再派多半还是失败：先跟用户商量换个做法或拆小")
        goal = goal + "\n\n" + _retry_context(previous)
    goal = goal[:MAX_GOAL]
    entry = {
        "id": f"dsp-{uuid.uuid4().hex[:10]}",
        "project": target["name"],
        "title": title,
        "goal": goal,
        "criteria": [str(item).strip()[:300] for item in (criteria or []) if str(item).strip()][:MAX_CRITERIA],
        "status": "proposed",
        "tainted": bool(tainted),
        "session": str(session or "")[:80],
        "remote_task_id": "",
        "request_id": f"assistant-{uuid.uuid4().hex}",
        "result": "",
        "error": "",
        "branch": "",
        "attempt": attempt,
        "retry_of": str(retry_of or ""),
        "brief": brief,
        "goal_link": link,
        "retry_note": retry_note,
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _transaction(root):
        items = _load(root)
        if link and link.get("criterion_id") and not retry_of:
            busy = next((item for item in items if (item.get("goal_link") or {}).get("criterion_id") == link["criterion_id"]
                         and (item.get("goal_link") or {}).get("id") == link["id"]
                         and item.get("status") not in _TERMINAL | {"stopped"}), None)
            if busy is not None:
                raise DispatchError(f"这条验收标准已经有任务单 {busy['id']}（{busy.get('status')}），别重复派")
        if sum(item.get("status") not in _TERMINAL for item in items) >= _MAX_ENTRIES:
            raise DispatchError("活动任务单已达上限，请先处理已有任务；未丢弃旧任务")
        items.append(entry)
        _save(root, items)
    return entry


def _goal_link(root: str, goal_id: str, criterion_id: str = "") -> Dict[str, str]:
    """任务单挂到个人目标上：只记 id 和标题，进度仍以目标卡为准。"""
    from src.workflow.workbench.personal_goals import PersonalGoals
    try:
        goal = PersonalGoals(root).get(str(goal_id).strip())
    except (KeyError, ValueError, OSError, TypeError) as error:
        raise DispatchError(f"找不到个人目标 {goal_id}") from error
    if goal.get("status") == "achieved":
        raise DispatchError("这个目标已经达成了，不用再为它派活")
    criterion = None
    if criterion_id:
        criterion = next((row for row in goal.get("acceptance_criteria") or [] if row.get("id") == criterion_id), None)
        if criterion is None:
            raise DispatchError(f"目标里没有验收标准 {criterion_id}")
    return {"id": goal["id"], "objective": str(goal.get("objective") or "")[:200],
            "criterion_id": str(criterion["id"]) if criterion else "",
            "criterion": str(criterion.get("text") or "")[:200] if criterion else ""}


def goal_confirm_id(dispatch_id: str) -> str:
    """「算达成这条验收标准」按钮的回调 id。"""
    return "dgl-" + dispatch_id[4:]


def goal_confirm_ready(item: Dict[str, Any]) -> bool:
    link = item.get("goal_link") if isinstance(item.get("goal_link"), dict) else {}
    return item.get("status") == "done" and not item.get("error") and bool(link.get("criterion_id")) \
        and not item.get("goal_confirmed_at")


def confirm_goal(root: str, dispatch_id: str) -> Dict[str, Any]:
    """你确认这张做完的任务单达成了它挂的那条验收标准：记一条你亲自确认的通过证据（和在目标卡上点确认一样）。"""
    from src.workflow.workbench.personal_goals import PersonalGoals
    entry = next((item for item in _load(root) if item.get("id") == dispatch_id), None)
    if entry is None:
        raise DispatchError("没有这张任务单")
    if entry.get("goal_confirmed_at"):
        return entry
    if not goal_confirm_ready(entry):
        raise DispatchError("只有挂了验收标准、成功做完的任务单能这样确认")
    link = entry["goal_link"]
    evidence = entry.get("delivery_evidence") if isinstance(entry.get("delivery_evidence"), dict) else {}
    summary = (f"任务单「{entry.get('title')}」做完（{entry.get('project')}"
               + (f"，分支 {entry['branch']}" if entry.get("branch") else "")
               + (f"，自动验证 {evidence['verification']}" if evidence.get("verification") else "")
               + (f"，PR {entry['pr_url']}" if entry.get("pr_url") else "") + "），用户确认达成这条标准")
    service = PersonalGoals(root)
    try:
        goal = service.get(link["id"])
        service.evidence(link["id"], link["criterion_id"],
                         {"revision": goal["revision"], "summary": summary, "passed": True, "confirmed": True})
    except (KeyError, ValueError, OSError, TypeError) as error:
        raise DispatchError(f"没记上：{error}") from error
    return _update(root, dispatch_id, lambda item: item.update(goal_confirmed_at=_now()))


def record_outcome(root: str, dispatch_id: str, outcome: Dict[str, Any]) -> Dict[str, Any]:
    """记下助理对一张结束的任务单的复盘（逐条验收 / 失败诊断）。只记一次：结束一轮复盘一次。"""
    return _update(root, dispatch_id, lambda item: item.update(outcome={**outcome, "at": _now()}))


def list_dispatches(root: str) -> List[Dict[str, Any]]:
    """最新的在前。"""
    return list(reversed(_load(root)))


def _update(root: str, dispatch_id: str, change: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    with _transaction(root):
        items = _load(root)
        for item in items:
            if item.get("id") == dispatch_id:
                change(item)
                item["updated_at"] = _now()
                _save(root, items)
                return dict(item)
    raise DispatchError(f"没有这张任务单：{dispatch_id}")


def cancel(root: str, dispatch_id: str) -> Dict[str, Any]:
    def change(item: Dict[str, Any]) -> None:
        if item.get("status") != "proposed" and not (item.get("status") == "send_failed" and item.get("submission_attempted") is False):
            raise DispatchError("只有还没下发的任务单能撤回")
        item["status"] = "cancelled"
    return _update(root, dispatch_id, change)


def _tail_lines(text: Any, limit: int) -> str:
    """取结尾一段，但从整行开始（别把「vorto/auto-4c82d9d8」截成「uto-4c82d9d8」推到手机上）。"""
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    tail = value[-limit:]
    cut = tail.find("\n")
    return tail[cut + 1:] if 0 <= cut < len(tail) - 1 else tail


def _service_url(project: Dict[str, Any], path: str) -> str:
    return f"http://127.0.0.1:{int(project['port'])}{path}"


def _headers() -> Dict[str, str]:
    token = os.getenv("VORTOCODE_API_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


async def _http(method: str, url: str, payload: Optional[dict] = None, *, seconds: float = 15) -> Dict[str, Any]:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=seconds)
    # trust_env=False：同机回环，不经代理
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as client:
        async with client.request(method, url, json=payload, headers=_headers()) as response:
            text = await response.text()
            if response.status >= 400:
                raise DispatchError(f"项目运行时返回 {response.status}：{text[:200]}")
            try:
                data = json.loads(text)
            except ValueError as error:
                raise DispatchError("项目运行时返回的不是 JSON") from error
            return data if isinstance(data, dict) else {}


async def approve(root: str, dispatch_id: str, *, http=_http) -> Dict[str, Any]:
    """人点了「下发」：把任务单交给项目运行时，记下远端任务 id。"""
    def claim(item: Dict[str, Any]) -> None:
        if item.get("legacy_submission"):
            raise DispatchError("旧运行时未确认幂等提交，请到项目工作台核对；未再次下发")
        if item.get("status") not in {"proposed", "send_failed", "submission_unknown", "sending"}:
            raise DispatchError("这张任务单已经下发或撤回了")
        if item["status"] != "proposed" and not item.get("request_id"):
            raise DispatchError("旧任务单的提交结果未核实，不能安全重试；请到项目工作台核对")
        item.setdefault("request_id", f"assistant-{uuid.uuid4().hex}")
        if item["status"] == "proposed":
            # 任务归属一个专属会话，开发流水线缺关键信息时才能提问（无主任务问不了，只能瞎猜或失败）。
            # 只在首次下发时定：重试必须沿用同一份提交合同（幂等键包含归属）。
            item.setdefault("remote_session", f"assistant-{dispatch_id[4:]}")
            # 项目经验也在首次下发时定下来，写进任务单：重试沿用同一份 prompt（幂等提交）
            item.setdefault("known_answers", project_answers(root, str(item.get("project") or "")))
        item.update(status="sending", remote_task_id=submission_task_id(item["request_id"]), error="")
    try:
        with pipeline_lock(root, dispatch_id, kind="assistant-send"):
            return await _send(root, dispatch_id, claim, http)
    except PipelineBusy as error:
        raise DispatchError("正在下发，请核对状态；未重复提交") from error


async def _send(root: str, dispatch_id: str, claim, http) -> Dict[str, Any]:
    entry = _update(root, dispatch_id, claim)
    attempted = bool(entry.get("submission_attempted"))
    try:
        project = _find_project(entry["project"])
        target = {"port": project["port"], "repo_root": project["repo_root"]}
        if entry.get("submission_target") and entry["submission_target"] != target:
            raise DispatchError("任务单对应的项目登记已变化，请先核对原项目；未再次下发")
        if not entry.get("submission_target"):
            _update(root, dispatch_id, lambda item: item.update(submission_target=target))
        capability = await http("GET", _service_url(project, "/api/tasks"))
        if capability.get("submission_idempotency") != 1:
            raise DispatchError("项目运行时不支持幂等提交，请先升级；未再次下发")
        scope = capability.get("submission_scope")
        actual_root = scope.get("repo_root") if isinstance(scope, dict) else None
        if not isinstance(actual_root, str) or actual_root != str(Path(project["repo_root"]).resolve()):
            raise DispatchError("项目运行时目录未确认或与登记不匹配，请核对项目服务；未再次下发")
        _update(root, dispatch_id, lambda item: item.update(submission_attempted=True, submission_runtime_root=actual_root))
        attempted = True
        created = await http("POST", _service_url(project, "/api/tasks"),
                             {"prompt": task_prompt(entry), "request_id": entry["request_id"],
                              "expected_repo_root": actual_root,
                              **({"session": entry["remote_session"]} if entry.get("remote_session") else {})})
        task_id = str(created.get("id") or "")
        if task_id != entry["remote_task_id"]:
            # A legacy server may have ignored the key. Preserve its returned id,
            # but never offer an unsafe automatic replay against that server.
            if task_id and safe_id(task_id) == task_id:
                _update(root, dispatch_id, lambda item: item.update(remote_task_id=task_id, legacy_submission=True))
            raise DispatchError("项目运行时未确认幂等提交，请先升级并核对实际任务")
    except Exception as error:  # noqa: BLE001 —— 下发失败记账，可重试
        message = str(error)[:300]
        _update(root, dispatch_id, lambda item: item.update(status="submission_unknown" if attempted else "send_failed", error=message,
                                                           submission_attempted=attempted, sync_state="unreachable"))
        raise DispatchError(f"{'提交结果待核对' if attempted else '下发失败'}：{message}") from error

    def sent(item: Dict[str, Any]) -> None:
        item.update(status=_REMOTE_STATUS.get(str(created.get("status") or ""), "running"),
                    remote_task_id=task_id, error="", sent_at=_now(),
                    last_checked_at=_now(), sync_state="current", sync_error="")
    return _update(root, dispatch_id, sent)


async def refresh(root: str, *, http=_http, dispatch_id: str | None = None) -> List[Dict[str, Any]]:
    """向各项目运行时查已下发任务的最新状态，写回台账。查不到的保持原样。"""
    entries = _load(root)
    if dispatch_id is not None and not any(item["id"] == dispatch_id for item in entries):
        raise DispatchError("没有这张任务单")
    candidates = [entry for entry in entries if entry.get("remote_task_id")
                  and entry.get("status") != "cancelled"
                  and (entry["id"] == dispatch_id if dispatch_id else entry.get("status") not in _TERMINAL
                       or isinstance(entry.get("recovery_task"), dict))]
    candidates.sort(key=lambda entry: entry.get("last_sync_attempt_at") or entry.get("last_checked_at") or "")
    # Keep a routine poll bounded; subsequent rounds prioritize older snapshots.
    semaphore = asyncio.Semaphore(4)

    async def check(entry):
        if not entry.get("remote_task_id") or entry.get("status") == "cancelled":
            return
        async with semaphore:
            try:
                with pipeline_lock(root, entry["id"], kind="assistant-send"):
                    current = next((item for item in _load(root) if item["id"] == entry["id"]), None)
                    if current is not None and current["status"] != "cancelled":
                        await _refresh_one(root, current, http, include_delivery=dispatch_id is not None)
            except PipelineBusy:
                return
    await asyncio.gather(*(check(entry) for entry in candidates[:20]))
    return list_dispatches(root)


async def _refresh_one(root: str, entry: dict, http, *, include_delivery: bool = False) -> None:
    try:
        project = _find_project(entry["project"])
        if entry.get("submission_target") and entry["submission_target"] != {
            "port": project["port"], "repo_root": project["repo_root"]
        }:
            raise DispatchError("项目登记已变化，无法核实原任务；未读取其它项目")
        task = await http("GET", _service_url(project, f"/api/tasks/{entry['remote_task_id']}"))
        scope = task.get("execution_scope")
        actual_root = scope.get("repo_root") if isinstance(scope, dict) else None
        if (task.get("id") != entry["remote_task_id"]
                or actual_root != str(Path(project["repo_root"]).resolve())):
            raise DispatchError("项目运行时返回的任务身份或目录不匹配，无法核实原任务")
        if task.get("status") not in _REMOTE_STATUS:
            raise DispatchError("项目未返回有效的任务状态")
    except Exception as error:  # noqa: BLE001 —— 项目运行时暂时不可达：下次再查
        _update(root, entry["id"], lambda item, error=error: item.update(
            sync_state="unreachable", sync_error=str(error)[:300], last_sync_attempt_at=_now()))
        return
    status = _REMOTE_STATUS.get(str(task.get("status") or ""), entry["status"])
    # 刚结束的那一轮顺手取交付证据（验证过没过），做完的通知里要用
    include_delivery = include_delivery or (status in {"done", "failed", "stopped"} and entry.get("status") not in _TERMINAL
                                            and entry.get("status") != "stopped")
    plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
    branch = str(task.get("branch") or plan.get("branch") or "")
    evidence, delivery_error = None, ""
    recovered, recovery_error = None, ""
    if include_delivery and task.get("resumed_task_id"):
        try:
            from src.workflow.tasks.assistant_recovery import recovery_observation
            from src.workflow.tasks.task_recovery import recovery_task_id
            child_id = task["resumed_task_id"]
            if child_id != recovery_task_id(entry['remote_task_id']):
                raise DispatchError("恢复任务标识不匹配，未读取其它任务")
            child = await http("GET", _service_url(project, f"/api/tasks/{child_id}"))
            recovered = recovery_observation(task, child, repo_root=actual_root)
            recovered['checked_at'] = _now()
            if recovered['branch'] and recovered['status'] in {'done', 'failed', 'paused', 'interrupted'}:
                from src.workflow.tasks.assistant_delivery import delivery_evidence
                snapshot = await http("GET", _service_url(project, f"/api/tasks/{child_id}/review"))
                recovered['delivery_evidence'] = delivery_evidence(snapshot, task_id=child_id, branch=recovered['branch'])
        except Exception as error:  # noqa: BLE001 — observation never changes execution or the original outcome
            recovery_error = str(error)[:300]
    elif include_delivery and entry.get("recovery_task"):
        recovery_error = "原任务已无可核实的恢复关联，请在项目中核对。"
    if include_delivery and branch and status in {"done", "failed", "stopped"}:
        try:
            from src.workflow.tasks.assistant_delivery import delivery_evidence
            snapshot = await http("GET", _service_url(project, f"/api/tasks/{entry['remote_task_id']}/review"))
            evidence = delivery_evidence(snapshot, task_id=entry["remote_task_id"], branch=branch)
        except Exception as error:  # noqa: BLE001 — task status remains valid even if review is unavailable
            delivery_error = str(error)[:300]

    def change(item: Dict[str, Any], task=task, status=status) -> None:
        item.update(status=status, result=_tail_lines(task.get("result"), 1500),
                    error=_tail_lines(task.get("error"), 500), branch=branch,
                    last_checked_at=_now(), sync_state="current", sync_error="")
        question = _open_question(task) if status == "waiting" else None
        if question:
            # 同一个问题沿用第一次看到的时间：没人答太久要提醒
            same = isinstance(item.get("question"), dict) and item["question"].get("id") == question["id"]
            question["seen_at"] = item["question"].get("seen_at") if same else _now()
        item["question"] = question
        item["progress"] = _progress(task, plan)
        if status in {"done", "failed", "stopped"} and not item.get("finished_at"):
            item["finished_at"] = _now()                 # 统计「多久做完」用；只记第一次看到结束
        elif status in {"running", "waiting"}:
            item.pop("finished_at", None)                # 停下后又续跑：重新算
        item["last_sync_attempt_at"] = _now()
        if include_delivery:
            item.update(recovery_task=recovered, recovery_error=recovery_error)
        elif item.get("recovery_task"):
            from src.workflow.tasks.assistant_recovery import observation_matches_source
            if not observation_matches_source(item['recovery_task'], task):
                item.update(recovery_task=None, recovery_error="恢复关联已变化或版本无法核实，请点击核对状态。")
        if include_delivery or branch != entry.get("branch") or status not in _TERMINAL:
            item.update(delivery_evidence=evidence, delivery_error=delivery_error)
    _update(root, entry["id"], change)


_STEP_STATES = {"pending", "running", "landed", "failed"}
# 计划整体：running → integrated（集成绿）/ integration_failed（集成红）/ done（开了 PR）/ failed
_PLAN_STAGES = {"running", "integrated", "integration_failed", "done", "failed"}
MAX_STEPS = 12


def _progress(task: dict, plan: dict) -> Optional[Dict[str, Any]]:
    """流水线做到哪了：计划的每一步（标题 + 状态）和已落地数；还没拆出计划时只给最近一条日志。都是任务输出，收紧长度。"""
    logs = task.get("log") if isinstance(task.get("log"), list) else []
    latest = _tail_lines(str(logs[-1]), 200) if logs else ""
    counts = plan.get("progress") if isinstance(plan.get("progress"), dict) else {}
    blocks = plan.get("blocks") if isinstance(plan.get("blocks"), list) else []
    if not blocks and not latest:
        return None
    steps = [{"title": " ".join(str(block.get("title") or "").split())[:120],
              "status": str(block.get("status")) if str(block.get("status")) in _STEP_STATES else "pending"}
             for block in blocks[:MAX_STEPS] if isinstance(block, dict)]

    def count(key: str) -> int:
        try:
            return max(0, int(counts.get(key) or 0))
        except (TypeError, ValueError):
            return 0
    return {"total": count("total") or len(blocks), "landed": count("landed"), "failed": count("failed"),
            "steps": steps, "more": max(0, len(blocks) - MAX_STEPS),
            "stage": str(plan.get("status")) if str(plan.get("status")) in _PLAN_STAGES else "",
            "latest": latest}


def progress_line(item: Dict[str, Any]) -> str:
    """一句话进度：「3/5 步已落地，正在做：补单测」。没有进度就空串。"""
    progress = item.get("progress") if isinstance(item.get("progress"), dict) else None
    if not progress:
        return ""
    if not progress.get("total"):
        return f"还在拆计划：{progress.get('latest')}" if progress.get("latest") else ""
    text = f"{progress.get('landed', 0)}/{progress['total']} 步已落地"
    if progress.get("failed"):
        text += f"，{progress['failed']} 步失败"
    if progress.get("stage") == "integrated":
        text += "，集成测试通过"
    elif progress.get("stage") == "integration_failed":
        text += "，集成测试没过"
    running = [step["title"] for step in progress.get("steps") or [] if step.get("status") == "running" and step.get("title")]
    if running:
        text += "，正在做：" + "、".join(running[:2])
    return text


def _open_question(task: dict) -> Optional[Dict[str, Any]]:
    """项目任务在等的那个问题（开发流水线缺关键事实时提的）。字段和长度都收紧：它是任务输出。"""
    questions = task.get("questions") if isinstance(task.get("questions"), list) else []
    for question in reversed(questions):
        if isinstance(question, dict) and question.get("status") == "open" and question.get("id"):
            options = question.get("options") if isinstance(question.get("options"), list) else []
            return {"id": str(question["id"])[:80], "round": question.get("round"),
                    "question": str(question.get("question") or "")[:1500],
                    "options": [str(option)[:200] for option in options[:3]],
                    "context": _tail_lines(question.get("context"), 600),
                    "expires_at": str(question.get("expires_at") or "")[:40]}
    return None


async def answer_question(root: str, dispatch_id: str, answer: str, *, http=None) -> Dict[str, Any]:
    """把主人的回答交给在等的项目任务；任务接着跑（用下一轮执行预算，回答不授予新权限）。"""
    answer = str(answer or "").strip()
    if not answer:
        raise DispatchError("回答不能是空的")
    entry = next((item for item in _load(root) if item.get("id") == dispatch_id), None)
    if entry is None:
        raise DispatchError("没有这张任务单")
    question = entry.get("question")
    if entry.get("status") != "waiting" or not isinstance(question, dict):
        raise DispatchError("这张任务单现在没有在等回答")
    if not entry.get("remote_session"):
        raise DispatchError("这张任务单是旧方式下发的，只能在项目工作台里回答")
    project = _find_project(entry["project"])
    try:
        await (http or _http)("POST", _service_url(project, f"/api/tasks/{entry['remote_task_id']}/answer"),
                              {"session": entry["remote_session"], "round": question.get("round"),
                               "question_id": question.get("id"), "answer": answer[:2000]})
    except DispatchError:
        raise
    except Exception as error:  # noqa: BLE001 —— 连不上项目运行时：如实说没交上，问题还在
        raise DispatchError(f"没连上项目运行时，回答没交上：{type(error).__name__}") from error

    try:
        _remember_answer(root, entry["project"], str(question.get("question") or ""), answer)
    except OSError:  # 记不下经验不影响这次回答
        pass

    def answered(item: Dict[str, Any]) -> None:
        item.update(status="running", question=None, error="", resumed_at=_now(),
                    answers=[*(item.get("answers") or []), {"question": question.get("question", "")[:300],
                                                           "answer": answer[:300], "at": _now()}][-4:])
    return _update(root, dispatch_id, answered)


async def open_pr(root: str, dispatch_id: str, *, http=None) -> Dict[str, Any]:
    """做完的任务单开一个 draft PR（人点按钮 = 人在合并口的确认；合并仍由人在 GitHub 上做）。"""
    entry = next((item for item in _load(root) if item.get("id") == dispatch_id), None)
    if entry is None:
        raise DispatchError("没有这张任务单")
    if entry.get("pr_url"):
        return entry
    _check_pr_ready(entry)
    project = _find_project(entry["project"])
    if http is None:
        async def http(method, url, payload=None):        # 推分支 + 开 PR 要走网络，给足时间
            return await _http(method, url, payload, seconds=180)
    try:
        result = await http("POST", _service_url(project, f"/api/tasks/{entry['remote_task_id']}/open_pr"), {})
    except DispatchError:
        raise
    except Exception as error:  # noqa: BLE001
        raise DispatchError(f"没连上项目运行时：{type(error).__name__}") from error
    if not result.get("ok") or not result.get("url"):
        raise DispatchError(str(result.get("error") or "项目运行时没有返回 PR 地址")[:300])
    return _update(root, dispatch_id, lambda item: item.update(pr_url=str(result["url"])[:300], pr_state="opened",
                                                                pr_error=""))


def _check_pr_ready(entry: Dict[str, Any]) -> None:
    if entry.get("status") != "done" or entry.get("error"):
        raise DispatchError("只有成功做完的任务单能开 PR")
    if not str(entry.get("branch") or "").startswith("vorto/"):
        raise DispatchError("这张任务单没有交付分支")


PR_OPENING_STALE_SECONDS = 600          # 「正在开」超过这么久多半是进程重启丢了：允许再点
_PR_JOBS: set = set()                   # 持有后台任务的引用，免得被回收


def _opening(item: Dict[str, Any]) -> bool:
    if item.get("pr_state") != "opening":
        return False
    try:
        started = datetime.fromisoformat(str(item.get("pr_started_at")))
    except (TypeError, ValueError):
        return False
    return (datetime.now(timezone.utc) - started).total_seconds() < PR_OPENING_STALE_SECONDS


def begin_open_pr(root: str, dispatch_id: str, *, http=None) -> Dict[str, Any]:
    """在后台开 draft PR，立刻返回：推分支可能几分钟，Desktop 连常驻电脑的请求等不了那么久。卡片看 pr_state。"""
    entry = next((item for item in _load(root) if item.get("id") == dispatch_id), None)
    if entry is None:
        raise DispatchError("没有这张任务单")
    if entry.get("pr_url"):
        return entry
    _check_pr_ready(entry)
    started: List[bool] = []

    def mark(item: Dict[str, Any]) -> None:
        if item.get("pr_url") or _opening(item):        # 已开 / 正在开：连点只开一次
            return
        item.update(pr_state="opening", pr_error="", pr_started_at=_now())
        started.append(True)
    entry = _update(root, dispatch_id, mark)
    if not started:
        return entry

    async def run() -> None:
        try:
            await open_pr(root, dispatch_id, http=http)
        except Exception as error:  # noqa: BLE001 —— 结果落在任务单上，卡片如实显示
            reason = str(error) if isinstance(error, DispatchError) else f"没开成：{type(error).__name__}"
            _update(root, dispatch_id, lambda item: item.update(pr_state="failed", pr_error=reason[:300]))
    job = asyncio.get_running_loop().create_task(run())
    _PR_JOBS.add(job)
    job.add_done_callback(_PR_JOBS.discard)
    return entry


def pr_confirm_id(dispatch_id: str) -> str:
    """「开 PR」按钮的回调 id：和「下发」按钮（就是任务单 id）区分开。"""
    return "dpr-" + dispatch_id[4:]


def build_dispatch_tools(root: str, *, is_tainted: Callable[[], bool] = lambda: False,
                         session: "str | Callable[[], Any]" = "") -> list:
    """常驻助理的派活工具：提议任务单、查看进度。没有登记项目时不提供。"""
    from src.agents.tool import Tool

    if not load_resident_projects():
        return []
    names = "、".join(project["name"] for project in load_resident_projects())

    async def _propose(args: dict) -> str:
        criteria = args.get("criteria") or []
        if isinstance(criteria, str):
            criteria = [line.strip("-• ").strip() for line in criteria.splitlines() if line.strip()]
        try:
            entry = propose(root, project=str(args.get("project") or ""), title=str(args.get("title") or ""),
                            goal=str(args.get("goal") or ""), criteria=list(criteria),
                            # 记下是哪段对话提的：Desktop 据此把任务单卡片放进那段对话里
                            session=str((session() if callable(session) else session) or ""),
                            tainted=is_tainted(), retry_of=str(args.get("retry_of") or "").strip(),
                            goal_id=str(args.get("goal_id") or "").strip(),
                            criterion_id=str(args.get("criterion_id") or "").strip())
        except DispatchError as error:
            return f"没有提出任务单：{error}"
        return (f"已写好任务单 {entry['id']}（{entry['project']}：{entry['title']}），卡片会出现在这段对话里，"
                "手机通知上也能批准。**还没有执行**——要等用户确认下发。告诉用户在卡片上确认，不要声称已经开始做。")

    async def _list(_args: dict) -> str:
        items = await refresh(root)
        if not items:
            return "还没有任务单。"
        rows = []
        for item in items[:20]:
            line = f"- [{item['status']}] {item['project']}：{item['title']}（{item['id']}）"
            if int(item.get("attempt") or 1) > 1:
                line += f" 第 {item['attempt']} 次"
            if item.get("sync_state") == "unreachable":
                line += f" 当前无法核实；上次核对：{item.get('last_checked_at') or '尚未核对'}"
            evidence = item.get("delivery_evidence")
            if isinstance(evidence, dict):
                states = {"passed": "该提交验证通过", "failed": "该提交验证失败", "stale": "验证证据已过期", "unknown": "尚无该提交的验证证据"}
                line += f" 提交 {str(evidence.get('head') or '')[:12]}：{states.get(evidence.get('verification'), '验证未知')}"
                line += "；不代表人工验收通过"
            if item["status"] in {"running", "waiting"} and progress_line(item):
                line += f" 进度：{progress_line(item)}"
            link = item.get("goal_link") if isinstance(item.get("goal_link"), dict) else {}
            if link:
                line += f" 为目标「{link.get('objective', '')[:60]}」" + (f"的标准「{link.get('criterion', '')[:60]}」" if link.get("criterion") else "")
                if item.get("goal_confirmed_at"):
                    line += "（用户已确认达成）"
            if item.get("branch"):
                line += f" 分支 {item['branch']}"
            if item.get("pr_url"):
                line += f" PR {item['pr_url']}"
            question = item.get("question")
            if item["status"] == "waiting" and isinstance(question, dict):
                options = "；".join(question.get("options") or [])
                line += f" 在问：{question.get('question', '')[:300]}" + (f"（建议选项：{options}）" if options else "")
            outcome = item.get("outcome") if isinstance(item.get("outcome"), dict) else {}
            if outcome.get("kind") == "acceptance" and outcome.get("criteria"):
                rows = outcome["criteria"]
                line += f" 验收对照（助理判断）{sum(row.get('verdict') == 'met' for row in rows)}/{len(rows)} 条对上"
            elif outcome.get("kind") == "diagnosis":
                line += f" 复盘：{str(outcome.get('diagnosis') or '')[:120]}"
                if outcome.get("draft_id"):
                    line += f"（已起草重试 {outcome['draft_id']}）"
            if item.get("error"):
                line += f" 错误：{item['error'][:120]}"
            elif item.get("result") and item["status"] == "done":
                line += f" 结果：{item['result'][-200:]}"
            rows.append(line)
        return "状态：proposed=等用户确认下发，running=在做，waiting=在等人，done=执行结束待审查，不代表验收通过，failed=失败\n" + "\n".join(rows)

    async def _answer(args: dict) -> str:
        if is_tainted():
            return "没有转交：这一轮读过网页等外部内容，回答只能由用户在 Desktop 或手机上直接给。"
        try:
            entry = await answer_question(root, str(args.get("dispatch_id") or ""), str(args.get("answer") or ""))
        except DispatchError as error:
            return f"没有转交：{error}"
        return f"已把回答交给「{entry['title']}」，任务接着做；做完或再有问题会通知用户。"

    async def _answer_memory(args: dict) -> str:
        project = str(args.get("project") or "").strip()
        try:
            name = _find_project(project)["name"]
        except DispatchError as error:
            return str(error)
        if str(args.get("action") or "list").strip() == "forget":
            if is_tainted():
                return "没有删：这一轮读过网页等外部内容，删记下的回答要用户直接说。"
            try:
                removed = forget_answers(root, name, int(args.get("index") or 0))
            except (DispatchError, ValueError) as error:
                return f"没有删：{error}"
            return f"已忘掉 {name} 记下的 {removed} 条回答。"
        rows = project_answers(root, name)
        if not rows:
            return f"{name} 还没有记下的回答。"
        return (f"{name} 记下的回答（以后派这个项目的活会带上，供任务参考）：\n"
                + "\n".join(f"{index}. 问：{row.get('question')} 答：{row.get('answer')}" for index, row in enumerate(rows, 1)))

    async def _stats(args: dict) -> str:
        from src.workflow.tasks.dispatch_stats import dispatch_stats, stats_text
        try:
            days = min(365, max(1, int(args.get("days") or 30)))
        except (TypeError, ValueError):
            days = 30
        return stats_text(dispatch_stats(root, datetime.now(timezone.utc), days))

    return [
        Tool("dispatch_stats",
             "派活的成绩：最近 N 天下发多少、做成多少、第一次就成的比例、重试后成的、问了用户几次、通常多久做完、"
             "验收标准对上多少。用户问「派活效果怎么样」「成功率多少」时用。",
             {"days": "统计最近几天，默认 30"}, _stats, read_only=True),
        Tool("dispatch_answer_memory",
             "查看或删除某个项目记下的回答：派出去的任务问过、用户答过的，会记下来，以后派同一个项目的活时自动带上，"
             "免得同样的问题再问。用户说「那个回答只是这次的」「别再按那个来」时用 forget 删掉。",
             {"project": f"项目名，只能是：{names}", "action": "list（默认）或 forget",
              "index": "forget 时：删第几条（list 里的编号）；不填删全部"},
             _answer_memory, read_only=False, serial=True),
        Tool("answer_dispatch_question",
             "把**用户自己给出的**回答转交给在等回答的任务单（list_dispatches 里 status=waiting、带「在问」的那张）。"
             "只转述用户在这段对话里明确说的答案，不要自己替用户拿主意；用户没答就先把问题和选项告诉用户。",
             {"dispatch_id": "任务单 id（dsp-…）", "answer": "用户的回答，原意转述，写清楚"},
             _answer, read_only=False, serial=True),
        Tool("propose_task",
             "把一件开发/修改代码的活写成任务单，交给常驻电脑上的项目去做（隔离分支交付）。"
             f"只是提议：用户在 Desktop 确认后才会执行。可派活的项目：{names}。"
             "目标要写清楚背景、要改什么、范围边界；验收标准写成可检查的几条。"
             "用户让你推进某个个人目标时：先用 personal_goals 看它的验收标准，对还没通过、靠改代码能达成的每一条"
             "各写一张任务单并填 goal_id / criterion_id（一条标准一张，不要合并）；不靠改代码的标准告诉用户要他自己做什么。",
             {"project": f"项目名，只能是：{names}", "title": "一句话标题（不超过 30 字）",
              "goal": "要做成什么、为什么、范围边界", "criteria": "验收标准，每行一条",
              "retry_of": "可选：接着哪张失败 / 没做完的任务单再来（dsp-…）。会自动带上它的错误和输出，"
                          "goal 里只写这次要怎么改做法；同一件事最多 3 次",
              "goal_id": "可选：这件活是为哪个个人目标做的（personal-…，用 personal_goals 查）",
              "criterion_id": "可选：对应目标里的哪条验收标准；做完后会问用户算不算达成这一条"},
             _propose, read_only=False, serial=True),
        Tool("list_dispatches",
             "查看派出去的任务单：哪些等用户确认、在做、做完了（含结果和分支）、失败了。向用户汇报进度时先调它。",
             {}, _list, untrusted_source=True),
    ]
