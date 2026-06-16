"""workspaces 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

@router.post("/api/workspaces")
async def create_workspace(request: CreateWorkspaceRequest):
    """创建工作区"""
    try:
        workspace = state.workspace_manager.create_workspace(
            name=request.name,
            goal=request.goal,
            project_id=request.project_id
        )
        return {"success": True, "workspace": workspace.to_dict()}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/api/workspaces")
async def list_workspaces():
    """列出所有工作区"""
    workspaces = state.workspace_manager.list_workspaces()
    return {"workspaces": workspaces}

@router.get("/api/workspaces/{workspace_id}")
async def get_workspace(workspace_id: str):
    """获取工作区详情"""
    workspace = state.workspace_manager.get_workspace(workspace_id)
    if workspace:
        return {"success": True, "workspace": workspace.to_dict()}
    return {"success": False, "error": "Workspace not found"}

@router.delete("/api/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: str):
    """删除工作区"""
    success = state.workspace_manager.delete_workspace(workspace_id)
    return {"success": success}

@router.post("/api/workspaces/{workspace_id}/switch")
async def switch_workspace(workspace_id: str):
    """切换活跃工作区"""
    success = state.workspace_manager.switch_workspace(workspace_id)
    return {"success": success}

@router.post("/api/workspaces/{workspace_id}/execute")
async def execute_in_workspace(workspace_id: str, request: ExecuteWorkspaceRequest):
    """在工作区执行任务"""
    async def callback(ws_id, event_type, data):
        await manager.broadcast({
            "type": f"workspace_{event_type}",
            "data": {"workspace_id": ws_id, **data}
        })
    
    try:
        result = await state.workspace_manager.execute_in_workspace(
            workspace_id, request.task, callback
        )
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/workspaces/execute-parallel")
async def execute_parallel_tasks(tasks: List[Dict[str, Any]]):
    """并行执行多个工作区任务"""
    async def callback(ws_id, event_type, data):
        await manager.broadcast({
            "type": f"workspace_{event_type}",
            "data": {"workspace_id": ws_id, **data}
        })
    
    try:
        results = await state.workspace_manager.execute_parallel(tasks, callback)
        return {"success": True, "results": results}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/api/workspaces/stats/all")
async def get_all_workspace_stats():
    """获取所有工作区统计"""
    stats = state.workspace_manager.get_all_stats()
    return {"stats": stats}
