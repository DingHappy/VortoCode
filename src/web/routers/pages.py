"""pages 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_WEB_DIR = _PROJECT_ROOT / "web"

# API 路由
@router.get("/")
async def root():
    """返回管理控制台"""
    html_path = _WEB_DIR / "admin.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return {"message": "Auto-Dev-Crew API", "version": "0.1.0"}

@router.get("/workspace")
async def workspace_view():
    """返回多工作区视图"""
    html_path = _WEB_DIR / "multi-workspace.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return {"message": "Auto-Dev-Crew API", "version": "0.1.0"}

@router.get("/workstation")
async def workstation_view():
    """返回工位视图"""
    html_path = _WEB_DIR / "workstation.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return {"message": "Auto-Dev-Crew API", "version": "0.1.0"}

@router.get("/classic")
async def classic_view():
    """返回经典视图"""
    html_path = _WEB_DIR / "index.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return {"message": "Auto-Dev-Crew API", "version": "0.1.0"}


@router.get("/quant")
async def quant_view():
    """返回量化研究监控页面"""
    html_path = _WEB_DIR / "quant.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return {"message": "Quant page not found"}
