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
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.workflow.workbench.resident_projects import load_resident_projects

MAX_TITLE = 60
MAX_GOAL = 4000
MAX_CRITERIA = 10
_MAX_ENTRIES = 200
_LOCK = threading.Lock()

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
    except (OSError, ValueError):
        return []
    items = data.get("dispatches") if isinstance(data, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _save(root: str, items: List[Dict[str, Any]]) -> None:
    path = _ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"dispatches": items[-_MAX_ENTRIES:]}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, path)


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
    return "\n".join(lines)


def propose(root: str, *, project: str, title: str, goal: str, criteria: Optional[List[str]] = None,
            session: str = "", tainted: bool = False) -> Dict[str, Any]:
    """助理提出一张任务单（只记账，不执行）。"""
    target = _find_project(project)
    title = " ".join(str(title or "").split())[:MAX_TITLE]
    goal = str(goal or "").strip()[:MAX_GOAL]
    if not title or not goal:
        raise DispatchError("任务单需要标题和目标")
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
        "result": "",
        "error": "",
        "branch": "",
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _LOCK:
        items = _load(root)
        items.append(entry)
        _save(root, items)
    return entry


def list_dispatches(root: str) -> List[Dict[str, Any]]:
    """最新的在前。"""
    return list(reversed(_load(root)))


def _update(root: str, dispatch_id: str, change: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    with _LOCK:
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
        if item.get("status") not in ("proposed", "send_failed"):
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


async def _http(method: str, url: str, payload: Optional[dict] = None) -> Dict[str, Any]:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=15)
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
    entry = next((item for item in _load(root) if item.get("id") == dispatch_id), None)
    if entry is None:
        raise DispatchError(f"没有这张任务单：{dispatch_id}")
    if entry.get("status") not in ("proposed", "send_failed"):
        raise DispatchError("这张任务单已经下发或撤回了")
    project = _find_project(entry["project"])
    try:
        created = await http("POST", _service_url(project, "/api/tasks"), {"prompt": task_prompt(entry)})
    except Exception as error:  # noqa: BLE001 —— 下发失败记账，可重试
        message = str(error)[:300]
        _update(root, dispatch_id, lambda item: item.update(status="send_failed", error=message))
        raise DispatchError(f"下发失败：{message}") from error
    task_id = str(created.get("id") or "")

    def sent(item: Dict[str, Any]) -> None:
        item.update(status="running", remote_task_id=task_id, error="", sent_at=_now())
    return _update(root, dispatch_id, sent)


async def refresh(root: str, *, http=_http) -> List[Dict[str, Any]]:
    """向各项目运行时查已下发任务的最新状态，写回台账。查不到的保持原样。"""
    for entry in _load(root):
        if not entry.get("remote_task_id") or entry.get("status") in ("done", "failed", "cancelled"):
            continue
        try:
            project = _find_project(entry["project"])
            task = await http("GET", _service_url(project, f"/api/tasks/{entry['remote_task_id']}"))
        except Exception:  # noqa: BLE001 —— 项目运行时暂时不可达：下次再查
            continue
        status = _REMOTE_STATUS.get(str(task.get("status") or ""), entry["status"])

        def change(item: Dict[str, Any], task=task, status=status) -> None:
            item.update(status=status, result=_tail_lines(task.get("result"), 1500),
                        error=_tail_lines(task.get("error"), 500), branch=str(task.get("branch") or ""))
        _update(root, entry["id"], change)
    return list_dispatches(root)


def build_dispatch_tools(root: str, *, is_tainted: Callable[[], bool] = lambda: False,
                         session: str = "") -> list:
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
                            session=session, tainted=is_tainted())
        except DispatchError as error:
            return f"没有提出任务单：{error}"
        return (f"已写好任务单 {entry['id']}（{entry['project']}：{entry['title']}），放在右侧「动态」的"
                "「等你处理」里。**还没有执行**——要等用户确认下发。告诉用户去确认，不要声称已经开始做。")

    async def _list(_args: dict) -> str:
        items = await refresh(root)
        if not items:
            return "还没有任务单。"
        rows = []
        for item in items[:20]:
            line = f"- [{item['status']}] {item['project']}：{item['title']}（{item['id']}）"
            if item.get("branch"):
                line += f" 分支 {item['branch']}"
            if item.get("error"):
                line += f" 错误：{item['error'][:120]}"
            elif item.get("result") and item["status"] == "done":
                line += f" 结果：{item['result'][-200:]}"
            rows.append(line)
        return "状态：proposed=等用户确认下发，running=在做，waiting=在等人，done=完成，failed=失败\n" + "\n".join(rows)

    return [
        Tool("propose_task",
             "把一件开发/修改代码的活写成任务单，交给常驻电脑上的项目去做（隔离分支交付）。"
             f"只是提议：用户在 Desktop 确认后才会执行。可派活的项目：{names}。"
             "目标要写清楚背景、要改什么、范围边界；验收标准写成可检查的几条。",
             {"project": f"项目名，只能是：{names}", "title": "一句话标题（不超过 30 字）",
              "goal": "要做成什么、为什么、范围边界", "criteria": "验收标准，每行一条"},
             _propose, read_only=False, serial=True),
        Tool("list_dispatches",
             "查看派出去的任务单：哪些等用户确认、在做、做完了（含结果和分支）、失败了。向用户汇报进度时先调它。",
             {}, _list),
    ]
