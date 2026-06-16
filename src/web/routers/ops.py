"""ops 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

# 监控和指标 API
@router.get("/api/monitoring/metrics")
async def get_metrics():
    """获取所有指标（进程级全局收集器：LLM/Agent/编排实时埋点）"""
    from src.core import metrics
    return {"metrics": metrics.get_all_metrics()}

@router.post("/api/monitoring/reset")
async def reset_metrics():
    """重置指标"""
    from src.core import metrics
    metrics.reset()
    return {"success": True}

# 健康检查 API
@router.get("/api/health")
async def health_check():
    """健康检查"""
    from src.core import HealthChecker, check_memory, check_disk, check_cpu
    
    # 初始化健康检查器（如果不存在）
    if not hasattr(state, 'health_checker'):
        state.health_checker = HealthChecker()
        state.health_checker.register_check("memory", check_memory)
        state.health_checker.register_check("disk", check_disk)
        state.health_checker.register_check("cpu", check_cpu)
    
    result = await state.health_checker.run_checks()
    return result

@router.get("/api/health/quick")
async def quick_health_check():
    """快速健康检查"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

# 缓存 API
@router.get("/api/cache/stats")
async def get_cache_stats():
    """获取缓存统计"""
    from src.core import CacheManager
    
    # 初始化缓存管理器（如果不存在）
    if not hasattr(state, 'cache_manager'):
        state.cache_manager = CacheManager()
    
    return {"stats": state.cache_manager.get_stats()}

@router.post("/api/cache/clear")
async def clear_cache():
    """清除缓存"""
    if hasattr(state, 'cache_manager'):
        await state.cache_manager.clear()
    return {"success": True}

# 任务队列 API
@router.get("/api/tasks/queue")
async def get_task_queue():
    """获取任务队列状态"""
    from src.core import TaskQueue
    
    # 初始化任务队列（如果不存在）
    if not hasattr(state, 'task_queue'):
        state.task_queue = TaskQueue()
    
    return {"stats": state.task_queue.get_stats()}

@router.get("/api/tasks/list")
async def list_tasks(status: str = None):
    """列出任务"""
    if not hasattr(state, 'task_queue'):
        return {"tasks": []}
    
    from src.core import TaskStatus
    
    task_status = TaskStatus(status) if status else None
    tasks = state.task_queue.list_tasks(task_status)
    
    return {
        "tasks": [
            {
                "id": t.id,
                "name": t.name,
                "status": t.status.value,
                "priority": t.priority.value,
                "created_at": t.created_at.isoformat(),
                "error": t.error
            }
            for t in tasks
        ]
    }
