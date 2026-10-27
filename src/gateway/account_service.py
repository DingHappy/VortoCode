"""Server-owned account origin and live verification; credentials are never saved."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import os
import re

import aiohttp

from src.gateway.account_access import (
    AccountIdentity,
    DEFAULT_ACCOUNT_ORIGIN,
    account_origin,
    identity_from_account_service,
)


class AccountRejected(RuntimeError):
    pass


class AccountUnavailable(RuntimeError):
    pass


class AccountIssuerMismatch(AccountUnavailable):
    pass


@dataclass(frozen=True)
class AccountSession:
    identity: AccountIdentity
    device_id: int

    def __post_init__(self):
        if (
            not isinstance(self.identity, AccountIdentity)
            or type(self.device_id) is not int
            or self.device_id < 1
        ):
            raise ValueError("账号设备身份无效")


def configured_account_origin() -> str:
    try:
        return account_origin(
            os.getenv("VORTOCODE_ACCOUNT_URL", "").strip() or DEFAULT_ACCOUNT_ORIGIN
        )
    except ValueError:
        raise AccountUnavailable("账号服务配置不可用") from None


async def verify_account_access(access: str, expected_issuer: str) -> AccountSession:
    """Only called by a trusted server/native bridge, not a public token relay."""
    if not isinstance(access, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", access):
        raise AccountRejected("账号凭证无效")
    origin = configured_account_origin()
    try:
        same_issuer = account_origin(expected_issuer) == origin
    except ValueError:
        same_issuer = False
    if not same_issuer:
        raise AccountIssuerMismatch("账号服务发生变化，请重新核对登录来源")
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5),
            trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar(),
        ) as session:
            async with session.get(
                origin + "/api/me",
                headers={"Authorization": "Bearer " + access, "Accept": "application/json"},
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise AccountRejected("账号登录已失效")
                if response.status != 200 or response.content_type != "application/json":
                    raise AccountUnavailable("账号服务暂不可用")
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(4096):
                    size += len(chunk)
                    if size > 16384:
                        raise AccountUnavailable("账号服务响应无效")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                body = json.loads(raw)
                identity = identity_from_account_service(body, origin)
                device = body.get("device")
                if (
                    not isinstance(device, dict)
                    or type(device.get("id")) is not int
                    or device["id"] < 1
                ):
                    raise ValueError
                return AccountSession(identity, device["id"])
    except (AccountRejected, AccountUnavailable):
        raise
    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        OSError,
        ValueError,
        TypeError,
        RecursionError,
    ):
        raise AccountUnavailable("账号身份暂时无法核验") from None
