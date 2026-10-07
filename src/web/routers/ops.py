"""ops 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()


# 状态快照（从退役的 system.py 迁来——它是冻结协议 get_status→status 的 REST 孪生，
# 且是鉴权测试的探针端点；路线 A 其余 system 端点已随 b4 PR-B3 删除）
@router.get("/api/status")
async def get_status():
    return state.to_dict()


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

# 注：旧的 /api/tasks/queue 与 /api/tasks/list（基于从未接线的 src/core/task_queue 骨架）已退役。
# 后台任务改由常驻运行时提供：见 src/web/routers/tasks.py（POST/GET /api/tasks…，gateway.TaskRunner）。
