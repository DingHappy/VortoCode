"""跟进建议：开发子 Agent 在任务范围外发现、但值得做的事。

子 Agent 不顺手去改（那会让改动越界、diff 变大、审查变难），而是用 ``suggest_followup`` 记一条；
Desktop 决策中心里一键「开始」就作为新的后台开发任务启动，或者「忽略」。
它们是建议、不挡任何事，所以不进早晚报和语音的「等你处理」。

台账：``.vortocode/followups.json``。同名（规整后）的未处理建议只留一条；每个来源最多 3 条；
未处理的总数有上限，满了拒绝新增而不是挤掉旧的。
"""
from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.workflow.pipeline.pipeline_storage import PipelineBusy, pipeline_lock, write_json

MAX_OPEN = 30
MAX_PER_SOURCE = 3
MAX_TITLE = 80
MAX_DETAIL = 1500
_LOCK = threading.Lock()


class FollowupError(ValueError):
    """建议不合法、已满或状态不允许当前操作。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(root: str) -> Path:
    return Path(root) / ".vortocode" / "followups.json"


@contextmanager
def _transaction(root: str):
    with _LOCK:
        try:
            with pipeline_lock(root, "followups", kind="followups"):
                yield
        except PipelineBusy as error:
            raise FollowupError("跟进建议正在更新，请稍后再试") from error


def _load(root: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        raise FollowupError("跟进建议台账无法读取，已保留原文件") from error
    items = data.get("followups") if isinstance(data, dict) else None
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise FollowupError("跟进建议台账格式损坏，已保留原文件")
    return items


def _key(title: str) -> str:
    return "".join(str(title).split()).lower()


def add(root: str, *, title: str, detail: str = "", source: str = "", tainted: bool = False) -> Dict[str, Any]:
    title = " ".join(str(title or "").split())[:MAX_TITLE]
    detail = str(detail or "").strip()[:MAX_DETAIL]
    source = " ".join(str(source or "").split())[:200]
    if not title:
        raise FollowupError("跟进建议需要一句话标题")
    with _transaction(root):
        items = _load(root)
        open_items = [item for item in items if item.get("status") == "open"]
        same = next((item for item in open_items if _key(item.get("title", "")) == _key(title)), None)
        if same is not None:
            return dict(same)                                     # 重试或别的任务记过同一件事：不重复
        if source and sum(item.get("source") == source for item in open_items) >= MAX_PER_SOURCE:
            raise FollowupError(f"这个任务已记了 {MAX_PER_SOURCE} 条跟进建议，挑最重要的记")
        if len(open_items) >= MAX_OPEN:
            raise FollowupError("未处理的跟进建议已满，请先在 Desktop 里开始或忽略一些")
        entry = {"id": f"fu-{uuid.uuid4().hex[:10]}", "title": title, "detail": detail, "source": source,
                 "tainted": bool(tainted), "status": "open", "task_id": "", "created": _now(), "updated": _now()}
        items.append(entry)
        write_json(_path(root), {"followups": items[-200:]})
        return dict(entry)


def list_open(root: str) -> List[Dict[str, Any]]:
    """未处理的建议，最新的在前。"""
    return [dict(item) for item in reversed(_load(root)) if item.get("status") == "open"]


def _update(root: str, followup_id: str, **changes: Any) -> Dict[str, Any]:
    with _transaction(root):
        items = _load(root)
        for item in items:
            if item.get("id") == followup_id:
                if item.get("status") != "open":
                    raise FollowupError("这条跟进建议已经处理过了")
                item.update(changes, updated=_now())
                write_json(_path(root), {"followups": items})
                return dict(item)
    raise FollowupError(f"没有这条跟进建议：{followup_id}")


def get_open(root: str, followup_id: str) -> Optional[Dict[str, Any]]:
    return next((item for item in list_open(root) if item.get("id") == followup_id), None)


def dismiss(root: str, followup_id: str) -> Dict[str, Any]:
    return _update(root, followup_id, status="dismissed")


def mark_started(root: str, followup_id: str, task_id: str) -> Dict[str, Any]:
    return _update(root, followup_id, status="started", task_id=str(task_id))


def task_prompt(entry: Dict[str, Any]) -> str:
    """作为新后台任务启动时交给开发流水线的描述。"""
    parts = [entry["title"]]
    if entry.get("detail"):
        parts.append(entry["detail"])
    if entry.get("source"):
        parts.append(f"（这是之前任务「{entry['source'][:80]}」做的时候在范围外发现的问题。）")
    return "\n\n".join(parts)


def decision_items(root: str) -> List[Dict[str, Any]]:
    """给 Desktop 决策中心：低优先级、可一键开始或忽略。读坏了返回空，不挡别的决策项。"""
    try:
        items = list_open(root)
    except FollowupError:
        return []
    return [{
        "id": f"followup:{item['id']}", "kind": "followup", "severity": "low",
        "title": item["title"], "detail": item.get("detail") or (f"来自：{item['source']}" if item.get("source") else ""),
        "created": item.get("created", ""), "target_id": item["id"], "action": "start_followup",
        "tainted": bool(item.get("tainted")), "can_dismiss": True,
    } for item in items]
