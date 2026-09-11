"""pages 路由（从 server.py 拆出）。

2026-07 路线 A：遗留页 admin / index(classic) / workstation / multi-workspace 及其
路由已下线删除——它们是 5 角色批处理编排时代的 UI；其后端（execution/security/
workspaces/indexing/sessions 路由与引擎）已于 b4 全部物理删除。
主线只保留 /（agent 台）、/agent、/artifacts。
"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_WEB_DIR = _PROJECT_ROOT / "web"


@router.get("/")
async def root():
    """默认首页 = 主线 agent 对话台。"""
    html_path = _WEB_DIR / "agent.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return {"message": "VortoCode API", "version": "0.1.0"}


@router.get("/review")
def review_console():
    """审批台：审阅流水线产出、给回执。

    IM 只负责"叫人"（状态变化推一条通知），审阅在这里做——一屏几十行的选题池在手机上
    读不了，人需要能横向对比、能展开原文。两条路共用同一份状态，不是两套。
    """
    html_path = _WEB_DIR / "review.html"
    if html_path.is_file():
        return FileResponse(html_path, media_type="text/html")
    return HTMLResponse("<h1>审批台页面缺失</h1>", status_code=404)


@router.get("/agent")
async def agent_view():
    """返回主 agent 网页对话台（WebSocket 流式，复用 TUI 的主 agent loop）"""
    html_path = _WEB_DIR / "agent.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return {"message": "Agent page not found"}


# ── 对话台静态件（2026-08 重设计拆分）：样式与逻辑从 agent.html 拆出 ────────────
# 与 PWA 件同款做法：逐条显式路由（路由集合被契约测试冻结），并进 auth 豁免清单——
# 页面本身免鉴权，它的静态件也必须免，否则登录门连样式都加载不出来。

_AGENT_ASSETS = {
    "agent.css": "text/css",
    "agent.js": "text/javascript",
}


def _agent_asset(name: str):
    path = _WEB_DIR / name
    if path.exists():
        return FileResponse(path, media_type=_AGENT_ASSETS[name])
    raise HTTPException(status_code=404, detail=f"{name} 缺失")


@router.get("/agent.css")
async def agent_css():
    return _agent_asset("agent.css")


@router.get("/agent.js")
async def agent_js():
    return _agent_asset("agent.js")


# ── PWA 静态件（P3）：manifest / icon / service worker ─────────────────────────
# 逐条显式路由而不是挂 StaticFiles：本服务的路由集合被契约测试冻结（server_routes_baseline），
# 一个目录挂载等于开一扇"往 web/ 丢文件就自动可访问"的门，契约就看不住了。
# sw.js 必须从根路径服务——SW 的作用域由其 URL 路径决定，挂深了管不到 /agent。

_PWA_FILES = {
    "manifest.webmanifest": "application/manifest+json",
    "pwa-icon.svg": "image/svg+xml",
    "pwa-icon.png": "image/png",
    "sw.js": "text/javascript",
}


def _pwa_file(name: str):
    path = _WEB_DIR / name
    if path.exists():
        return FileResponse(path, media_type=_PWA_FILES[name])
    raise HTTPException(status_code=404, detail=f"{name} 缺失")


@router.get("/manifest.webmanifest")
async def pwa_manifest():
    return _pwa_file("manifest.webmanifest")


@router.get("/pwa-icon.svg")
async def pwa_icon_svg():
    return _pwa_file("pwa-icon.svg")


@router.get("/pwa-icon.png")
async def pwa_icon_png():
    return _pwa_file("pwa-icon.png")


@router.get("/sw.js")
async def pwa_service_worker():
    return _pwa_file("sw.js")
