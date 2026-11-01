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
        body = await _signed(request, grant, "approval" if approve else "device")
        action_id, digest = body.get("action_id"), body.get("action_digest")
        if not isinstance(action_id, str) or not re.fullmatch(r"cdx-[0-9a-f]{10}", action_id) or type(body.get("approve")) is not bool:
            return _reply({"detail": "请求无效"}, 400)
        from src.scheduling import codex_watch

        def run():
            current = next((item for item in codex_watch.load_actions(os.getcwd()) if item.get("id") == action_id), None)
            if current is None:
                raise KeyError(action_id)
            if body["approve"] and signing.action_digest(current) != digest:
                raise codex_watch.CodexActionError("这条动作已经变了，请刷新后重新核对")
            return codex_watch.decide_action(os.getcwd(), action_id, body["approve"])
        try:
            action = await asyncio.to_thread(run)
        except KeyError:
            return _reply({"detail": "没有这条动作"}, 404)
        except codex_watch.CodexActionError as error:
            return _reply({"detail": str(error)[:200]}, 409)
        return _reply({"id": action["id"], "status": action["status"],
                       "status_text": codex_watch.ACTION_STATUS_TEXT.get(action["status"], action["status"])})
    return await _guarded(request, handle)


@router.post("/api/mobile/native/capture")
async def capture(request: Request):
    """随手记：直接记成一个个人目标（来源是主人），不运行模型；之后可以在助理里再整理，也能撤销。"""
    async def handle(grant):
        body = await _signed(request, grant, "device")
        text, request_id = body.get("text"), body.get("request_id")
        if not isinstance(text, str) or not text.strip() or len(text) > 500 or not isinstance(request_id, str) \
                or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id):
            return _reply({"detail": "内容需要 1–500 字"}, 400)
        import src.agents.tools.personal_goals  # noqa: F401 —— 导入即登记目标改动的撤销函数
        from src.agents import undo_log
        from src.workflow.workbench.personal_goals import PersonalGoals

        def run():
            root, text_ = os.getcwd(), text.strip()
            service = PersonalGoals(root)
            # 网络重试会带同一个 request_id：目标只建一次（create 本身幂等），撤销也只记一次
            existed = service.ledger._path(f"personal-m-{request_id}").exists()
            goal = service.create({"request_id": f"m-{request_id}", "objective": text_,
                                   "acceptance_criteria": [text_], "next_action": "待整理（手机随手记）"},
                                  origin="owner")
            if not existed:
                undo_log.record(root, action="personal_goal.create", summary=f"手机随手记「{goal['objective'][:60]}」",
                                payload={"goal_id": goal["id"], "revision": goal["revision"]}, source="mobile")
            return goal
        try:
            goal = await asyncio.to_thread(run)
        except (OSError, ValueError) as error:
            return _reply({"detail": str(error)[:200]}, 409)
        return _reply({"goal_id": goal["id"], "objective": goal["objective"]})
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
        op_id = body.get("op_id")
        if not isinstance(op_id, str) or not re.fullmatch(r"op-[0-9a-f]{12}", op_id):
            return _reply({"detail": "没有这条操作"}, 404)
        import src.agents.tools.personal_goals  # noqa: F401 —— 导入即登记目标改动的撤销函数
        from src.agents import undo_log
        try:
            result = await asyncio.to_thread(undo_log.undo, os.getcwd(), op_id)
        except undo_log.UndoError as error:
            return _reply({"detail": str(error)}, 409)
        return _reply({"ok": result.ok, "detail": result.detail, "not_recallable": list(result.not_recallable)})
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
