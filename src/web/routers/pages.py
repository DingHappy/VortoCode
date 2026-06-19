"""pages 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

# API 路由
@router.get("/")
async def root():
    """返回管理控制台"""
    html_path = Path(__file__).parent.parent.parent / "web" / "admin.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return {"message": "Auto-Dev-Crew API", "version": "0.1.0"}

@router.get("/workspace")
async def workspace_view():
    """返回多工作区视图"""
    html_path = Path(__file__).parent.parent.parent / "web" / "multi-workspace.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return {"message": "Auto-Dev-Crew API", "version": "0.1.0"}

@router.get("/workstation")
async def workstation_view():
    """返回工位视图"""
    html_path = Path(__file__).parent.parent.parent / "web" / "workstation.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return {"message": "Auto-Dev-Crew API", "version": "0.1.0"}

@router.get("/classic")
async def classic_view():
    """返回经典视图"""
    html_path = Path(__file__).parent.parent.parent / "web" / "index.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return {"message": "Auto-Dev-Crew API", "version": "0.1.0"}


@router.get("/quant")
async def quant_view():
    """返回量化研究监控页面"""
    html_path = Path(__file__).parent.parent.parent / "web" / "quant.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return {"message": "Quant page not found"}
