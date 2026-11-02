"""动态时间线的 Desktop 接口（ADR-012）：常驻助理做过、正在做、等你处理的事按时间排成一条线。
App 经手机网关读同一份（scheduling/timeline.py），两边看到的完全一样。"""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/api/assistant/timeline")
async def assistant_timeline(limit: int = 60):
    from src.scheduling import timeline
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=400, detail="limit 需要 1–200")
    return await asyncio.to_thread(timeline.build, os.getcwd(), limit=limit)


@router.post("/api/assistant/goal-advance/resume")
async def resume_goal_advance(body: dict):
    """被自动审核暂停的目标现在就恢复自动推进。"""
    from src.web import owner_ops
    status, data = await asyncio.to_thread(owner_ops.resume_goal, os.getcwd(), body)
    if status >= 400:
        raise HTTPException(status, data["detail"])
    return data
