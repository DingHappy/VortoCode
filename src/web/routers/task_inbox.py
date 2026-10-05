"""Manual handling of completed tasks, using the shared owner-scoped inbox."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from src.gateway.dispatch import session_identity
from src.gateway.handoffs import CompletionInbox, HandoffConflict
from src.web import task_events

router = APIRouter(prefix="/api/task-inbox", tags=["Task handoff"])


class Acknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session: str = Field(min_length=1, max_length=124)
    revision: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    note: str = Field(min_length=1, max_length=2000)


def _inbox(session: str) -> CompletionInbox:
    root = os.getcwd()

    def on_update(task):
        from src.gateway.worktree_sessions import task_session_view
        # A receipt update is not another delivery/completion notification.
        task_events.broadcast_task_update(task_session_view(root, task))

    return CompletionInbox(root, session_identity(session), on_update=on_update)


def _http_error(error: ValueError | OSError) -> HTTPException:
    status = 409 if isinstance(error, HandoffConflict) else 503 if isinstance(error, OSError) else 400
    detail = "任务已更新或不属于此会话，请刷新任务后重试" if isinstance(error, HandoffConflict) else str(error)
    return HTTPException(status_code=status, detail=detail)


@router.get("")
async def pending(session: str = Query(min_length=1, max_length=124), limit: int = Query(default=20, ge=1, le=20)):
    try:
        return {"tasks": _inbox(session).pending(limit)}
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.post("/{task_id}/acknowledge")
async def acknowledge(task_id: str, body: Acknowledgement):
    if not body.note.strip():
        raise HTTPException(status_code=400, detail="请填写具体处理说明")
    try:
        return _inbox(body.session).acknowledge(task_id, body.revision, body.note)
    except (ValueError, OSError) as error:
        raise _http_error(error) from error
