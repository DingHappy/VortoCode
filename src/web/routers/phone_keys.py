"""手机钥匙申请：Desktop（管理员令牌）批准或拒绝新手机的写入钥匙，移除不再信任的手机。

手机入口在公网上，光凭账号登录不能拿到写入能力（ADR-011 修订）。这些路由不在手机网关上，也不在鉴权豁免名单里。
"""
from __future__ import annotations

import asyncio
import os
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from src.gateway.mobile_devices import MobileDevices, MobileStoreError

router = APIRouter(tags=["phone-keys"])


class KeyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    approve: bool


async def _call(method: str, *args):
    try:
        return await asyncio.to_thread(getattr(MobileDevices(os.getcwd()), method), *args)
    except (MobileStoreError, OSError):
        raise HTTPException(503, "设备记录暂不可用，请稍后重试") from None
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    except KeyError:
        raise HTTPException(404, "没有这份申请，可能已处理或手机已退出") from None


@router.get("/api/assistant/phone-keys")
async def list_phone_keys():
    return await _call("key_requests")


@router.post("/api/assistant/phone-keys/{device_id}")
async def decide_phone_key(device_id: str, body: KeyDecision):
    return await _call("decide_keys", device_id, body.approve)


@router.delete("/api/assistant/phone-keys/trusted/{code}")
async def remove_trusted_phone(code: str):
    if not re.fullmatch(r"[0-9A-F]{4}-[0-9A-F]{4}", code):
        raise HTTPException(404, "没有这台手机")
    return await _call("untrust_keys", code)
