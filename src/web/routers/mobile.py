"""Mobile v1 read projections with independent device authorization.

The mock UI never calls these routes. Grants are provisioned on the admin
surface; no approval, chat write, model call or desktop-control route exists.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import ipaddress
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from src.gateway.mobile_devices import MobileDevices, MobileStoreError
from src.gateway.mobile_pairing import MobilePairing, PairingDenied
from src.web.auth import get_api_token, is_authed

router = APIRouter(tags=["mobile"])


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _secure_transport(request: Request) -> None:
    try:
        peer = ipaddress.ip_address(request.client.host if request.client else "")
        loopback_peer = peer.is_loopback or bool(
            getattr(peer, "ipv4_mapped", None) and peer.ipv4_mapped.is_loopback
        )
    except ValueError:
        loopback_peer = False
    if request.url.scheme != "https" and not (
        loopback_peer
        and request.url.hostname
        in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
    ):
        raise HTTPException(403, "设备接口需要 HTTPS", headers={"Cache-Control": "no-store"})


def _admin(request: Request, response: Response) -> None:
    _private(response)
    _secure_transport(request)
    if not get_api_token() or not is_authed(request):
        raise HTTPException(403, "需要已配置的管理员鉴权")
    # A cross-site page must not provision a grant using an existing cookie.
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "设备管理需要同源请求")


async def _store(method: str, *args, pairing: bool = False):
    try:
        service = MobilePairing if pairing else MobileDevices
        return await asyncio.to_thread(getattr(service(os.getcwd()), method), *args)
    except PairingDenied:
        raise HTTPException(401, "配对身份无效", headers={"Cache-Control": "no-store"}) from None
    except (MobileStoreError, OSError):
        raise HTTPException(
            503, "设备授权存储暂不可用", headers={"Cache-Control": "no-store"}
        ) from None
    except ValueError as error:
        raise HTTPException(409, str(error), headers={"Cache-Control": "no-store"}) from None
    except KeyError:
        raise HTTPException(404, "没有该设备授权", headers={"Cache-Control": "no-store"}) from None


class GrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80)
    ttl_days: int = Field(default=30, ge=1, le=90)


class WindowBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool


class PairingStart(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    name: str = Field(min_length=1, max_length=80)
    device_token: str = Field(pattern=r"^vcm_[A-Za-z0-9_-]{43}$")


class PairingPoll(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    device_token: str = Field(pattern=r"^vcm_[A-Za-z0-9_-]{43}$")


class PairingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str = Field(min_length=8, max_length=16)
    approve: bool
    ttl_days: int = Field(default=30, ge=1, le=90)


def _pairing_request(request: Request, response: Response) -> None:
    _private(response)
    _secure_transport(request)
    if not get_api_token():
        raise HTTPException(403, "尚未配置设备接入")
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "配对需要同源请求")


@router.get("/api/mobile/pairings", dependencies=[Depends(_admin)])
async def pairings():
    return await _store("overview", pairing=True)


@router.post("/api/mobile/pairing-window", dependencies=[Depends(_admin)])
async def pairing_window(body: WindowBody):
    return await _store("window", body.enabled, pairing=True)


@router.post("/api/mobile/pairings/{request_id}/decision", dependencies=[Depends(_admin)])
async def pairing_decision(request_id: str, body: PairingDecision):
    return await _store("decide", request_id, body.code, body.approve, body.ttl_days, pairing=True)


@router.post("/api/mobile/pairing/start", dependencies=[Depends(_pairing_request)])
async def start_pairing(body: PairingStart):
    return await _store("start", body.request_id, body.name, body.device_token, pairing=True)


@router.post("/api/mobile/pairing/status", dependencies=[Depends(_pairing_request)])
async def pairing_status(body: PairingPoll):
    return await _store("status", body.request_id, body.device_token, pairing=True)


@router.post("/api/mobile/pairing/cancel", dependencies=[Depends(_pairing_request)])
async def cancel_pairing(body: PairingPoll):
    return await _store("cancel", body.request_id, body.device_token, pairing=True)


@router.post("/api/mobile/devices", dependencies=[Depends(_admin)])
async def issue_device(body: GrantBody):
    """Trusted admin provisioning only; token is returned once, not a QR pairing flow."""
    return await _store("issue", body.name, body.ttl_days)


@router.get("/api/mobile/devices", dependencies=[Depends(_admin)])
async def list_devices():
    return await _store("list")


@router.delete("/api/mobile/devices/{device_id}", dependencies=[Depends(_admin)])
async def revoke_device(device_id: str):
    return await _store("revoke", device_id)


async def _device(request: Request, response: Response) -> dict:
    _private(response)
    _secure_transport(request)
    # No root-token, cookie, query-string or X-API-Token fallback here.
    authorization = request.headers.get("authorization", "")
    grant = await _store(
        "authenticate", authorization[7:] if authorization.startswith("Bearer ") else ""
    )
    if not get_api_token() or grant is None:
        raise HTTPException(401, "设备授权无效或已到期", headers={"Cache-Control": "no-store"})
    return grant


@router.get("/api/mobile/v1/bootstrap")
async def bootstrap(grant: dict = Depends(_device)):
    return {
        "schema_version": 1,
        **grant,
        "capabilities": {"reads": ["profile", "main_chat", "goals"], "writes": []},
    }


def _profile(root: str) -> dict:
    from src.memory.assistant_profile import load_profile

    # Preserve the original profile's default-name semantics, omit persona/SOUL.
    return {"name": load_profile(root)["name"]}


def _main_chat(root: str) -> dict:
    from src.gateway.main_conversation import main_chat_key

    key = main_chat_key(root)
    if key is None and (Path(root) / ".vortocode" / "main_chat.json").exists():
        raise ValueError("unreadable main-chat registration")
    return {"sid": key[4:] if key else None}


def _goals(root: str) -> dict:
    from src.workflow.workbench.personal_goals import PersonalGoals

    items = PersonalGoals(root).list()
    fields = ("id", "objective", "status", "next_action", "blocker", "revision")
    return {
        "items": [{key: item[key] for key in fields} for item in items[:100]],
        "truncated": len(items) > 100,
    }


async def _source(reader, root: str) -> dict:
    try:
        data = await asyncio.to_thread(reader, root)
    except Exception:
        # An unavailable source must not turn into a successful empty result.
        return {"status": "unavailable", "checked_at": None, "data": None}
    return {"status": "current", "checked_at": datetime.now(timezone.utc).isoformat(), "data": data}


@router.get("/api/mobile/v1/snapshot")
async def snapshot(grant: dict = Depends(_device)):
    return await read_mobile_snapshot(os.getcwd(), grant["instance_id"])


async def read_mobile_snapshot(root: str, instance_id: str):
    sources = await asyncio.gather(
        *(_source(reader, root) for reader in (_profile, _main_chat, _goals))
    )
    return {
        "schema_version": 1,
        "instance_id": instance_id,
        "sources": dict(zip(("profile", "main_chat", "goals"), sources)),
    }
