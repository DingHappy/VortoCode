"""常驻助理的资料与记忆：Desktop 身份卡显示/修改名字与「关于你」，「记忆」页查看与删除。"""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, HTTPException

from src.memory import assistant_profile

router = APIRouter()


def _personal_skills():
    from src.gateway.workspace_scope import GENERAL, current_workspace_scope
    from src.workflow.workbench.personal_skills import PersonalSkills
    if current_workspace_scope() != GENERAL:
        raise HTTPException(403, "个人技能仅在私人助理范围管理")
    return PersonalSkills(os.getcwd())


def _skill_error(error):
    from src.workflow.workbench.personal_skills import SkillConflict
    if isinstance(error, FileNotFoundError):
        return HTTPException(404, "个人技能不存在")
    if isinstance(error, SkillConflict):
        return HTTPException(409, "技能正在更新或版本已变化，请刷新并重新审阅")
    return HTTPException(400, "个人技能无法处理，请检查格式、长度、版本和存储权限")


@router.get("/api/assistant/skills")
async def personal_skills_list():
    skills = _personal_skills()
    try:
        return skills.list()
    except Exception as error:
        raise _skill_error(error) from None


@router.get("/api/assistant/skills/{skill_id}")
async def personal_skill_preview(skill_id: str):
    skills = _personal_skills()
    try:
        return skills.get(skill_id, preview=True)
    except Exception as error:
        raise _skill_error(error) from None


@router.put("/api/assistant/skills/{skill_id}")
async def personal_skill_save(skill_id: str, body: dict):
    skills = _personal_skills()
    try:
        return skills.save(skill_id, body)
    except Exception as error:
        raise _skill_error(error) from None


@router.put("/api/assistant/skills/{skill_id}/enabled")
async def personal_skill_enabled(skill_id: str, body: dict):
    skills = _personal_skills()
    try:
        if set(body) != {"enabled", "revision"}:
            raise ValueError("需要明确的状态和版本")
        return skills.set_enabled(skill_id, enabled=body["enabled"], revision=body["revision"])
    except Exception as error:
        raise _skill_error(error) from None


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
    replace = (body or {}).get("replace") or []
    if not isinstance(replace, list) or len(replace) > 5:
        raise HTTPException(status_code=400, detail="replace 必须是最多 5 个记忆 id 的列表")
    result = assistant_profile.review_memory_proposal(os.getcwd(), proposal_id, action == "approve",
                                                      replace=[str(item) for item in replace])
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=str(result.get("error") or "无法处理这条候选"))
    return result


@router.get("/api/assistant/connectors")
async def connectors_status():
    """本机凭据可用性与设备授权状态；不代表 MCP 已连接，不含令牌。"""
    from src.workflow.workbench import connectors
    return {"connectors": connectors.status(os.getcwd())}


@router.get("/api/assistant/plugins")
async def plugins_status():
    """助理插件（日历、Notion…）的本机凭据状态；不含凭据。"""
    from src.plugins import plugin_status
    return {"plugins": await asyncio.to_thread(plugin_status)}


@router.post("/api/assistant/plugins/{name}/check")
async def plugin_check(name: str):
    """真去读一次，告诉你插件能不能用。"""
    from src.plugins import check_plugin
    try:
        return await asyncio.wait_for(check_plugin(name), 45)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except asyncio.TimeoutError:
        return {"ok": False, "status": "available", "detail": "检查超时，对方太慢或网络不通"}


@router.put("/api/assistant/plugins/{name}")
async def plugin_save(name: str, body: dict):
    """从 Desktop 填插件凭据：写进本机凭据文件后真去读一次。返回里不带凭据。"""
    from src.plugins import check_plugin, save_credential
    credential = body.get("credential") if isinstance(body, dict) else None
    if not isinstance(credential, str):
        raise HTTPException(status_code=400, detail="缺少凭据")
    try:
        await asyncio.to_thread(save_credential, name, credential)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    try:
        check = await asyncio.wait_for(check_plugin(name), 45)
    except asyncio.TimeoutError:
        check = {"ok": False, "status": "available", "detail": "已保存；检查超时，对方太慢或网络不通"}
    return {"saved": True, "check": check}


@router.delete("/api/assistant/plugins/{name}")
async def plugin_remove(name: str):
    from src.plugins import remove_credential
    try:
        return {"removed": await asyncio.to_thread(remove_credential, name)}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/api/assistant/connectors/{name}/authorize")
async def connector_authorize(name: str):
    """发起设备授权：返回验证码与 github.com/login/device 链接，常驻电脑在后台等你点授权。"""
    from src.workflow.workbench import connectors
    try:
        return await connectors.start_authorization(os.getcwd(), name)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except connectors.AuthorizationSuperseded:
        raise HTTPException(status_code=409, detail="授权已取消或被替换，请核对当前状态。") from None
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=502, detail="发起授权失败，请检查服务配置后重试。") from None


@router.delete("/api/assistant/connectors/{name}")
async def connector_disconnect(name: str):
    from src.workflow.workbench import connectors
    from src.agents.mcp_session import detach_connector, finish_connector_detach
    from src.web.routers.realtime import _SESSIONS
    detached = []
    def disabled(revocation):
        seen = set()
        for record in list(_SESSIONS.values()):
            agent = record.get('agent') if isinstance(record, dict) else None
            if agent is None or id(agent) in seen:
                continue
            seen.add(id(agent))
            revision = detach_connector(agent, revocation)
            if revision is not None:
                detached.append((agent, revision))
    def cleaned(status):
        for agent, revision in detached:
            finish_connector_detach(agent, revision, status)
    try:
        result = await connectors.disconnect_with_connections(os.getcwd(), name, on_revoke=disabled, on_cleanup=cleaned)
        return {**result, "sessions_updated": len(detached)}
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
