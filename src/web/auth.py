"""Web 层鉴权与执行开关（安全默认值）。

设计原则：本地开发不被打扰，但默认不裸奔危险能力。
- 鉴权：仅当设置了环境变量 VORTOCODE_API_TOKEN 时强制校验
  （Bearer 或 X-API-Token）；未设则放行（配合默认仅绑 127.0.0.1）。
  这样既不破坏本地 UI，又能在对外暴露时一键加固。
- 危险的宿主机命令执行端点默认禁用，需显式 VORTOCODE_ENABLE_SHELL=1。
"""

import hmac
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import JSONResponse


def resolve_within(base, rel) -> Optional[Path]:
    """把 rel 安全解析到 base 目录内；越界返回 None。**实现在 src/utils/paths，全仓唯一一份。**

    这里保留同名函数只为不动存量调用点。原先本模块有一份独立实现，与 agents/memory 那两份
    **漂移过**：空串返回 base 本身（调用方写的是 `is None` 判拒，于是空串过了闸）、
    且不捕 OSError（软链环时异常穿出围栏，安全判定函数抛异常是 fail-open 的形状）。
    现在统一到最严的那套。
    """
    from src.utils.paths import resolve_within as _fence

    return _fence(base, rel)


def get_api_token() -> str:
    from src.env_compat import env_compat
    return env_compat("VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN", "").strip()


# 登录后种下的 httpOnly Cookie 名——token 走 Cookie（浏览器）/ Authorization 头（程序化），
# **绝不再进 URL**（审计 P0#4：?token= 会被 access log / referer / 分享链接泄漏，且 allow-scripts
# 制品 iframe 的脚本能从 location 外带）。
SESSION_COOKIE = "vortocode_session"


def shell_enabled() -> bool:
    from src.env_compat import env_compat
    return env_compat("VORTOCODE_ENABLE_SHELL", "AUTODEV_ENABLE_SHELL", "").strip().lower() \
        in ("1", "true", "yes", "on")


# 鉴权豁免：页面 HTML 外壳（本身不含数据，数据走各自需鉴权的 API）、API 文档、健康检查、
# 登录/状态端点（必须能在鉴权前访问，否则没法登录）。
# 路线 A 下线遗留页后只剩主线页 /、/agent、/artifacts（含制品分享链接）。
_EXEMPT_PREFIXES = ("/docs", "/redoc", "/openapi.json", "/static")
# PWA 静态件必须豁免，而且不豁免就没法用：<link rel="manifest"> 的 fetch 默认 **anonymous
# 不带 Cookie**（除非 crossorigin="use-credentials"），Android 的可安装性检查同样匿名取
# manifest/sw——设了 token 的真机上这四条 401，「加到主屏幕」就退化成普通书签
# （2026-08-02 部署后 curl 当场撞到）。内容全部公开无敏感：名字、图标、三行空监听的 sw。
_EXEMPT_EXACT = {"/", "/agent", "/artifacts", "/review",
                 "/agent.css", "/agent.js",   # 对话台的样式/逻辑（拆分件）：登录门本身要靠它们渲染
                 "/manifest.webmanifest", "/pwa-icon.svg", "/pwa-icon.png", "/sw.js",
                 "/api/health", "/api/health/quick",
                 "/api/auth/login", "/api/auth/logout", "/api/auth/status"}


def ct_eq(candidate: str, token: str) -> bool:
    """常时比较（防时序侧信道）——token 校验点专用。

    encode 成 bytes 兜底：candidate 是攻击者可控输入，裸 hmac.compare_digest(str, str)
    遇非 ASCII 会抛 TypeError → 500。surrogatepass 一并兜住孤代理（如 U+D800）——虽经
    Starlette 的 latin-1 头/Cookie 解码不可达，但不留「任何输入都不抛」的破绽。长度不同
    亦安全（长度本非机密）。仅当本机对外暴露（99 服务器）时侧信道才有网络路径，故收紧。
    """
    return hmac.compare_digest(candidate.encode("utf-8", "surrogatepass"),
                               token.encode("utf-8", "surrogatepass"))


def _token_ok(request) -> bool:
    token = get_api_token()
    if not token:
        return True  # 未配置 token：本地开发放行
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and ct_eq(auth[7:].strip(), token):   # 程序化客户端：Authorization 头
        return True
    if ct_eq(request.headers.get("x-api-token", "").strip(), token):
        return True
    return ct_eq(request.cookies.get(SESSION_COOKIE, "").strip(), token)  # 浏览器：登录后 httpOnly Cookie


def is_authed(request) -> bool:
    """当前请求是否已鉴权（供 /api/auth/status 与前端登录门判断）。"""
    return _token_ok(request)


async def auth_middleware(request, call_next):
    """仅当配置了 VORTOCODE_API_TOKEN 时，对非豁免路径强制校验。"""
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
                    "设置环境变量 VORTOCODE_ENABLE_SHELL=1。"),
        )


def browser_enabled() -> bool:
    from src.env_compat import env_compat
    return env_compat("VORTOCODE_ENABLE_BROWSER", "AUTODEV_ENABLE_BROWSER", "").strip().lower() \
        in ("1", "true", "yes", "on")


def require_browser() -> None:
    """浏览器自动化入口调用；未显式开启则拒绝（fail-closed）。

    浏览器能力可被滥用（SSRF 探内网、读本地文件），与 shell 同样默认禁用。
    """
    if not browser_enabled():
        raise HTTPException(
            status_code=403,
            detail=("浏览器自动化已默认禁用。如确需启用，请在可信环境中"
                    "设置环境变量 VORTOCODE_ENABLE_BROWSER=1。"),
        )


def validate_navigation_url(url: str) -> Optional[str]:
    """校验导航 URL；返回拒绝原因（None=放行）。

    防 SSRF / 本地文件读取：仅允许 http/https，且目标解析出的 IP 不得为
    环回/私有/链路本地/保留地址（挡 file://、127.0.0.1、169.254.169.254 云元数据、
    10/172/192 内网等）。
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return "URL 无法解析"
    if parsed.scheme not in ("http", "https"):
        return f"仅允许 http/https（拒绝 scheme={parsed.scheme or '空'}）"
    host = parsed.hostname
    if not host:
        return "URL 缺少主机名"
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return f"无法解析主机：{host}"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return f"拒绝访问内网/环回/保留地址：{ip}"
    return None


def ws_token_ok(websocket) -> bool:
    """WebSocket 鉴权：未配置 token 放行，否则校验 Bearer 头或**同源握手自带的 Cookie**（不再收 ?token=）。"""
    token = get_api_token()
    if not token:
        return True
    auth = websocket.headers.get("authorization", "")
    if auth.startswith("Bearer ") and ct_eq(auth[7:].strip(), token):
        return True
    return ct_eq(websocket.cookies.get(SESSION_COOKIE, "").strip(), token)
