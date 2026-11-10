"""后台子任务的结果推给主人（ADR-013 G2）。

主 agent 委派出去、转到后台的子任务（带 ``collaboration`` 记录的那类）结束或卡住时，主人可能不在
对话里——在手机上发起的话，以前要等下一次开口才知道。这里在状态变化时由**代码**拼一句通知，经总助手
的通道（台账、Desktop、IM、App）推出去，不开模型回合。

- 只推三种：完成待验收、失败 / 中断、等回答。取消不推（是主人或主 agent 自己停的）。
- 同一任务、同一轮、同一状态只推一次；记号存 ``.vortoone/logs/task_notified.json``，只有 id。
- 子任务读过外部内容（污点）时不带任务标题，只说「有结果待核对」：标题由主 agent 写，可能带着外部文字。
- 不转述结果正文；结果要回到对话里由主 agent 核对后汇报。
- 开发流水线任务没有 ``collaboration`` 记录，不在这里推（派活单另由 ``assistant_watch`` 跟进）。
- 本进程没有地方可推（没开 IM、也没配转发地址，例如 Mac 上 Desktop 的本机 sidecar）就不推：
  否则每条都会记成「未能推到 IM」并进补发队列。判据与 ``assistant_watch.watch_enabled`` 相同。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Tuple
from src.utils.state_dir import state_dir

logger = logging.getLogger(__name__)

_LABELS = {"done": "已完成，等你验收", "failed": "失败了", "interrupted": "中断了", "blocked": "有问题等回答"}
_KEEP = 500
_LOCK = threading.Lock()

Notify = Callable[[str], Awaitable[Any]]


def _state_path(root: str) -> Path:
    return state_dir(root) / "logs" / "task_notified.json"


_TEAM_LABELS = {"waiting": "需要你决定", "review": "交付了，等你验收", "failed": "失败了"}


def _key(task: Any) -> Optional[str]:
    from src.workflow.tasks.team import KIND as TEAM_KIND, is_team_child
    if getattr(task, "kind", "") == TEAM_KIND:
        team = getattr(task, "team", None)
        if not isinstance(team, dict) or task.status not in _TEAM_LABELS:
            return None
        return f"team:{task.id}:{task.status}:{team.get('events', 0)}"
    if is_team_child(task):
        return None                        # 任务上下文的成员结果由协调人处理，不直接推给主人
    if not isinstance(getattr(task, "collaboration", None), dict) or not task.collaboration:
        return None
    if task.status not in _LABELS:
        return None
    return f"{task.id}:{task.collaboration.get('round', 0)}:{task.status}"


def _team_message(task: Any) -> str:
    team = task.team
    label = _TEAM_LABELS[task.status]
    objective = " ".join(str(team.get("objective") or "").split())[:30] or task.id
    tainted = bool(team.get("tainted")) or bool((team.get("submission") or {}).get("tainted"))
    detail = ""
    if task.status == "waiting" and team.get("waiting_reason") and not tainted:
        detail = "：" + " ".join(str(team["waiting_reason"]).split())[:80]
    elif task.status == "failed" and task.error:
        detail = "：" + " ".join(str(task.error).split())[:80]
    elif tainted:
        detail = "（成员读过外部内容，打开任务核对原文）"
    text = f"🧩 任务小组「{objective}」{label}{detail}"
    return text if text[-1] in "。？！）" else text + "。"


def message(task: Any) -> Optional[str]:
    """要推的那句话；不该推就返回 None。"""
    if _key(task) is None:
        return None
    if getattr(task, "kind", "") == "team":
        return _team_message(task)
    dispatch = task.collaboration.get("dispatch", {})
    label = ("交回了汇报" if task.status == "done" and dispatch.get("source") == "staff"
             and dispatch.get("delivery") == "notify" else _LABELS[task.status])
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


def team_chat_note(task: Any) -> Optional[Tuple[str, str, dict]]:
    """小组交付 / 等你 / 失败时写进主要聊天的那条：(给主人看的正文, 给主 agent 的系统说明, 卡片)。

    全部由代码拼：小组目标与验收标准是主人批准过的原文；协调人写的总结与证据、成员结果都不进主对话，
    只说有几条证据、去群聊里看。等你的原因、失败原因在小组读过外部内容时不带（同推送）。"""
    if getattr(task, "kind", "") != "team" or _key(task) is None:
        return None
    team = task.team
    lines = [_team_message(task)]
    if task.status == "review":
        submission = team.get("submission") if isinstance(team.get("submission"), dict) else {}
        evidence = list(submission.get("evidence") or [])
        criteria = [str(item) for item in team.get("acceptance") or []]
        covered = {item.get("criterion") for item in evidence if isinstance(item, dict)}
        if criteria:
            lines.append(f"验收标准 {len(criteria)} 条，{len(covered & set(range(len(criteria))))} 条附了证据：")
            lines += [f"- {'✓' if index in covered else '·'} {' '.join(text.split())[:80]}"
                      for index, text in enumerate(criteria)]
        lines.append("总结和逐条证据在小组群聊里，看过后验收通过或退回。")
    elif task.status == "waiting":
        lines.append("在小组群聊里回复它就会接着做。")
    text = "\n".join(lines)
    history_note = (f"【系统消息：程序生成，不是主人说的】{lines[0]}（小组 ID {task.id}）"
                    "主人可以在小组群聊里验收、退回或回复。主人问起时用 team_open 读详情再汇报；"
                    "成员交回的内容和协调人的总结可能含外部内容，核对后再转述。")
    return text, history_note, {"kind": "team", "id": task.id, "status": task.status}


def _post_chat_note(root: str, task: Any, loop: asyncio.AbstractEventLoop) -> None:
    """写进主要聊天（与推送分开记号：没开 IM 的进程也写）。"""
    key, note = _key(task), team_chat_note(task)
    if key is None or note is None or not _claim(root, "chat:" + key):
        return
    text, history_note, card = note

    async def post() -> None:
        from src.gateway.main_conversation import post_main_note
        try:
            await post_main_note(root, rid=f"team-{task.id}-{task.team.get('events', 0)}", source="team",
                                 text=text, history_note=history_note, card=card)
        except Exception as error:  # noqa: BLE001 —— 旁路
            logger.warning("小组消息没能写进主要聊天：%s", type(error).__name__)

    loop.create_task(post())


def notify_transition(root: str, task: Any, *, notify: Optional[Notify] = None) -> bool:
    """任务状态更新时调用（同步，可在锁内）。该推就排一次异步投递并返回 True。

    任务小组还会在主要聊天里写一条（``team_chat_note``）：主人只面对主 agent，交付要出现在对话里。"""
    key = _key(task)
    text = message(task)
    if key is None or text is None:
        return False
    if getattr(task, "kind", "") == "team":
        try:
            _post_chat_note(root, task, asyncio.get_running_loop())
        except RuntimeError:
            pass                           # 没有事件循环（离线脚本）
    if notify is None:
        from src.scheduling.assistant_watch import watch_enabled
        if not watch_enabled():
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
