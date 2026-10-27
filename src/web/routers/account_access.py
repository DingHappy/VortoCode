"""Trusted native/BFF account bridge. These are NOT public phone login routes.

The phone receives a short, runtime-only credential after OAuth is connected.
The provider's broad access token never becomes the runtime's general API key.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.gateway.account_registration import AccountOwnerMismatch, AccountRegistration
from src.gateway.account_service import (
    AccountIssuerMismatch,
    AccountRejected,
    AccountUnavailable,
    verify_account_access,
    configured_account_origin,
)
from src.gateway.account_access import account_origin
from src.gateway.mobile_devices import MobileStoreError
from src.web.routers.mobile import _admin

router = APIRouter(
    prefix="/api/mobile/account", tags=["account-bridge"], dependencies=[Depends(_admin)]
)


class RevisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)
    instance_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class RegistrationBody(RevisionBody):
    access_token: str = Field(min_length=32, max_length=512)
    issuer: str = Field(min_length=1, max_length=2048)
    name: str = Field(min_length=1, max_length=80)


class ConnectBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    access_token: str = Field(min_length=32, max_length=512)
    issuer: str = Field(min_length=1, max_length=2048)
    registration_revision: int = Field(ge=1)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    device_token: str = Field(pattern=r"^vcm_[A-Za-z0-9_-]{43}$")
    previous_expires_at: int = Field(default=0, ge=0)
    name: str = Field(min_length=1, max_length=80)


async def _identity(access: str, issuer: str):
    try:
        return await verify_account_access(access, issuer)
    except AccountIssuerMismatch:
        raise HTTPException(409, "账号服务与登录来源不一致，请重新核对") from None
    except AccountRejected:
        raise HTTPException(401, "账号登录已失效") from None
    except AccountUnavailable:
        raise HTTPException(503, "账号身份暂时无法核验") from None


async def _operation(method: str, *args):
    try:
        return await asyncio.to_thread(getattr(AccountRegistration(os.getcwd()), method), *args)
    except AccountOwnerMismatch:
        raise HTTPException(403, "账号或原设备授权不匹配") from None
    except (MobileStoreError, OSError):
        raise HTTPException(503, "助手归属存储暂不可用") from None
    except ValueError:
        raise HTTPException(409, "助手归属、设备身份或回执已变化，请重新核对") from None


@router.get("/registration")
async def registration():
    result = await _operation("read")
    try:
        issuer = configured_account_origin()
    except AccountUnavailable:
        raise HTTPException(503, "账号服务配置暂不可用") from None
    public = None
    try:
        public = account_origin(os.getenv("VORTOCODE_MOBILE_PUBLIC_ORIGIN", "")) + "/mobile"
    except ValueError:
        pass
    return {**result, "account_issuer": issuer, "mobile_entry_url": public}


@router.put("/registration")
async def register(body: RegistrationBody):
    session = await _identity(body.access_token, body.issuer)
    return await _operation(
        "register",
        session,
        body.name,
        body.revision,
        body.instance_id if "instance_id" in body.model_fields_set else ...,
    )


@router.delete("/registration")
async def unregister(body: RevisionBody):
    return await _operation(
        "unregister",
        body.revision,
        body.instance_id if "instance_id" in body.model_fields_set else ...,
    )


@router.post("/connect")
async def connect(body: ConnectBody):
    session = await _identity(body.access_token, body.issuer)
    return await _operation(
        "read_lease",
        session,
        body.name,
        body.registration_revision,
        body.request_id,
        body.device_token,
        body.previous_expires_at,
    )
