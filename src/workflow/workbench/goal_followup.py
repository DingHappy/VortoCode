"""Personal reminder transactions, invoked by the existing assistant watcher.

The goal lock spans the durable claim, notification I/O and receipt save. A
leftover sending claim is uncertain, never an instruction to replay the send.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from src.workflow.workbench.personal_goals import PersonalGoals, _date

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
MAX_PER_PASS = 5
NOTIFY_TIMEOUT = 10


def _candidates(service: PersonalGoals) -> list[str]:
    directory = service.ledger._dir()
    if directory.is_symlink() or directory.parent.is_symlink():
        raise OSError("目标存储位置不可用")
    rows = []
    for path in directory.glob("personal-*.json"):
        try:
            goal = service._load(path.stem)
            state = goal.follow_up
            if state.get("status") in {"scheduled", "sending"}:
                due = _date(state.get("due_at", ""))
                if not due:
                    raise ValueError("提醒时间缺失")
                rows.append((due, goal.id))
        except (KeyError, TypeError, ValueError, OSError):
            logger.warning("个人目标提醒记录不可读，跳过该项")
    return [goal_id for _, goal_id in sorted(rows)]


async def poll_goal_followups(root: str, now: datetime, *, notify: Callable[[str], Awaitable[bool]]) -> None:
    service = PersonalGoals(root)
    now = now.astimezone(timezone.utc)
    handled = 0
    for goal_id in _candidates(service):
        if handled >= MAX_PER_PASS:
            break
        try:
            with service._lock(goal_id):
                goal = service._load(goal_id)
                state = goal.follow_up
                if state.get("status") == "sending":
                    state.update(status="unknown", reason="此前发送中断，需核对通知结果")
                elif state.get("status") == "scheduled":
                    if goal.status == "achieved" or state.get("due_at") != goal.follow_up_at:
                        state.update(status="cancelled", reason="目标或跟进时间已变化")
                    else:
                        due = datetime.fromisoformat(_date(state["due_at"]))
                        retry_at = state.get("next_attempt_at")
                        if due > now or (retry_at and datetime.fromisoformat(_date(retry_at)) > now):
                            continue
                        attempts = state.get("attempts", 0)
                        text = state.get("text")
                        if type(attempts) is not int or not 0 <= attempts < MAX_ATTEMPTS or not isinstance(text, str) or not 1 <= len(text) <= 2000:
                            raise ValueError("提醒合同无效")
                        state.update(status="sending", attempts=attempts + 1, attempt_id=uuid.uuid4().hex,
                                     attempted_at=now.isoformat(timespec="seconds"))
                        goal.revision += 1
                        service._save(goal)  # Never notify before the claim is durable.
                        handled += 1
                        try:
                            # 到点时间是主人批准的：投递器支持就标成定时提醒，不被安静时段攒到早上
                            sent = notify(text, timed=True) if getattr(notify, "accepts_timed", False) else notify(text)
                            accepted = await asyncio.wait_for(sent, timeout=NOTIFY_TIMEOUT)
                        except Exception:  # Cancellation propagates, preserving the sending claim.
                            state.update(status="unknown", reason="通知结果不明，请核对后再决定是否重试")
                        else:
                            if type(accepted) is not bool:
                                state.update(status="unknown", reason="通知回执格式无效，需人工核对")
                            elif accepted is True:
                                state.update(status="accepted", accepted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                             reason="通知发送或补发队列已受理，不代表已送达或已读")
                            elif state["attempts"] >= MAX_ATTEMPTS:
                                state.update(status="failed", reason="通知连续三次未受理，需要人工重试")
                            else:
                                retry = now + timedelta(seconds=60 * 2 ** (state["attempts"] - 1))
                                state.update(status="scheduled", next_attempt_at=retry.isoformat(timespec="seconds"),
                                             reason="通知未受理，等待重试")
                else:
                    continue
                goal.revision += 1
                service._save(goal)
        except (KeyError, ValueError, TypeError, OSError) as error:
            logger.warning("个人目标提醒未处理：%s", type(error).__name__)
