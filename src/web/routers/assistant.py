"""常驻助理的资料与记忆：Desktop 身份卡显示/修改名字与「关于你」，「记忆」页查看与删除。"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from src.memory import assistant_profile

router = APIRouter()


@router.get("/api/assistant/profile")
async def get_profile():
    return assistant_profile.load_profile(os.getcwd())


@router.put("/api/assistant/profile")
async def put_profile(body: dict):
    body = body or {}
    name = body.get("name")
    about = body.get("about_you")
    if name is not None and not str(name).strip():
        raise HTTPException(status_code=400, detail="名字不能为空")
    return assistant_profile.save_profile(os.getcwd(), name=None if name is None else str(name),
                                          about_you=None if about is None else str(about))


@router.get("/api/assistant/memories")
async def get_memories():
    return {"memories": assistant_profile.list_memories(os.getcwd())}


@router.delete("/api/assistant/memories/{memory_id}")
async def remove_memory(memory_id: str):
    if not assistant_profile.delete_memory(os.getcwd(), memory_id):
        raise HTTPException(status_code=404, detail="没有这条记忆")
    return {"ok": True}
