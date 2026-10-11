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
    soul = body.get("soul")
    if name is not None and not str(name).strip():
        raise HTTPException(status_code=400, detail="名字不能为空")
    return assistant_profile.save_profile(os.getcwd(), name=None if name is None else str(name),
                                          about_you=None if about is None else str(about),
                                          soul=None if soul is None else str(soul))


@router.get("/api/assistant/memories")
async def get_memories():
    return {"memories": assistant_profile.list_memories(os.getcwd())}


@router.delete("/api/assistant/memories/{memory_id}")
async def remove_memory(memory_id: str):
    if not assistant_profile.delete_memory(os.getcwd(), memory_id):
        raise HTTPException(status_code=404, detail="没有这条记忆")
    return {"ok": True}


@router.get("/api/assistant/schedule")
async def get_schedule():
    """助理名下的定时事项：早晚报、Heartbeat、定时作业。"""
    from src.scheduling.assistant_watch import schedule_overview
    return {"items": schedule_overview(os.getcwd())}


_IM_PREFIXES = ("telegram-", "dingtalk-")


@router.get("/api/assistant/phone-chats")
async def phone_chats(limit: int = 200):
    """手机上（Telegram / 钉钉）和助理的聊天记录，**只读**：Desktop 只看不接管，
    回复留在手机上——两个进程同时写一段对话会互相覆盖历史。"""
    from src.gateway.session_store import list_sessions, load_session

    root, chats = os.getcwd(), []
    for summary in list_sessions(root):
        sid = str(summary.get("sid") or "")
        if not sid.startswith(_IM_PREFIXES):
            continue
        data = load_session(root, f"sid-{sid}") or {}
        transcript = [{key: item.get(key) for key in ("role", "text", "rid", "at")}
                      for item in data.get("transcript") or [] if isinstance(item, dict)]
        chats.append({"sid": sid, "title": summary.get("title") or sid, "updated": summary.get("updated"),
                      "transcript": transcript[-max(1, min(int(limit), 400)):]})
    return {"chats": chats}


@router.get("/api/assistant/memory-proposals")
async def memory_proposals():
    """聊天里提炼出的「关于你」候选，等你确认。"""
    return {"proposals": assistant_profile.list_memory_proposals(os.getcwd())}


@router.post("/api/assistant/memory-proposals/{proposal_id}")
async def decide_memory_proposal(proposal_id: str, body: dict):
    action = str((body or {}).get("action") or "")
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action 只能是 approve 或 reject")
    result = assistant_profile.review_memory_proposal(os.getcwd(), proposal_id, action == "approve")
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=str(result.get("error") or "无法处理这条候选"))
    return result
