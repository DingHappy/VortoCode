"""手机 App 对话的本机转发接口（ADR-012 阶段 A）：只给同机的手机网关用。

手机网关核对完原生会话和设备签名，带专用令牌 ``VORTOCODE_APP_RELAY_TOKEN`` 经回环地址转进来；
本接口不收主 API 令牌，这个令牌也进不了别的接口；只接受回环来源，只在常驻助理（通用范围）开放，
不设令牌 = 端点不存在（404）。

都用固定路径 + POST（id 放在请求体里），这样鉴权豁免只放精确路径，不放前缀。
"""
from __future__ import annotations

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
PATHS = (TURN_PATH, STATUS_PATH, CARD_PATH, CHAT_PATH, OP_PATH)

_turns = None


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
                                 trust_level=lambda: get_trust_level(root), with_dev=False)

        async def notify(text: str):
            from src.gateway.mobile_push import push_owner
            await push_owner(root, text)

        _turns = AppTurns(root, build, notify=notify)
    return _turns


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "不是合法的 JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "需要 JSON 对象")
    return body


@router.post(TURN_PATH)
async def start_turn(request: Request):
    _guard(request)
    from src.gateway.app_turns import AppTurnError
    body = await _body(request)
    try:
        return turns().start(body.get("request_id"), body.get("text")).public()
    except AppTurnError as error:
        raise HTTPException(409, str(error)) from None


@router.post(STATUS_PATH)
async def turn_status(request: Request):
    _guard(request)
    body = await _body(request)
    turn = turns().get(body.get("rid"))
    if turn is None:
        raise HTTPException(404, "没有这一轮（可能助理重启过，请重新发送）")
    return turn.public()


@router.post(CARD_PATH)
async def decide_card(request: Request):
    _guard(request)
    from src.gateway.app_turns import AppTurnError
    body = await _body(request)
    approve, key_kind = body.get("approve"), body.get("key_kind")
    if not isinstance(approve, bool) or key_kind not in ("device", "approval"):
        raise HTTPException(400, "需要 {rid, card_id, approve, digest, key_kind}")
    try:
        return turns().decide(str(body.get("rid")), str(body.get("card_id")), approve,
                              str(body.get("digest")), key_kind)
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
    if not isinstance(limit, int) or not 1 <= limit <= 200:
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


@router.post(OP_PATH)
async def owner_op(request: Request):
    """手机上的写操作（随手记、撤销、批 Codex 动作、下发 / 撤回任务单）：签名已由手机网关核对。"""
    _guard(request)
    body = await _body(request)
    from src.web import owner_ops
    status, data = await owner_ops.run(os.getcwd(), str(body.get("op") or ""), body.get("args"))
    if status >= 400:
        raise HTTPException(status, str(data.get("detail") or "请求没有被接受"))
    return data
