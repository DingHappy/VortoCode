"""在办工作汇总（ADR-013 G3）：主 agent 一次读清「现在都有哪些事、各在哪一步」。

只读、零新状态，每个来源用它自己的公开读法：

| 段 | 来源 |
| --- | --- |
| 等你处理 | ``workflow.tasks.attention.attention_items``（早晚报、Desktop 决策中心同一份） |
| 进行中 · 任务单 | ``assistant_dispatch.list_dispatches`` 里下发中 / 已下发 / 执行中的 |
| 进行中 · 目标 | ``PersonalGoals.list`` 里未达成的 |
| 进行中 · Codex | ``codex_watch.thread_states``（与 ``codex_status`` 同一套状态判断） |
| 定时异常 | ``assistant_watch.schedule_overview`` 里连续失败的 |

读不到的来源单独列出来，不当成「没有」。每条带精确 id，细节再用对应的专用工具查。
内容含外部来源的文字（Codex 的会话名、任务单标题等），工具按外部内容标记。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)

_PER_SECTION = 8
_DISPATCH_LIVE = {"sending": "下发中", "sent": "已下发", "running": "执行中"}


def _clip(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _attention(root: str) -> List[str]:
    from src.workflow.tasks.attention import attention_items
    return [f"- {_clip(item.get('title'), 80)}（{item.get('kind')}，id {item.get('target_id') or item.get('id')}）"
            for item in attention_items(root, limit=_PER_SECTION)]


def _dispatches(root: str) -> List[str]:
    from src.workflow.tasks.assistant_dispatch import list_dispatches
    lines = []
    for item in list_dispatches(root):
        status = _DISPATCH_LIVE.get(str(item.get("status") or ""))
        if status:
            lines.append(f"- 任务单「{_clip(item.get('title'), 40)}」：{status}，项目 {_clip(item.get('project'), 30)}，"
                         f"更新于 {_clip(item.get('updated_at') or item.get('created_at'), 16)}（id {item.get('id')}）")
    return lines[:_PER_SECTION]


def _goals(root: str) -> List[str]:
    from src.workflow.workbench.personal_goals import PersonalGoals
    lines = []
    for goal in PersonalGoals(root).list():
        if goal.get("status") == "achieved":
            continue
        line = f"- 目标「{_clip(goal.get('objective'), 40)}」：{'受阻' if goal.get('status') == 'blocked' else '进行中'}"
        if goal.get("blocker"):
            line += f"，阻塞：{_clip(goal['blocker'], 40)}"
        if goal.get("next_action"):
            line += f"，下一步：{_clip(goal['next_action'], 40)}"
        if goal.get("due_at"):
            line += f"，截止 {_clip(goal['due_at'], 10)}"
        lines.append(line + f"（id {goal.get('id')}）")
    return lines[:_PER_SECTION]


def _codex(root: str, now: Optional[datetime]) -> List[str]:
    from src.scheduling.codex_watch import thread_states
    view = thread_states(root, now=now)
    if not view["received"]:
        return []
    lines = [f"- Codex {thread['label']}：{thread['status']}{'，已交给助理' if thread['handed_over'] else ''}"
             f"（id {thread['id']}）" for thread in view["threads"]]
    if view["stale"] and lines:
        lines.insert(0, "- （Codex 的进度超过 30 分钟没更新，Mac 可能合盖或 Desktop 没开）")
    return lines[:_PER_SECTION]


_TEAM_STATUS = {"active": "进行中", "waiting": "等你决定", "review": "待你验收"}


def _teams(root: str) -> List[str]:
    from src.workflow.tasks.team import LIVE, TeamService
    service = TeamService(root)
    lines = []
    for task in service.list():
        if task.status not in LIVE:
            continue
        view = service.view(task)
        line = (f"- 任务小组「{_clip(view['objective'], 40)}」：{_TEAM_STATUS[task.status]}，"
                f"已派 {view['used'].get('tasks', 0)} 个子任务、{view['used'].get('rounds', 0)} 轮")
        if task.status == "waiting" and view["waiting_reason"] and not view["tainted"]:
            line += f"，在等：{_clip(view['waiting_reason'], 60)}"
        lines.append(line + f"（id {task.id}）")
    return lines[:_PER_SECTION]


def _schedule_trouble(root: str) -> List[str]:
    from src.scheduling.assistant_watch import schedule_overview
    return [f"- 定时「{_clip(item.get('title'), 40)}」连续失败 {item['failures']} 次（id {item.get('id')}）"
            for item in schedule_overview(root) if int(item.get("failures") or 0) > 0][:_PER_SECTION]


def work_status(root: str, *, now: Optional[datetime] = None) -> str:
    unavailable: List[str] = []

    def read(name: str, loader: Callable[[], List[str]]) -> List[str]:
        try:
            return loader()
        except Exception as error:  # noqa: BLE001 —— 一个来源坏了，其余照样报
            logger.warning("在办汇总读取 %s 失败：%s", name, type(error).__name__)
            unavailable.append(name)
            return []

    waiting = read("等你处理", lambda: _attention(root))
    live = (read("任务小组", lambda: _teams(root)) + read("任务单", lambda: _dispatches(root))
            + read("个人目标", lambda: _goals(root))
            + read("Codex", lambda: _codex(root, now)))
    trouble = read("定时", lambda: _schedule_trouble(root))
    parts = [f"等你处理（{len(waiting)}）：", *(waiting or ["- 没有"]),
             "进行中：", *(live or ["- 没有"])]
    if trouble:
        parts += ["定时异常：", *trouble]
    if unavailable:
        parts.append(f"暂时读不到：{'、'.join(unavailable)}（不代表这些来源没有事）")
    parts.append("每条带 id；要细节用对应工具查（team_open / list_dispatches / personal_goals / codex_status / schedule_list）。")
    return "\n".join(parts)


def build_work_status_tool(root: str):
    from src.agents.tool import Tool

    async def handler(_args: dict) -> str:
        return work_status(root)

    return Tool("work_status",
                "一次看清现在都有哪些事：等用户处理的、进行中的任务单 / 个人目标 / Codex 会话、出问题的定时。"
                "用户问「现在都在忙什么」「进展怎么样」或你要汇报全局时先调它；某一项的细节再用专用工具查。",
                {}, handler, untrusted_source=True, operation_kind='read')
