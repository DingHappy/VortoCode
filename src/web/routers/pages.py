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


@router.get("/agent")
async def agent_view():
    """返回主 agent 网页对话台（WebSocket 流式，复用 TUI 的主 agent loop）"""
    html_path = _WEB_DIR / "agent.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return {"message": "Agent page not found"}
