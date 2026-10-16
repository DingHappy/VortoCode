"""Fixed Telegram failure categories and bounded, credential-free diagnostics."""
from __future__ import annotations

import asyncio
import math
from typing import Any

POLL_ERROR_CATEGORIES = frozenset({"none", "timeout", "transport_error", "invalid_response", "rate_limited",
                                   "authorization", "conflict", "server_error", "api_error", "unexpected_error"})


def error_category(code: int | None) -> str:
    if code == 429:
        return "rate_limited"
    if code in {401, 403}:
        return "authorization"
    if code == 409:
        return "conflict"
    return "server_error" if code is not None and code >= 500 else "api_error"


class TelegramError(Exception):
    def __init__(self, message: str, *, code: int | None = None, category: str | None = None,
                 retry_after: int | None = None):
        super().__init__(message)
        self.code = code if type(code) is int and 100 <= code <= 599 else None
        self.category = category if category in POLL_ERROR_CATEGORIES else error_category(self.code)
        self.retry_after = (retry_after if self.code == 429 and type(retry_after) is int
                            and 0 < retry_after <= 2**31 - 1 else None)


def request_error(error: Exception, status: int | None = None) -> TelegramError:
    """Keep a classification, never the exception message or credential-bearing URL."""
    import aiohttp

    if status is not None and status >= 400:
        return TelegramError("Bot API 请求失败", code=status)
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        category = "timeout"
    elif isinstance(error, (aiohttp.ContentTypeError, ValueError, UnicodeError)):
        category = "invalid_response"
    elif isinstance(error, aiohttp.ClientError):
        category = "transport_error"
    else:
        category = "unexpected_error"
    return TelegramError(f"Bot API 请求失败（{category}）", category=category)


def safe_poll_diagnostics(data: object) -> dict:
    """Whitelist the same scalar contract when a sampler reads an IM endpoint."""
    if not isinstance(data, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("failures", "consecutive_failures", "malformed_updates"):
        value = data.get(key)
        if type(value) is int and 0 <= value <= 2**63 - 1:
            result[key] = value
    for key in ("last_failure_age", "last_recovery_age", "retry_in"):
        value = data.get(key)
        if value is None and key in data:
            result[key] = None
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 2**63 - 1 and math.isfinite(value):
            result[key] = value
    category = data.get("last_error_category")
    if isinstance(category, str) and category in POLL_ERROR_CATEGORIES:
        result["last_error_category"] = category
    code = data.get("last_error_code")
    if type(code) is int and 100 <= code <= 599:
        result["last_error_code"] = code
    return result
