"""项目上下文 / 记忆 路由（从 server.py 拆出；共享状态统一来自 src.web.state）。"""
from pathlib import Path

from fastapi import APIRouter

from src.memory.session_store import SessionStore
from src.memory.write_policy import MemoryWriteRequest, MemoryWriter
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
    """显式 Web POST 写记忆；同样经过长期记忆策略，不允许旧路由绕过。"""
    if not state.project_context:
        return {"success": False, "error": "Project context not loaded"}
    writers = {
        "learning": state.project_context.add_learning,
        "pattern": state.project_context.add_pattern,
        "convention": state.project_context.add_convention,
        "debugging": state.project_context.add_debugging_tip,
    }
    project_writer = writers.get(type)
    if project_writer is None:
        return {"success": False, "error": f"Unknown memory type: {type}"}

    store = SessionStore(str(Path(state.workdir) / ".vortocode" / "sessions.db"))
    result = MemoryWriter(store).write(
        MemoryWriteRequest(
            content=content,
            source="web_context_api",
            session_id="web-context",
            write_method="http_post",
            memory_type=f"project_{type}",
        ),
        confirmed=True,  # 发出 POST 本身就是这个旧 API 的显式用户写动作
        confirmed_by="explicit_http_request",
    )
    if result.status != "stored":
        return {
            "success": False,
            "status": result.status,
            "proposal_id": result.record_id,
            "error": result.message,
        }

    # 保持旧 /api/context 返回结构；SQLite 记录提供跨端召回与 provenance 审计。
    project_writer(content)
    return {"success": True, "memory_id": result.record_id}
