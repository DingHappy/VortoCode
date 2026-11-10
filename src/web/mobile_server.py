"""Non-executing mobile gateway sharing a resident assistant's existing state.

This process has no Agent lifespan, scheduler, IM bridge, tools or project APIs.
Run exactly one worker on loopback behind the mobile-only HTTPS allowlist.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from time import monotonic

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.gateway.account_access import account_origin
from src.web.auth import auth_middleware, get_api_token
from src.web.mobile_rate_limit import MobileRateLimiter


def create_app() -> FastAPI:
    if not get_api_token():
        raise RuntimeError("手机网关需要独立的管理员身份")
    account_origin(os.getenv("VORTOONE_MOBILE_PUBLIC_ORIGIN", ""))
    root = os.getenv("VORTOONE_MOBILE_STATE_ROOT", "").strip()
    if not root or not Path(root).is_absolute() or Path(root).resolve() != Path.cwd().resolve():
        raise RuntimeError("手机网关工作目录必须匹配已登记的助手状态目录")
    from src.utils.state_dir import state_dir
    state_dir(root)              # 状态目录改名（ADR-018 批次 C）；只读挂载时搬不动，继续用旧目录
    release = os.getenv("VORTOONE_MOBILE_RELEASE", "dev")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,95}", release):
        raise RuntimeError("手机网关发布标识无效")

    app = FastAPI(title="VortoOne Mobile", docs_url=None, redoc_url=None, openapi_url=None)
    app.middleware("http")(auth_middleware)
    limiter = MobileRateLimiter(clock=monotonic)

    @app.middleware("http")
    async def limit_source(request, call_next):
        # 只使用传输层确认的来源；转发头的信任仍由服务器配置负责。
        source = request.client.host if request.client else "unknown"
        retry_after = limiter.admit(source)
        if retry_after:
            return JSONResponse({"detail": "请求繁忙，请稍后重试"}, status_code=429,
                                headers={"Retry-After": str(retry_after),
                                         "Cache-Control": "no-store", "Pragma": "no-cache"})
        response = await call_next(request)
        if response.status_code == 401:
            limiter.authentication_failed(source)
        return response

    from src.web.routers.mobile import router as mobile
    from src.web.routers.mobile_pages import router as pages
    from src.web.routers.account_access import router as account
    from src.web.routers.mobile_browser import router as browser
    from src.web.routers.mobile_native import router as native
    from src.web.routers.mobile_owner import router as owner

    for router in (mobile, pages, account, browser, native, owner):
        app.include_router(router)

    @app.get("/api/health/quick")
    def health():
        return {"status": "healthy", "service": "mobile-gateway", "release": release,
                "execution_runtime": False}

    return app
