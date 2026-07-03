"""项目上下文 / 记忆 路由（从 server.py 拆出；共享状态统一来自 src.web.state）。"""
from fastapi import APIRouter
from src.web.state import state

router = APIRouter()

# 项目上下文 API
@router.get("/api/context")
async def get_project_context():
    """获取项目上下文"""
    if state.project_context:
        return {
            "success": True,
            "instructions": state.project_context.instructions.model_dump() if state.project_context.instructions else None,
            "memory": state.project_context.memory.model_dump(),
            "prompt": state.project_context.get_context_prompt()
        }
    return {"success": False, "error": "Project context not loaded"}

@router.post("/api/context/memory")
async def add_memory(type: str, content: str):
    """添加记忆"""
    if not state.project_context:
        return {"success": False, "error": "Project context not loaded"}
    
    if type == "learning":
        state.project_context.add_learning(content)
    elif type == "pattern":
        state.project_context.add_pattern(content)
    elif type == "convention":
        state.project_context.add_convention(content)
    elif type == "debugging":
        state.project_context.add_debugging_tip(content)
    else:
        return {"success": False, "error": f"Unknown memory type: {type}"}
    
    return {"success": True}
