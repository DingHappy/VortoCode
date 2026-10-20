"""运维心跳报到：家里其它机器的定时活（备份同步、恢复演练）向常驻助理报平安或报失败；
另有 /api/ops/notice 转一条要告诉主人的事（账号服务的新注册、付款），限每天条数。

专用令牌 ``VORTOCODE_OPS_TOKEN``，不收主 API 令牌，这个令牌也进不了别的接口；不设令牌 = 端点不存在（404）。
只在常驻助理（通用范围）开放。失败与「失败后恢复」立刻推到手机；「该报没报」由常驻助理的巡检发现。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

PATH = "/api/ops/heartbeat"
NOTICE_PATH = "/api/ops/notice"
TOKEN_ENV = "VORTOCODE_OPS_TOKEN"


def _guard(request: Request) -> None:
    from src.gateway.workspace_scope import GENERAL, current_workspace_scope
    from src.web.auth import ct_eq
    token = os.getenv(TOKEN_ENV, "").strip()
    header = request.headers.get("authorization", "")
    if (not token or current_workspace_scope() != GENERAL
            or not (header.startswith("Bearer ") and ct_eq(header[7:].strip(), token))):
        raise HTTPException(404, "Not Found")


@router.post(PATH)
async def ops_heartbeat(request: Request):
    _guard(request)
    from src.scheduling.ops_heartbeats import HeartbeatError, record
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "不是合法的 JSON") from None
    if not isinstance(body, dict) or not isinstance(body.get("ok"), bool):
        raise HTTPException(400, "需要 {source, ok, text?, expect_hours?, event?}")
    root = os.getcwd()
    try:
        message = record(root, str(body.get("source") or ""), ok=body["ok"], text=str(body.get("text") or ""),
                         expect_hours=body.get("expect_hours", 26), event=body.get("event") is True)
    except HeartbeatError as error:
        raise HTTPException(400, str(error)) from None
    if message:
        from src.scheduling.notices import make_notifier
        await make_notifier(root, source=f"ops:{body.get('source')}")(message)
    return {"ok": True, "notified": bool(message)}


@router.post(NOTICE_PATH)
async def ops_notice(request: Request):
    """家里机器转来的一条要告诉主人的事（如账号服务的新注册、付款）；同一令牌，限每天条数。"""
    _guard(request)
    from src.scheduling.ops_heartbeats import HeartbeatError, notice_text
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "不是合法的 JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "需要 {source, text}")
    try:
        message = notice_text(str(body.get("source") or ""), str(body.get("text") or ""))
    except HeartbeatError as error:
        raise HTTPException(429 if "太多" in str(error) else 400, str(error)) from None
    from src.scheduling.notices import make_notifier
    notified = await make_notifier(os.getcwd(), source=f"ops:{body.get('source')}")(message)
    return {"ok": True, "notified": bool(notified)}
