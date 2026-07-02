"""Web 服务器 — FastAPI 应用装配。

本文件只负责「创建 app + 装配各域路由 + 启动」。具体路由实现见 src/web/routers/，
共享状态见 src/web/state.py，请求模型见 src/web/schemas.py。
（历史上这里是 2000+ 行的巨石，已按域拆分。）
"""

import sys
from pathlib import Path

# 确保以任意工作目录运行时都能 import src.*
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI

from src.web.auth import auth_middleware, get_api_token

app = FastAPI(
    title="VortoCode",
    description="多 Agent 协作开发平台",
    version="0.1.0",
)

# 鉴权中间件：仅当设置了 AUTODEV_API_TOKEN 时强制（不破坏本地无 token 使用）
app.middleware("http")(auth_middleware)

# 按域拆分的路由
from src.web.routers.pages import router as pages_router
from src.web.routers.system import router as system_router
from src.web.routers.execution import router as execution_router
from src.web.routers.security import router as security_router
from src.web.routers.git import router as git_router
from src.web.routers.context import router as context_router
from src.web.routers.agents import router as agents_router
from src.web.routers.skills import router as skills_router
from src.web.routers.indexing import router as indexing_router
from src.web.routers.sessions import router as sessions_router
from src.web.routers.projects import router as projects_router
from src.web.routers.workspaces import router as workspaces_router
from src.web.routers.editor import router as editor_router
from src.web.routers.sandbox import router as sandbox_router
from src.web.routers.browser import router as browser_router
from src.web.routers.github import router as github_router
from src.web.routers.ops import router as ops_router
from src.web.routers.generators import router as generators_router
from src.web.routers.realtime import router as realtime_router
from src.web.routers.artifacts import router as artifacts_router

for _router in (
    pages_router, system_router, execution_router,
    security_router, git_router, context_router,
    agents_router, skills_router, indexing_router, sessions_router,
    projects_router, workspaces_router, editor_router, sandbox_router,
    browser_router, github_router, ops_router, generators_router, realtime_router,
    artifacts_router,
):
    app.include_router(_router)


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1"}


def _insecure_bind_reason(host: str) -> str:
    """绑定非本地地址却没鉴权 → 返回拒绝理由（非空）；安全则空串。

    fail-closed（2026-07 审计 P0#5）：此前只打印警告，忘设 token 就把所有 API
    （含驱动主 agent 读写文件、跑命令的 /ws）无鉴权暴露到公网。确需开放（如自建
    反代已鉴权）设 AUTODEV_ALLOW_INSECURE_BIND=1 显式放行。
    """
    import os
    if host in _LOCAL_HOSTS:
        return ""
    if get_api_token():
        return ""
    if os.getenv("AUTODEV_ALLOW_INSECURE_BIND", "").strip().lower() in ("1", "true", "yes", "on"):
        return ""
    return (f"拒绝启动：绑定到非本地地址 {host} 但未设置 AUTODEV_API_TOKEN，"
            f"会把所有 API（含 /ws 主 agent、可读写文件/跑命令）无鉴权暴露到网络。\n"
            f"      请先设置一个强随机 token：export AUTODEV_API_TOKEN=<随机串>\n"
            f"      或（确知风险、已有外层鉴权时）：export AUTODEV_ALLOW_INSECURE_BIND=1")


def start_server(host: str = "127.0.0.1", port: int = 8000):
    """启动服务器（绑非本地且无鉴权时 fail-closed 拒绝启动，见 _insecure_bind_reason）。"""
    import uvicorn
    reason = _insecure_bind_reason(host)
    if reason:
        raise SystemExit(f"  ⛔ {reason}")
    print(f"\n{'='*50}")
    print(f"  VortoCode 服务器启动")
    print(f"  访问: http://{host}:{port}")
    if host not in _LOCAL_HOSTS:
        print("  ⚠️  绑定到非本地地址；已设置鉴权/显式放行。请确认 token 足够强。")
    print(f"{'='*50}\n")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    start_server()
