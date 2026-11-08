"""手机 App 对话的本机转发接口（ADR-012 阶段 A）：只给同机的手机网关用。

手机网关核对完原生会话和设备签名，带专用令牌 ``VORTOCODE_APP_RELAY_TOKEN`` 经回环地址转进来；
本接口不收主 API 令牌，这个令牌也进不了别的接口；只接受回环来源，只在常驻助理（通用范围）开放，
不设令牌 = 端点不存在（404）。

都用固定路径 + POST（id 放在请求体里），这样鉴权豁免只放精确路径，不放前缀。
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

TOKEN_ENV = "VORTOCODE_APP_RELAY_TOKEN"
TURN_PATH = "/api/assistant/app-relay/turn"
STATUS_PATH = "/api/assistant/app-relay/status"
CARD_PATH = "/api/assistant/app-relay/card"
CHAT_PATH = "/api/assistant/app-relay/chat"
OP_PATH = "/api/assistant/app-relay/op"
TIMELINE_PATH = "/api/assistant/app-relay/timeline"
ACCESS_PATH = "/api/assistant/app-relay/access"
PATHS = (TURN_PATH, STATUS_PATH, CARD_PATH, CHAT_PATH, OP_PATH, TIMELINE_PATH, ACCESS_PATH)

_turns = None


def pending_card_refs(root: str) -> set[str]:
    """Resident-process evidence only; reading never constructs an Agent or restores a wait."""
    from pathlib import Path
    return (_turns.pending_card_refs() if _turns is not None and Path(_turns.root).resolve() == Path(root).resolve() else set())


def _guard(request: Request) -> None:
    from src.gateway.workspace_scope import GENERAL, current_workspace_scope
    from src.web.auth import ct_eq
    token = os.getenv(TOKEN_ENV, "").strip()
    header = request.headers.get("authorization", "")
    try:
        loopback = ipaddress.ip_address(request.client.host if request.client else "").is_loopback
    except ValueError:
        loopback = False
    if (not token or not loopback or current_workspace_scope() != GENERAL
            or not (header.startswith("Bearer ") and ct_eq(header[7:].strip(), token))):
        raise HTTPException(404, "Not Found")


def turns():
    """本进程唯一的 App 回合管理器：owner_device 档案、用户在 Desktop 选的授权档位、推送提醒。"""
    global _turns
    if _turns is None:
        from src.gateway.agent_session import build_session
        from src.gateway.app_turns import AppTurns
        from src.gateway.trust_setting import get_trust_level

        root = os.getcwd()

        def build(confirm):
            return build_session(root, kind="web", confirm=confirm, can_ask_human=True,
                                 capability_profile="owner_device", workspace_scope="general",
                                 trust_level=lambda: get_trust_level(root), with_dev=False,
                                 on_decision=manager.audit_decision, enable_turn_scope=True,
                                 on_tool_event=lambda stage, payload: manager.tool_event(stage, payload))

        async def notify(text: str):
            from src.gateway.mobile_push import push_owner
            await push_owner(root, text)

        manager = AppTurns(root, build, notify=notify)
        _turns = manager
    return _turns


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "不是合法的 JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "需要 JSON 对象")
    return body


def _opened(body: dict, expected: set):
    """拆开转发来的签名请求并再验一遍；操作由签进去的路径决定，不在预期里就拒。"""
    from src.gateway.relay_envelope import EnvelopeRejected, open_envelope
    try:
        op, data, _device, key_kind = open_envelope(os.getcwd(), body.get("envelope"))
    except EnvelopeRejected as error:
        raise HTTPException(401, str(error)) from None
    if op not in expected:
        raise HTTPException(401, "这个请求不能在这里执行")
    return op, data, key_kind


@router.post(TURN_PATH)
async def start_turn(request: Request):
    _guard(request)
    from src.gateway.app_turns import AppTurnError, AppRequestNeedsReview
    _op, data, _kind = _opened(await _body(request), {"chat_send"})
    try:
        return (await turns().start(data.get("request_id"), data.get("text"))).public()
    except AppRequestNeedsReview as error:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(error), "code": "app_request_review_required"}, status_code=409)
    except AppTurnError as error:
        raise HTTPException(409, str(error)) from None


@router.post(STATUS_PATH)
async def turn_status(request: Request):
    _guard(request)
    body = await _body(request)
    turn = turns().get(body.get("rid"))
    if turn is None:
        import re
        rid = body.get("rid")
        if not isinstance(rid, str) or not re.fullmatch(r"app-[0-9a-f]{16}", rid):
            raise HTTPException(404, "没有这一轮")
        # The manager may have restarted or evicted this turn. Never infer that earlier actions did not run.
        return {"rid": rid, "request_id": "", "state": "failed", "outcome": "unknown", "reply": "", "cards": [],
                "error": "这一轮的进度已无法核对，助理可能重启过或记录已清理。旧审批卡不再有效；请先查看主聊天和审批记录，核对之前的动作，再决定是否发起新请求。"}
    return turn.public()


@router.post(CARD_PATH)
async def decide_card(request: Request):
    _guard(request)
    from src.gateway.app_turns import AppTurnError
    _op, data, key_kind = _opened(await _body(request), {"chat_card"})
    if not isinstance(data.get("approve"), bool):
        raise HTTPException(400, "需要 {rid, card_id, approve, digest, key_kind}")
    try:
        return turns().decide(str(data.get("rid")), str(data.get("card_id")), data["approve"],
                              str(data.get("digest")), key_kind, scope=data.get("scope", "once"))
    except KeyError:
        raise HTTPException(404, "没有这张卡") from None
    except AppTurnError as error:
        raise HTTPException(409, str(error)) from None


@router.post(CHAT_PATH)
async def recent_chat(request: Request):
    """主要聊天最近的对话（各入口的都有，标来源），给 App 的对话页。只给正文，不给工具细节。"""
    _guard(request)
    body = await _body(request)
    limit = body.get("limit", 50)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise HTTPException(400, "limit 需要 1–200")
    from src.gateway.main_conversation import main_chat_key
    from src.gateway.session_store import load_session

    root = os.getcwd()
    key: Optional[str] = main_chat_key(root)
    data = load_session(root, key) if key else None
    items = [{field: item.get(field) for field in ("role", "text", "rid", "source", "at")}
             for item in (data or {}).get("transcript") or []
             if isinstance(item, dict) and item.get("role") in ("user", "assistant")]
    return {"registered": key is not None, "items": items[-limit:]}


_OP_ARGS = {"capture": ("text", "request_id"), "undo": ("op_id",),
            "decide_codex": ("action_id", "approve", "action_digest"), "dispatch": ("dispatch_id", "approve", "digest"),
            "resume_goal": ("goal_id",)}


@router.post(OP_PATH)
async def owner_op(request: Request):
    """手机上的写操作（随手记、撤销、批 Codex 动作、下发 / 撤回任务单）。网关转来的是手机签过名的原始请求，
    这里再验一遍签名；做哪个操作由签进去的路径决定，参数只取签名请求体里的。"""
    _guard(request)
    op, data, _kind = _opened(await _body(request), set(_OP_ARGS))
    from src.web import owner_ops
    status, result = await owner_ops.run(os.getcwd(), op, {key: data.get(key) for key in _OP_ARGS[op]})
    if status >= 400:
        raise HTTPException(status, str(result.get("detail") or "请求没有被接受"))
    return result


@router.post(TIMELINE_PATH)
async def relay_timeline(request: Request):
    """动态时间线，给 App（只读；网关沙箱里读不了要加锁的目标存储，所以经这里读）。"""
    _guard(request)
    body = await _body(request)
    limit = body.get("limit", 60)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise HTTPException(400, "limit 需要 1–200")
    from src.scheduling import timeline
    return await asyncio.to_thread(timeline.build, os.getcwd(), limit=limit)


@router.post(ACCESS_PATH)
async def relay_access(request: Request):
    _guard(request)
    from src.gateway.owner_access import snapshot
    root = os.getcwd()
    return await asyncio.to_thread(snapshot, root, live_cards=pending_card_refs(root))
