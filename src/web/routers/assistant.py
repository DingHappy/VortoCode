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


@router.get("/api/assistant/avatar")
async def get_avatar():
    """助理头像（base64）；没设过返回 avatar=null，界面用名字首字。"""
    return {"avatar": assistant_profile.load_avatar(os.getcwd())}


@router.put("/api/assistant/avatar")
async def put_avatar(body: dict):
    """换头像：{"data": <base64>}。按文件头认 PNG/JPEG/WebP，最大 512KB。"""
    import base64
    import binascii
    raw = (body or {}).get("data")
    if not isinstance(raw, str) or len(raw) > assistant_profile.MAX_AVATAR_BYTES * 4 // 3 + 8:
        raise HTTPException(status_code=400, detail="需要 base64 图片数据（最大 512KB）")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=400, detail="图片数据不是有效的 base64") from error
    try:
        return {"avatar": assistant_profile.save_avatar(os.getcwd(), data)}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.delete("/api/assistant/avatar")
async def remove_avatar():
    return {"removed": assistant_profile.delete_avatar(os.getcwd())}


@router.get("/api/assistant/main-chat")
async def get_main_chat():
    """主要聊天是哪一段（手机、电脑共用）；还没登记返回 sid=null。"""
    from src.gateway.main_conversation import main_chat_key
    key = main_chat_key(os.getcwd())
    return {"sid": key[len("sid-"):] if key else None}


@router.put("/api/assistant/main-chat")
async def claim_main_chat(body: dict):
    """登记主要聊天（Desktop 第一次连上时把自己已有的那段报上来）。已登记过就返回已登记的那段。"""
    from src.gateway.main_conversation import MainChatUnavailable, claim_main_chat as claim
    sid = str((body or {}).get("sid") or "")
    try:
        key = claim(os.getcwd(), f"sid-{sid}")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except MainChatUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"sid": key[len("sid-"):]}


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


@router.get("/api/assistant/connectors")
async def connectors_status():
    """本机凭据可用性与设备授权状态；不代表 MCP 已连接，不含令牌。"""
    from src.workflow.workbench import connectors
    return {"connectors": connectors.status(os.getcwd())}


@router.post("/api/assistant/connectors/{name}/authorize")
async def connector_authorize(name: str):
    """发起设备授权：返回验证码与 github.com/login/device 链接，常驻电脑在后台等你点授权。"""
    from src.workflow.workbench import connectors
    try:
        return await connectors.start_authorization(os.getcwd(), name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"发起授权失败：{error}") from error


@router.delete("/api/assistant/connectors/{name}")
async def connector_disconnect(name: str):
    from src.workflow.workbench import connectors
    try:
        return {"removed": connectors.disconnect(os.getcwd(), name)}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
