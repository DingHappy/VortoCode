"""手机 App 作为主人终端的写入（ADR-011 / ADR-009 修订）：绑定钥匙、随手记、等你决定、撤销。

都是确定性的文件操作，不运行模型——手机网关进程本来就没有 Agent、调度和 IM。默认关闭：
只有设置 ``VORTOCODE_MOBILE_WRITES=1`` 才开放，部署上去不等于开放写入；ADR-009 修订要求先完成
WireGuard 直连（不开放公网 HTTP 端口）再开启。

身份三层：原生会话（Bearer）证明是哪台设备；每个写请求再带这台设备的签名（见 gateway/mobile_signing）；
批准动作只认需要生物识别才能用的那把「批准钥匙」，并且签进去的动作摘要必须和服务端当前的动作一致。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.gateway.account_service import AccountRejected, AccountUnavailable
from src.gateway.mobile_browser import BrowserDenied, BrowserNotReady
from src.gateway.mobile_devices import MobileDevices, MobileStoreError
from src.gateway import mobile_signing as signing
from src.web.routers.mobile_browser import _PRIVATE, failure
from src.web.routers.mobile_native import native_session

router = APIRouter(tags=["mobile-owner"])
_AUTH_ERRORS = (BrowserDenied, BrowserNotReady, AccountUnavailable, AccountRejected, MobileStoreError)


def writes_enabled() -> bool:
    return os.getenv("VORTOCODE_MOBILE_WRITES", "").strip() == "1"


def _reply(data: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers=_PRIVATE)


async def _device(request: Request) -> dict:
    sessions, session = native_session(request)
    grant = await sessions.grant(session)
    sessions._check(session)
    return grant


async def _signed(request: Request, grant: dict, kind: str) -> dict:
    """核对这次写请求的签名并消耗 nonce，返回请求体。任何一项不对都是 401，不说明是哪一项。"""
    root, device_id = os.getcwd(), grant["device"]["id"]
    store = MobileDevices(root)
    key = store.signing_key(device_id, kind)
    if not key:
        raise signing.SignatureRejected("这台设备还没绑定钥匙")
    body = await request.body()
    if len(body) > 16 * 1024:
        raise signing.SignatureRejected("请求过大")
    timestamp, nonce = request.headers.get("x-vc-timestamp"), request.headers.get("x-vc-nonce")
    signing.check_freshness(timestamp, nonce, time.time())
    signing.verify(key, signing.message(method=request.method, path=request.url.path, body=body,
                                        timestamp=str(timestamp), nonce=str(nonce), device_id=device_id),
                   request.headers.get("x-vc-signature"))
    try:
        store.consume_nonce(device_id, str(nonce))
    except ValueError as error:
        raise signing.SignatureRejected("重复的请求") from error
    try:
        data = json.loads(body or b"{}")
    except ValueError as error:
        raise signing.SignatureRejected("请求体无效") from error
    if not isinstance(data, dict):
        raise signing.SignatureRejected("请求体无效")
    return data


async def _guarded(request: Request, handler):
    if not writes_enabled():
        return _reply({"detail": "这台助理没有开启手机写入"}, 403)
    try:
        return await handler(await _device(request))
    except signing.SignatureRejected:
        return _reply({"detail": "请求签名无效或已过期，请在 App 里重试"}, 401)
    except _AUTH_ERRORS as error:
        return failure(error)


async def _envelope(request: Request, grant: dict, key_kind: str) -> dict:
    """这次已核对过签名的请求，原样打包给常驻助理再验一遍（见 gateway/relay_envelope.py）。"""
    from src.gateway.relay_envelope import build
    return build(method=request.method, path=request.url.path, body=await request.body(), headers=request.headers,
                 device_id=grant["device"]["id"], key_kind=key_kind)


async def _execute(request: Request, grant: dict, key_kind: str, op: str, args: dict):
    """写操作交给常驻助理执行（网关的沙箱写不了目标、撤销记录、Codex 动作和任务单，见 web/owner_ops.py）。
    转发的是手机签过名的原始请求，助理自己再验签、按签进去的路径决定操作；没配转发（本机开发、测试）
    就地执行同一份代码。"""
    from src.gateway.app_relay_client import RelayUnavailable, configured, relay
    from src.web import owner_ops

    if not configured():
        status, data = await owner_ops.run(os.getcwd(), op, args)
        return _reply(data, status)
    try:
        status, data = await relay("/api/assistant/app-relay/op",
                                   {"envelope": await _envelope(request, grant, key_kind)})
    except RelayUnavailable as error:
        return _reply({"detail": str(error)}, 503)
    return _reply(data if status < 400 else {"detail": str(data.get("detail") or "请求没有被接受")[:200]}, status)


@router.post("/api/mobile/native/keys")
async def bind_keys(request: Request):
    """第一次绑定两把公钥（只凭原生会话；之后所有写入都要签名）。已绑定的设备不能换钥匙。"""
    async def handle(grant):
        try:
            body = json.loads(await request.body() or b"{}")
            device_key = signing.normalize_public_key(body.get("device_key"))
            approval_key = signing.normalize_public_key(body.get("approval_key"))
            receipt = await asyncio.to_thread(MobileDevices(os.getcwd()).bind_keys, grant["device"]["id"],
                                              device_key, approval_key)
        except (ValueError, AttributeError) as error:
            return _reply({"detail": str(error)[:200] or "钥匙无效"}, 409)
        except KeyError:
            return _reply({"detail": "设备授权无效或已到期"}, 401)
        return _reply({"ok": True, **receipt})
    return await _guarded(request, handle)


def _decisions(root: str) -> list[dict]:
    from src.scheduling import codex_watch

    return [{"id": item["id"], "kind": item.get("kind"), "thread": codex_watch.thread_label(
                {"name": item.get("thread_name"), "cwd": item.get("thread_cwd"), "id": item.get("thread_id")}),
             "text": item.get("text") or "", "tainted": bool(item.get("tainted")),
             "created_at": item.get("created_at"), "action_digest": signing.action_digest(item)}
            for item in codex_watch.load_actions(root) if item.get("status") == "proposed"]


@router.get("/api/mobile/native/decisions")
async def decisions(request: Request):
    async def handle(grant):
        return _reply({"decisions": await asyncio.to_thread(_decisions, os.getcwd())})
    return await _guarded(request, handle)


@router.post("/api/mobile/native/decide")
async def decide(request: Request):
    """批准用批准钥匙（要过生物识别），拒绝用日常钥匙。签进去的摘要必须是这条动作此刻的样子。"""
    async def handle(grant):
        try:
            peek = json.loads(await request.body() or b"{}")
        except ValueError:
            return _reply({"detail": "请求无效"}, 400)
        approve = isinstance(peek, dict) and peek.get("approve") is True
        kind = "approval" if approve else "device"
        body = await _signed(request, grant, kind)
        return await _execute(request, grant, kind, "decide_codex",
                              {key: body.get(key) for key in ("action_id", "approve", "action_digest")})
    return await _guarded(request, handle)


@router.post("/api/mobile/native/capture")
async def capture(request: Request):
    """随手记：直接记成一个个人目标（来源是主人），不运行模型；之后可以在助理里再整理，也能撤销。"""
    async def handle(grant):
        body = await _signed(request, grant, "device")
        return await _execute(request, grant, "device", "capture",
                              {"text": body.get("text"), "request_id": body.get("request_id")})
    return await _guarded(request, handle)


@router.get("/api/mobile/native/changes")
async def changes(request: Request):
    async def handle(grant):
        from src.agents import undo_log
        return _reply({"ops": await asyncio.to_thread(undo_log.recent, os.getcwd(), limit=20)})
    return await _guarded(request, handle)


@router.post("/api/mobile/native/undo")
async def undo(request: Request):
    async def handle(grant):
        body = await _signed(request, grant, "device")
        return await _execute(request, grant, "device", "undo", {"op_id": body.get("op_id")})
    return await _guarded(request, handle)


@router.post("/api/mobile/native/push")
async def register_push(request: Request):
    """登记 / 刷新推送：APNs 设备令牌和解密推送正文用的公钥（Secure Enclave 里另一把钥匙）。App 每次启动都调。"""
    async def handle(grant):
        body = await _signed(request, grant, "device")
        from src.gateway.mobile_push import normalize_registration
        try:
            registration = normalize_registration(body.get("apns_token"), body.get("push_key"), body.get("environment"))
            await asyncio.to_thread(MobileDevices(os.getcwd()).register_push, grant["device"]["id"], registration)
        except ValueError as error:
            return _reply({"detail": str(error)[:200]}, 400)
        except KeyError:
            return _reply({"detail": "设备授权无效或已到期"}, 401)
        return _reply({"ok": True})
    return await _guarded(request, handle)


# —— 对话（ADR-012 阶段 A）：手机是主要聊天的又一个入口。网关只核对会话与签名，再经本机专用接口
# 转给常驻助理跑回合（owner_device 档案）；回合中途要确认的动作以待批准卡出现，App 签名批准。
# 对话正文比概览敏感：读也要求这台设备的钥匙已被信任。

def _trusted(grant) -> bool:
    return bool(MobileDevices(os.getcwd()).signing_key(grant["device"]["id"], "device"))


async def _relayed(path: str, body: dict):
    from src.gateway.app_relay_client import RelayUnavailable, relay
    try:
        status, data = await relay(path, body)
    except RelayUnavailable as error:
        return _reply({"detail": str(error)}, 503)
    if status == 404 and not data.get("detail"):
        return _reply({"detail": "这台助理还没开通手机对话"}, 503)
    return _reply(data if status < 400 else {"detail": str(data.get("detail") or "请求没有被接受")[:200]}, status)


@router.post("/api/mobile/native/chat/send")
async def chat_send(request: Request):
    async def handle(grant):
        await _signed(request, grant, "device")
        return await _relayed("/api/assistant/app-relay/turn", {"envelope": await _envelope(request, grant, "device")})
    return await _guarded(request, handle)


@router.get("/api/mobile/native/chat/turn")
async def chat_turn(request: Request):
    async def handle(grant):
        if not _trusted(grant):
            return _reply({"detail": "这台手机还没获得对话权限"}, 403)
        rid = request.query_params.get("rid", "")
        if not re.fullmatch(r"app-[0-9a-f]{16}", rid):
            return _reply({"detail": "没有这一轮"}, 404)
        return await _relayed("/api/assistant/app-relay/status", {"rid": rid})
    return await _guarded(request, handle)


@router.get("/api/mobile/native/chat")
async def chat_recent(request: Request):
    async def handle(grant):
        if not _trusted(grant):
            return _reply({"detail": "这台手机还没获得对话权限"}, 403)
        return await _relayed("/api/assistant/app-relay/chat", {"limit": 50})
    return await _guarded(request, handle)


@router.post("/api/mobile/native/chat/card")
async def chat_card(request: Request):
    """批准 / 拒绝回合里的一张卡。用哪把钥匙签写在请求体里（也被签进去）；够不够级别由常驻助理按卡判定。"""
    async def handle(grant):
        try:
            declared = json.loads(await request.body() or b"{}").get("key_kind")
        except (ValueError, AttributeError):
            declared = None
        if declared not in ("device", "approval"):
            return _reply({"detail": "需要 {rid, card_id, approve, digest, key_kind}"}, 400)
        await _signed(request, grant, declared)
        return await _relayed("/api/assistant/app-relay/card", {"envelope": await _envelope(request, grant, declared)})
    return await _guarded(request, handle)


# —— 任务单（ADR-012）：助理在对话里拟好的任务单，手机上核对后下发（Face ID）或撤回 ——

@router.get("/api/mobile/native/dispatches")
async def dispatches(request: Request):
    async def handle(grant):
        if not _trusted(grant):
            return _reply({"detail": "这台手机还没获得写入权限"}, 403)
        from src.web import owner_ops
        status, data = await asyncio.to_thread(owner_ops.dispatches, os.getcwd())
        return _reply(data, status)
    return await _guarded(request, handle)


@router.post("/api/mobile/native/dispatch")
async def dispatch(request: Request):
    """下发用批准钥匙（Face ID），撤回用日常钥匙；下发签进去的摘要必须对上任务单此刻的标题和目标。"""
    async def handle(grant):
        try:
            peek = json.loads(await request.body() or b"{}")
        except ValueError:
            return _reply({"detail": "请求无效"}, 400)
        approve = isinstance(peek, dict) and peek.get("approve") is True
        kind = "approval" if approve else "device"
        body = await _signed(request, grant, kind)
        return await _execute(request, grant, kind, "dispatch",
                              {key: body.get(key) for key in ("dispatch_id", "approve", "digest")})
    return await _guarded(request, handle)
