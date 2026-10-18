"""pages 路由（从 server.py 拆出）。

2026-07 路线 A：遗留页 admin / index(classic) / workstation / multi-workspace 及其
路由已下线删除——它们是 5 角色批处理编排时代的 UI；其后端（execution/security/
workspaces/indexing/sessions 路由与引擎）已于 b4 全部物理删除。
2026-10-17 浏览器对话台（/agent、/artifacts 画廊）退役后，只剩 / 的 API 说明与 /review 审批台。
"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_WEB_DIR = _PROJECT_ROOT / "web"


@router.get("/")
async def root():
    """服务首页：浏览器对话台已于 2026-10-17 退役，入口是 Desktop 与无头 CLI。"""
    return {"message": "VortoCode API", "version": "0.1.0",
            "clients": "Desktop 或 vc agent；流水线审批台在 /review"}


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
