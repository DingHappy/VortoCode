"""后台子任务的结果推给主人（ADR-013 G2）。

主 agent 委派出去、转到后台的子任务（带 ``collaboration`` 记录的那类）结束或卡住时，主人可能不在
对话里——在手机上发起的话，以前要等下一次开口才知道。这里在状态变化时由**代码**拼一句通知，经总助手
的通道（台账、Desktop、IM、App）推出去，不开模型回合。

- 只推三种：完成待验收、失败 / 中断、等回答。取消不推（是主人或主 agent 自己停的）。
- 同一任务、同一轮、同一状态只推一次；记号存 ``.vortocode/logs/task_notified.json``，只有 id。
- 子任务读过外部内容（污点）时不带任务标题，只说「有结果待核对」：标题由主 agent 写，可能带着外部文字。
- 不转述结果正文；结果要回到对话里由主 agent 核对后汇报。
- 开发流水线任务没有 ``collaboration`` 记录，不在这里推（派活单另由 ``assistant_watch`` 跟进）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

_LABELS = {"done": "已完成，等你验收", "failed": "失败了", "interrupted": "中断了", "blocked": "有问题等回答"}
_KEEP = 500
_LOCK = threading.Lock()

Notify = Callable[[str], Awaitable[Any]]


def _state_path(root: str) -> Path:
    return Path(root) / ".vortocode" / "logs" / "task_notified.json"


def _key(task: Any) -> Optional[str]:
    if not isinstance(getattr(task, "collaboration", None), dict) or not task.collaboration:
        return None
    if task.status not in _LABELS:
        return None
    return f"{task.id}:{task.collaboration.get('round', 0)}:{task.status}"


def message(task: Any) -> Optional[str]:
    """要推的那句话；不该推就返回 None。"""
    if _key(task) is None:
        return None
    label = _LABELS[task.status]
    if task.collaboration.get("tainted"):
        return f"🧩 一个后台子任务{label}（它读过外部内容，结果要核对）。回到对话里让我汇报。"
    title = " ".join(str(task.prompt or "").split())[:30] or task.id
    return f"🧩 后台子任务「{title}」{label}。回到对话里让我汇报。"


def _claim(root: str, key: str) -> bool:
    """记下这条要推；已经推过返回 False。先记后推：宁可漏一条，也不重复。"""
    path = _state_path(root)
    with _LOCK:
        try:
            seen = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(seen, list):
                seen = []
        except (OSError, ValueError):
            seen = []
        if key in seen:
            return False
        seen = [*seen, key][-_KEEP:]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(seen), encoding="utf-8")
            tmp.replace(path)
        except OSError as error:
            logger.warning("子任务通知记号保存失败：%s", type(error).__name__)
            return False
        return True


def notify_transition(root: str, task: Any, *, notify: Optional[Notify] = None) -> bool:
    """任务状态更新时调用（同步，可在锁内）。该推就排一次异步投递并返回 True。"""
    key = _key(task)
    text = message(task)
    if key is None or text is None:
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False                       # 没有事件循环（离线脚本）：不推，也不占记号
    if not _claim(root, key):
        return False
    if notify is None:
        from src.scheduling.notices import make_notifier
        notify = make_notifier(root, source="assistant")

    async def deliver() -> None:
        try:
            await notify(text)
        except Exception as error:  # noqa: BLE001 —— 推送是旁路，不影响任务状态
            logger.warning("子任务通知未送出：%s", type(error).__name__)

    loop.create_task(deliver())
    return True
