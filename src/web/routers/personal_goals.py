"""Personal goal UI endpoints. Intentionally no task/run/verifier endpoint."""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException

from src.workflow.workbench.personal_goals import PersonalGoals

router = APIRouter()


async def _call(method: str, *args):
    service = PersonalGoals(os.getcwd())
    try:
        return await asyncio.to_thread(getattr(service, method), *args)
    except KeyError:
        raise HTTPException(status_code=404, detail="私人目标不存在或无法读取") from None
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except OSError:
        raise HTTPException(status_code=503, detail="目标存储暂不可用，请刷新确认后重试") from None


@router.get("/api/assistant/goals")
async def list_personal_goals():
    return {"goals": await _call("list")}


@router.post("/api/assistant/goals")
async def create_personal_goal(body: dict):
    return await _call("create", body)


@router.get("/api/assistant/goals/{goal_id}")
async def get_personal_goal(goal_id: str):
    return await _call("get", goal_id)


@router.patch("/api/assistant/goals/{goal_id}")
async def update_personal_goal(goal_id: str, body: dict):
    return await _call("update", goal_id, body)


@router.post("/api/assistant/goals/{goal_id}/criteria/{criterion_id}/evidence")
async def confirm_personal_goal(goal_id: str, criterion_id: str, body: dict):
    return await _call("evidence", goal_id, criterion_id, body)
