"""First-party mobile OAuth HTTP calls; no browser credentials or token logging."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import re

import aiohttp

from src.gateway.account_service import (
    AccountIssuerMismatch,
    AccountRejected,
    AccountUnavailable,
    configured_account_origin,
)


@dataclass(frozen=True)
class OAuthTokens:
    access: str = field(repr=False)
    refresh: str = field(repr=False)
    expires_in: int


async def token_request(issuer: str, form: dict[str, str]) -> OAuthTokens:
    if configured_account_origin() != issuer:
        raise AccountIssuerMismatch("账号服务已变化")
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5),
            trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar(),
        ) as client:
            async with client.post(
                issuer + "/oauth/token", data=form, allow_redirects=False
            ) as response:
                if response.status in (400, 401, 403):
                    raise AccountRejected("登录授权无效")
                if response.status != 200 or response.content_type != "application/json":
                    raise AccountUnavailable("账号服务暂不可用")
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(4096):
                    size += len(chunk)
                    if size > 16384:
                        raise ValueError
                    chunks.append(chunk)
                body = json.loads(b"".join(chunks))
                access, refresh, expires = (
                    body.get("access_token"),
                    body.get("refresh_token"),
                    body.get("expires_in"),
                )
                if (
                    not isinstance(access, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", access)
                    or not isinstance(refresh, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", refresh)
                    or type(expires) is not int
                    or not 60 <= expires <= 3600
                ):
                    raise ValueError
                return OAuthTokens(access, refresh, expires)
    except (AccountRejected, AccountUnavailable):
        raise
    except (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        ValueError,
        TypeError,
        AttributeError,
        RecursionError,
    ):
        raise AccountUnavailable("登录交换没有取得有效回执") from None


async def logout_provider(issuer: str, access: str) -> None:
    if configured_account_origin() != issuer:
        return
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5),
            trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar(),
        ) as client:
            async with client.post(
                issuer + "/api/logout",
                headers={"Authorization": "Bearer " + access},
                allow_redirects=False,
            ):
                pass
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        pass  # Local session and runtime authorization are already revoked.
