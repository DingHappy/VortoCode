"""Web 层鉴权与执行开关（安全默认值）。

设计原则：本地开发不被打扰，但默认不裸奔危险能力。
- 鉴权：仅当设置了环境变量 AUTODEV_API_TOKEN 时强制校验
  （Bearer 或 X-API-Token）；未设则放行（配合默认仅绑 127.0.0.1）。
  这样既不破坏本地 UI，又能在对外暴露时一键加固。
- 危险的宿主机命令执行端点默认禁用，需显式 AUTODEV_ENABLE_SHELL=1。
"""

import os
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import JSONResponse


def resolve_within(base, rel) -> Optional[Path]:
    """把 rel 安全解析到 base 目录内；越界（绝对路径 / ..）返回 None。

    用 Path.relative_to 做组件级判断，根治 startswith 字符串前缀绕过
    （如 /x/proj 误判 /x/proj-secrets 在内）。
    """
    base_p = Path(base).resolve()
    target = (base_p / str(rel)).resolve()
    try:
        target.relative_to(base_p)
    except ValueError:
        return None
    return target


def get_api_token() -> str:
    return os.getenv("AUTODEV_API_TOKEN", "").strip()


def shell_enabled() -> bool:
    return os.getenv("AUTODEV_ENABLE_SHELL", "").strip().lower() in ("1", "true", "yes", "on")


# 鉴权豁免：页面、API 文档、健康检查
_EXEMPT_PREFIXES = ("/docs", "/redoc", "/openapi.json", "/static")
_EXEMPT_EXACT = {"/", "/workspace", "/workstation", "/classic",
                 "/api/health", "/api/health/quick"}


def _token_ok(request) -> bool:
    token = get_api_token()
    if not token:
        return True  # 未配置 token：本地开发放行
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and auth[7:].strip() == token:
        return True
    return request.headers.get("x-api-token", "").strip() == token


async def auth_middleware(request, call_next):
    """仅当配置了 AUTODEV_API_TOKEN 时，对非豁免路径强制校验。"""
    path = request.url.path
    exempt = path in _EXEMPT_EXACT or path.startswith(_EXEMPT_PREFIXES)
    if not exempt and not _token_ok(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


def require_shell() -> None:
    """危险的宿主机执行入口调用此函数；未显式开启则拒绝（fail-closed）。"""
    if not shell_enabled():
        raise HTTPException(
            status_code=403,
            detail=("宿主机命令执行已默认禁用。如确需启用，请在可信环境中"
                    "设置环境变量 AUTODEV_ENABLE_SHELL=1。"),
        )


def ws_token_ok(websocket) -> bool:
    """WebSocket 鉴权：未配置 token 放行，否则校验 ?token= 或 Bearer。"""
    token = get_api_token()
    if not token:
        return True
    if websocket.query_params.get("token", "") == token:
        return True
    auth = websocket.headers.get("authorization", "")
    return auth.startswith("Bearer ") and auth[7:].strip() == token
