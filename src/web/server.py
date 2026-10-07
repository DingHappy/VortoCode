"""Web 服务器 — FastAPI 应用装配。

本文件只负责「创建 app + 装配各域路由 + 启动」。具体路由实现见 src/web/routers/，
共享状态见 src/web/state.py，请求模型见 src/web/schemas.py。
（历史上这里是 2000+ 行的巨石，已按域拆分。）
"""

import sys
from pathlib import Path

# 确保以任意工作目录运行时都能 import src.*
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.web.auth import auth_middleware, get_api_token


@asynccontextmanager
async def _lifespan(_app):
    from src.web.runtime import runtime_lifespan
    async with runtime_lifespan():
        yield


app = FastAPI(
    title="VortoCode",
    description="多 Agent 协作开发平台",
    version="0.1.0",
    lifespan=_lifespan,
)

# 鉴权中间件：仅当设置了 VORTOCODE_API_TOKEN 时强制（不破坏本地无 token 使用）
app.middleware("http")(auth_middleware)


@app.middleware("http")
async def workspace_scope_middleware(request, call_next):
    """General 的 REST 面同样无目录；不能只缩 Agent 工具、却让侧栏 API 偷跑 Shell/Git。"""
    from src.gateway.workspace_scope import http_workspace_requirement

    required = http_workspace_requirement(request.url.path)
    if required is not None:
        label = "已有 Git 项目" if required == "project" else "隔离 Scratch"
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "workspace_required",
                    "scope": required,
                    "reason": f"这个功能需要{label}；General 不访问文件、Shell 或 Git",
                }
            },
        )
    return await call_next(request)

# 按域拆分的路由
from src.web.routers.pages import router as pages_router
from src.web.routers.git import router as git_router
from src.web.routers.context import router as context_router
from src.web.routers.ops import router as ops_router
from src.web.routers.realtime import router as realtime_router
from src.web.routers.artifacts import router as artifacts_router
from src.web.routers.auth_routes import router as auth_router
from src.web.routers.tasks import router as tasks_router
from src.web.routers.delegations import router as delegations_router
from src.web.routers.task_inbox import router as task_inbox_router
from src.web.routers.goals import router as goals_router
from src.web.routers.runs import router as runs_router
from src.web.routers.terminals import router as terminals_router
from src.web.routers.decisions import router as decisions_router
from src.web.routers.journal import router as journal_router
from src.web.routers.hooks import router as hooks_router
from src.web.routers.extensions import router as extensions_router
from src.web.routers.cron import router as cron_router
from src.web.routers.dev_plans import router as dev_plans_router
from src.web.routers.pipelines import router as pipelines_router
from src.web.routers.trust import router as trust_router

for _router in (
    pages_router, git_router, context_router,
    ops_router, realtime_router,
    artifacts_router, auth_router, tasks_router, delegations_router, task_inbox_router, goals_router, runs_router, terminals_router, decisions_router,
    journal_router, hooks_router, extensions_router, cron_router, dev_plans_router,
    pipelines_router, trust_router,
):
    app.include_router(_router)


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1"}


def _insecure_bind_reason(host: str) -> str:
    """绑定非本地地址却没鉴权 → 返回拒绝理由（非空）；安全则空串。

    fail-closed（2026-07 审计 P0#5）：此前只打印警告，忘设 token 就把所有 API
    （含驱动主 agent 读写文件、跑命令的 /ws）无鉴权暴露到公网。确需开放（如自建
    反代已鉴权）设 VORTOCODE_ALLOW_INSECURE_BIND=1 显式放行。
    """
    from src.env_compat import env_compat
    if host in _LOCAL_HOSTS:
        return ""
    if get_api_token():
        return ""
    if env_compat("VORTOCODE_ALLOW_INSECURE_BIND", "AUTODEV_ALLOW_INSECURE_BIND", "") \
            .strip().lower() in ("1", "true", "yes", "on"):
        return ""
    return (f"拒绝启动：绑定到非本地地址 {host} 但未设置 VORTOCODE_API_TOKEN，"
            f"会把所有 API（含 /ws 主 agent、可读写文件/跑命令）无鉴权暴露到网络。\n"
            f"      请先设置一个强随机 token：export VORTOCODE_API_TOKEN=<随机串>\n"
            f"      或（确知风险、已有外层鉴权时）：export VORTOCODE_ALLOW_INSECURE_BIND=1")


def start_server(host: str = "127.0.0.1", port: int = 8000, im: str = ""):
    """启动服务器（绑非本地且无鉴权时 fail-closed 拒绝启动，见 _insecure_bind_reason）。

    im：非空则内嵌 IM 桥（telegram|dingtalk，凭证缺失 fail-closed 拒启——显式要了就不能静默没有）。
    """
    import os
    import uvicorn

    import src.llm.client  # noqa: F401 —— 导入即加载 .env（OPENAI_API_KEY 等进 os.environ）。
    # 不导它的话：shell 没 export key 时，/agent 首回合的 key 早检查（realtime.handle_agent_message）
    # 永远走"未配置"降级——llm.client 只会在会话创建后才被导入，而会话创建在检查之后（先有鸡问题）。
    reason = _insecure_bind_reason(host)
    if reason:
        raise SystemExit(f"  ⛔ {reason}")
    im = (im or os.getenv("VORTOCODE_IM", "")).strip().lower()
    if im:                                    # 凭证前置检查：启动前就 fail-closed，不等 lifespan 半路炸
        from src.gateway.im_service import IMConfigError, build_adapter
        try:
            adapter, _owner = build_adapter(im)
        except IMConfigError as e:
            raise SystemExit(f"  ⛔ {e}")
        del adapter                            # 只验配置；真正的 adapter 由 lifespan 构造并管生命周期
        os.environ["VORTOCODE_IM"] = im        # lifespan 从 env 读（uvicorn.run 不传自定义参数）
    print(f"\n{'='*50}")
    print(f"  VortoCode 服务器启动")
    print(f"  访问: http://{host}:{port}")
    if host not in _LOCAL_HOSTS:
        print("  ⚠️  绑定到非本地地址；已设置鉴权/显式放行。请确认 token 足够强。")
    print(f"{'='*50}\n")
    # access_log=False：token 现已移出 URL（走 httpOnly Cookie / Authorization 头，审计 P0#4 已收口），
    # URL 里不再有敏感串；仍关访问日志作为纵深防御（少一处可能记录敏感请求体/头的面）。应用级日志不受影响。
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    start_server()
