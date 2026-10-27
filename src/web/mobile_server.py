"""Non-executing mobile gateway sharing a resident assistant's existing state.

This process has no Agent lifespan, scheduler, IM bridge, tools or project APIs.
Run exactly one worker on loopback behind the mobile-only HTTPS allowlist.
"""

from __future__ import annotations

import os
from pathlib import Path
import re

from fastapi import FastAPI

from src.gateway.account_access import account_origin
from src.web.auth import auth_middleware, get_api_token


def create_app() -> FastAPI:
    if not get_api_token():
        raise RuntimeError("手机网关需要独立的管理员身份")
    account_origin(os.getenv("VORTOCODE_MOBILE_PUBLIC_ORIGIN", ""))
    root = os.getenv("VORTOCODE_MOBILE_STATE_ROOT", "").strip()
    if not root or not Path(root).is_absolute() or Path(root).resolve() != Path.cwd().resolve():
        raise RuntimeError("手机网关工作目录必须匹配已登记的助手状态目录")
    release = os.getenv("VORTOCODE_MOBILE_RELEASE", "dev")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,95}", release):
        raise RuntimeError("手机网关发布标识无效")

    app = FastAPI(title="VortoCode Mobile", docs_url=None, redoc_url=None, openapi_url=None)
    app.middleware("http")(auth_middleware)

    from src.web.routers.mobile import router as mobile
    from src.web.routers.mobile_pages import router as pages
    from src.web.routers.account_access import router as account
    from src.web.routers.mobile_browser import router as browser
    from src.web.routers.mobile_native import router as native

    for router in (mobile, pages, account, browser, native):
        app.include_router(router)

    @app.get("/api/health/quick")
    def health():
        return {"status": "healthy", "service": "mobile-gateway", "release": release,
                "execution_runtime": False}

    return app
