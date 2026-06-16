"""indexing 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

# 代码索引 API
@router.post("/api/indexing/start")
async def start_indexing():
    """开始索引代码"""
    try:
        state.code_indexer = CodeIndexer(state.workdir)
        
        async def progress_callback(current, total, file):
            await manager.broadcast({
                "type": "indexing_progress",
                "data": {"current": current, "total": total, "file": file}
            })
        
        await state.code_indexer.index_repository(progress_callback)
        
        return {"success": True, "message": "Indexing complete"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/api/indexing/search")
async def search_code(query: str, top_k: int = 10):
    """语义搜索代码"""
    if not state.code_indexer:
        return {"success": False, "error": "Index not built"}
    
    try:
        results = await state.code_indexer.search(query, top_k)
        return {"success": True, "results": results}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/error/analyze")
async def analyze_error(request: ErrorAnalysisRequest):
    """分析错误"""
    from src.core import AutoFixer
    
    fixer = AutoFixer()
    
    # 创建模拟异常
    class MockException(Exception):
        pass
    
    error = MockException(request.error_message)
    suggestions = fixer.get_fix_suggestions(error)
    
    return {
        "success": True,
        "suggestions": suggestions
    }
