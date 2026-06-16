"""projects 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

@router.post("/api/projects")
async def add_project(request: AddProjectRequest):
    """添加项目"""
    try:
        config = state.project_manager.add_project(
            name=request.name,
            path=request.path,
            description=request.description,
            tech_stack=request.tech_stack
        )
        return {"success": True, "project": config.model_dump()}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/api/projects")
async def list_projects():
    """列出所有项目"""
    projects = state.project_manager.list_projects()
    return {"projects": projects}

@router.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    """获取项目详情"""
    project = state.project_manager.get_project(project_id)
    if project:
        return {
            "success": True,
            "project": {
                "id": project.config.id,
                "name": project.config.name,
                "path": project.config.path,
                "description": project.config.description,
                "tech_stack": project.config.tech_stack,
                "stats": project.stats
            }
        }
    return {"success": False, "error": "Project not found"}

@router.delete("/api/projects/{project_id}")
async def remove_project(project_id: str):
    """移除项目"""
    success = state.project_manager.remove_project(project_id)
    return {"success": success}

@router.post("/api/projects/{project_id}/switch")
async def switch_project(project_id: str):
    """切换活跃项目"""
    success = state.project_manager.switch_project(project_id)
    if success:
        # 更新工作目录
        project = state.project_manager.get_active_project()
        if project:
            state.workdir = str(project.workdir)
    return {"success": success}

@router.get("/api/projects/{project_id}/files")
async def get_project_files(project_id: str):
    """获取项目文件列表"""
    files = state.project_manager.get_project_files(project_id)
    return {"files": files}

@router.get("/api/projects/{project_id}/search")
async def search_project(project_id: str, query: str):
    """在项目中搜索"""
    results = state.project_manager.search_in_project(project_id, query)
    return {"results": results}

@router.post("/api/projects/{project_id}/execute")
async def execute_in_project(project_id: str, task: str):
    """在指定项目中执行任务"""
    try:
        result = await state.multi_project_orchestrator.execute_in_project(
            project_id, task
        )
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/api/projects/stats/all")
async def get_all_project_stats():
    """获取所有项目统计"""
    stats = state.multi_project_orchestrator.get_all_stats()
    return {"stats": stats}
