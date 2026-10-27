"""Native login handoff. Account tokens remain in the resident service."""

from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from src.gateway.mobile_browser import BrowserDenied, BrowserNotReady, SESSION_TTL, PENDING_TTL
from src.gateway.account_service import AccountUnavailable, AccountRejected
from src.gateway.mobile_devices import MobileStoreError
from src.web.routers.mobile_browser import config, manager, failure, _PRIVATE
from src.web.routers.mobile import read_mobile_snapshot

router = APIRouter(tags=["mobile-native"])


@router.get("/api/mobile/native/authorize")
async def authorize(request: Request):
    try:
        current = config(request)
        challenge, state = (
            request.query_params.get("challenge", ""),
            request.query_params.get("state", ""),
        )
        if (
            any(len(request.query_params.getlist(key)) != 1 for key in ("challenge", "state"))
            or not re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge)
            or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", state)
        ):
            raise BrowserDenied()
        sessions = manager(request)
        context = sessions.context(current)  # Isolated from any pre-existing web/native login.
        url, proof = sessions.begin(current, context)
        oauth_state = parse_qs(urlsplit(url).query)["state"][0]
        from src.gateway.mobile_browser import _digest

        pending = sessions.pending[_digest(oauth_state)]
        pending.native_challenge, pending.native_state = challenge, state
        response = RedirectResponse(url, status_code=303, headers=_PRIVATE)
        secure = current.origin.startswith("https:")
        response.set_cookie(
            current.context_cookie,
            context,
            httponly=True,
            secure=secure,
            samesite="lax",
            max_age=SESSION_TTL,
            path="/api/mobile",
        )
        response.set_cookie(
            current.login_cookie,
            proof,
            httponly=True,
            secure=secure,
            samesite="lax",
            max_age=PENDING_TTL,
            path="/api/mobile/account",
        )
        return response
    except (
        BrowserDenied,
        BrowserNotReady,
        AccountUnavailable,
        AccountRejected,
        MobileStoreError,
    ) as error:
        return failure(error)


class Exchange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ticket: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    verifier: str = Field(pattern=r"^[A-Za-z0-9_-]{43,128}$")
    state: str = Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")


@router.post("/api/mobile/native/exchange")
async def exchange(request: Request, body: Exchange):
    try:
        value = manager(request).redeem_native(body.ticket, body.verifier, body.state)
        current = config(request)
        if value["instance_id"] != current.instance_id:
            raise BrowserDenied()
        return JSONResponse(value, headers=_PRIVATE)
    except (
        BrowserDenied,
        BrowserNotReady,
        AccountUnavailable,
        AccountRejected,
        MobileStoreError,
    ) as error:
        return failure(error)


async def native_read(request: Request, *, snapshot=False, logout=False):
    try:
        sessions = manager(request)
        auth = request.headers.get("authorization", "")
        session = sessions.find_native(auth[7:] if auth.startswith("Bearer ") else "")
        if session.config.root != os.getcwd():
            raise BrowserDenied()
        # Captured origin permits logout even after an owner unbind.
        from src.web.routers.mobile_browser import guard_origin

        guard_origin(request, session.config)
        if logout:
            await sessions.logout(session)
            return JSONResponse({"ok": True}, headers=_PRIVATE)
        current = config(request)
        grant = await sessions.grant(session)
        result = (
            await read_mobile_snapshot(current.root, grant["instance_id"])
            if snapshot
            else {
                "schema_version": 1,
                "instance_id": grant["instance_id"],
                "device": grant["device"],
                "capabilities": {"reads": ["profile", "main_chat", "goals"], "writes": []},
            }
        )
        sessions._check(session)
        return JSONResponse(result, headers=_PRIVATE)
    except (
        BrowserDenied,
        BrowserNotReady,
        AccountUnavailable,
        AccountRejected,
        MobileStoreError,
        ValueError,
    ) as error:
        return failure(error)


@router.get("/api/mobile/native/bootstrap")
async def bootstrap(request: Request):
    return await native_read(request)


@router.get("/api/mobile/native/snapshot")
async def snapshot(request: Request):
    return await native_read(request, snapshot=True)


@router.post("/api/mobile/native/logout")
async def logout(request: Request):
    return await native_read(request, logout=True)
