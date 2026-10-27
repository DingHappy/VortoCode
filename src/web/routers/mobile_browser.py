"""Same-origin browser account login and read BFF; independent HttpOnly cookies."""

from __future__ import annotations

import os
import html
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse

from src.gateway.account_service import AccountRejected, AccountUnavailable
from src.gateway.mobile_devices import MobileStoreError
from src.gateway.mobile_browser import (
    BrowserCapacity,
    BrowserDenied,
    BrowserNotReady,
    MobileBrowserSessions,
    PENDING_TTL,
    SESSION_TTL,
    browser_config,
)
from src.web.routers.mobile import read_mobile_snapshot

router = APIRouter(tags=["mobile-browser"])
_PRIVATE = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def native_return(values: dict):
    # A loaded document ends the original account form's CSP redirect chain.
    destination = html.escape("vortocode-mobile-auth://callback?" + urlencode(values), quote=True)
    return HTMLResponse(
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0;url={destination}">'
        f'<title>返回 VortoCode</title><body><a href="{destination}">返回 VortoCode App</a></body></html>',
        headers={
            **_PRIVATE,
            "Content-Security-Policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        },
    )


def manager(request: Request) -> MobileBrowserSessions:
    if not hasattr(request.app.state, "mobile_browser"):
        request.app.state.mobile_browser = MobileBrowserSessions()
    return request.app.state.mobile_browser


def config(request: Request, *, post=False):
    value = browser_config(os.getcwd())
    guard_origin(request, value, post=post)
    return value


def guard_origin(request: Request, value, *, post=False):
    if str(request.base_url).rstrip("/") != value.origin:
        raise BrowserNotReady("访问地址与手机入口配置不一致")
    origin = request.headers.get("origin")
    if (
        (post and origin != value.origin)
        or (origin and origin != value.origin)
        or request.headers.get("sec-fetch-site") == "cross-site"
    ):
        raise BrowserDenied("请从手机入口发起此操作")


def failure(error, response=None):
    if isinstance(error, (BrowserDenied, AccountRejected)):
        code, message = 401, "登录或设备授权已失效，请重新登录"
    elif isinstance(error, BrowserCapacity):
        code, message = 429, "登录请求繁忙，请稍后重试"
    else:
        code, message = 503, "账号连接暂不可用，请稍后重试"
    return JSONResponse({"detail": message}, status_code=code, headers=_PRIVATE)


@router.get("/api/mobile/browser/status")
async def status(request: Request):
    try:
        current = config(request)
        sessions = manager(request)
        context_secret = sessions.context(current, request.cookies.get(current.context_cookie, ""))
        session = sessions.find(current.root, request.cookies)
        signed_in = False
        if session:
            try:
                manager(request)._check(session)
                signed_in = True
            except BrowserDenied:
                pass
        data = {
            "schema_version": 1,
            "ready": True,
            "signed_in": signed_in,
            "account_origin": current.issuer,
            "discovery_scope": "current_runtime",
        }
        if signed_in:
            data.update(instance_id=current.instance_id, name=current.name)
        response = JSONResponse(data, headers=_PRIVATE)
        response.set_cookie(
            current.context_cookie,
            context_secret,
            max_age=SESSION_TTL,
            httponly=True,
            secure=current.origin.startswith("https:"),
            samesite="lax",
            path="/api/mobile",
        )
        if not signed_in:
            response.delete_cookie(current.session_cookie, path="/api/mobile")
        return response
    except BrowserNotReady as error:
        return JSONResponse(
            {"schema_version": 1, "ready": False, "signed_in": False, "message": str(error)},
            headers=_PRIVATE,
        )
    except (BrowserDenied, AccountUnavailable, MobileStoreError) as error:
        return failure(error)


@router.post("/api/mobile/account/login")
async def login(request: Request):
    try:
        current = config(request, post=True)
        sessions = manager(request)
        old = sessions.find(current.root, request.cookies)
        url, proof = sessions.begin(
            current,
            request.cookies.get(current.context_cookie, ""),
            request.cookies.get(current.login_cookie, ""),
        )
        if old:
            await sessions.logout(old, invalidate=False)
        response = JSONResponse({"authorize_url": url}, headers=_PRIVATE)
        response.set_cookie(
            current.login_cookie,
            proof,
            max_age=PENDING_TTL,
            httponly=True,
            secure=current.origin.startswith("https:"),
            samesite="lax",
            path="/api/mobile/account",
        )
        response.delete_cookie(current.session_cookie, path="/api/mobile")
        return response
    except (BrowserNotReady, BrowserDenied, AccountUnavailable, MobileStoreError) as error:
        return failure(error)


@router.get("/api/mobile/account/callback")
async def callback(request: Request):
    try:
        # OAuth navigation comes from the account origin; only callback state/cookie binds it.
        current = browser_config(os.getcwd())
        if str(request.base_url).rstrip("/") != current.origin:
            raise BrowserDenied()
        if len(request.query_params.getlist("state")) != 1:
            raise BrowserDenied()
        pending = manager(request).claim(
            current, request.query_params["state"], request.cookies.get(current.login_cookie, "")
        )
    except (BrowserDenied, BrowserNotReady, MobileStoreError):
        return JSONResponse(
            {"detail": "登录回调无效，请从手机入口重新登录"}, status_code=400, headers=_PRIVATE
        )
    try:
        if request.query_params.get("error"):
            raise BrowserDenied()
        if len(request.query_params.getlist("code")) != 1:
            raise BrowserDenied()
        secret = await manager(request).complete(pending, request.query_params["code"])
        if pending.native_challenge:
            ticket = manager(request).native_ticket(pending, secret)
            response = native_return({"ticket": ticket, "state": pending.native_state})
            response.delete_cookie(current.login_cookie, path="/api/mobile/account")
            return response
        response = RedirectResponse("/mobile", status_code=303, headers=_PRIVATE)
        response.set_cookie(
            current.session_cookie,
            secret,
            max_age=SESSION_TTL,
            httponly=True,
            secure=current.origin.startswith("https:"),
            samesite="lax",
            path="/api/mobile",
        )
    except (BrowserDenied, AccountRejected, AccountUnavailable, MobileStoreError, ValueError):
        destination = (
            (
                "vortocode-mobile-auth://callback?"
                + urlencode({"error": "login_failed", "state": pending.native_state})
            )
            if pending.native_challenge
            else "/mobile?login=failed"
        )
        response = (
            native_return({"error": "login_failed", "state": pending.native_state})
            if pending.native_challenge
            else RedirectResponse(destination, status_code=303, headers=_PRIVATE)
        )
    response.delete_cookie(current.login_cookie, path="/api/mobile/account")
    return response


async def _read(request: Request, *, snapshot=False):
    session = None
    try:
        sessions = manager(request)
        session = sessions.find(os.getcwd(), request.cookies)
        if not session:
            raise BrowserDenied()
        sessions._check(session)
        current = config(request)
        grant = await sessions.grant(session)
        if snapshot:
            result = await read_mobile_snapshot(current.root, grant["instance_id"])
        else:
            result = {
                "schema_version": 1,
                "instance_id": grant["instance_id"],
                "device": grant["device"],
                "capabilities": {"reads": ["profile", "main_chat", "goals"], "writes": []},
            }
        sessions._check(session)
        return JSONResponse(result, headers=_PRIVATE)
    except (
        BrowserDenied,
        BrowserNotReady,
        AccountRejected,
        AccountUnavailable,
        MobileStoreError,
        ValueError,
    ) as error:
        response = failure(error)
        if session and not session.active:
            response.delete_cookie(session.config.session_cookie, path="/api/mobile")
        return response


@router.get("/api/mobile/browser/bootstrap")
async def bootstrap(request: Request):
    return await _read(request)


@router.get("/api/mobile/browser/snapshot")
async def snapshot(request: Request):
    return await _read(request, snapshot=True)


@router.post("/api/mobile/browser/logout")
async def logout(request: Request):
    try:
        sessions = manager(request)
        session = sessions.find(os.getcwd(), request.cookies)
        current = session.config if session else config(request, post=True)
        guard_origin(request, current, post=True)
        sessions.invalidate_context(current.root, request.cookies.get(current.context_cookie, ""))
        if session:
            await sessions.logout(session)
        proof = request.cookies.get(current.login_cookie, "")
        for key, pending in list(sessions.pending.items()):
            if pending.config.root == current.root:
                try:
                    # claim uses the state hash; invalidating this browser's proof cannot affect others.
                    from src.gateway.mobile_browser import _digest

                    if proof and pending.proof_hash == _digest(proof):
                        sessions.pending.pop(key, None)
                except ValueError:
                    pass
        response = JSONResponse({"ok": True}, headers=_PRIVATE)
        response.delete_cookie(current.session_cookie, path="/api/mobile")
        response.delete_cookie(current.login_cookie, path="/api/mobile/account")
        return response
    except (BrowserNotReady, BrowserDenied, AccountUnavailable, MobileStoreError) as error:
        return failure(error)
