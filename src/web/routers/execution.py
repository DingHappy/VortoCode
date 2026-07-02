"""execution 路由（从 server.py 拆出）。

execute_tasks 驱动真实多 Agent 流程：需求分析 → 架构设计 → 迭代开发闭环
（开发→测试→审查→失败反馈修复），并把每阶段与每轮迭代实时广播到 WebSocket。
"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()


async def _set_agent(role: str, status: str, current_task=None):
    """更新某 agent 的状态并实时广播（前端 admin 监听 agent_status）。"""
    state.agents[role] = {"status": status, "current_task": current_task}
    await manager.broadcast({
        "type": "agent_status",
        "data": {"agent_id": role, "role": role, "status": status, "current_task": current_task},
    })


_RUNNING = {}          # agent_id -> asyncio.Task（用于停止与防重复）
_RUN_TIMEOUT = 300     # 单次运行最长 5 分钟，超时即终止


@router.post("/api/agents/{agent_id}/run")
async def run_agent(agent_id: str, request: RunAgentRequest):
    """运行一个已创建的 agent：用它的 system_prompt 执行任务，结果流式广播到 /ws。"""
    a = state.agent_manager.get_agent(agent_id)
    if not a:
        return {"success": False, "error": "Agent not found"}
    if agent_id in _RUNNING and not _RUNNING[agent_id].done():
        return {"success": False, "error": "Agent already running"}
    _RUNNING[agent_id] = asyncio.create_task(_run_single_agent(agent_id, a.config, request.task))
    return {"success": True, "agent_id": agent_id}


@router.post("/api/agents/{agent_id}/stop")
async def stop_agent(agent_id: str):
    """停止正在运行的 agent。"""
    t = _RUNNING.get(agent_id)
    if t and not t.done():
        t.cancel()
        return {"success": True, "agent_id": agent_id}
    return {"success": False, "error": "Agent not running"}


async def _run_single_agent(agent_id, config, task):
    """后台执行单个配置 agent，并实时广播状态/输出；支持超时与取消。"""
    from src.agents.config_agent import build_config_agent
    from src.web.streaming import TokenBatcher

    await _set_agent(agent_id, "running", task[:60])
    add_log("info", f"运行 agent「{config.name}」：{task[:50]}")

    async def _emit_token(text):
        await manager.broadcast({"type": "token", "data": {"role": agent_id, "text": text}})

    batcher = TokenBatcher(_emit_token, max_chars=48)
    agent = build_config_agent(config.name, config.role, config.system_prompt, config.model)
    try:
        result = await asyncio.wait_for(
            agent.execute(task, context={"on_token": batcher.feed}), timeout=_RUN_TIMEOUT)
        await batcher.flush()
        await _set_agent(agent_id, "completed" if result.success else "failed")
        out = str(result.output)[:1000] if result.output else (result.error or "")
        await manager.broadcast({"type": "agent_run_completed",
                                 "data": {"agent_id": agent_id, "success": result.success, "output": out}})
        add_log("success" if result.success else "error",
                f"agent「{config.name}」{'完成' if result.success else '失败'}")
    except (asyncio.CancelledError, asyncio.TimeoutError) as e:
        cancelled = isinstance(e, asyncio.CancelledError)
        reason = "已取消" if cancelled else f"超时（{_RUN_TIMEOUT}s）"
        try:
            await _set_agent(agent_id, "cancelled" if cancelled else "failed")
            await manager.broadcast({"type": "agent_run_completed",
                                     "data": {"agent_id": agent_id, "success": False, "error": reason}})
        except Exception:  # noqa: BLE001
            pass
        add_log("warning", f"agent「{config.name}」{reason}")
        if cancelled:
            raise   # 让任务真正进入 cancelled 状态
    except Exception as e:  # noqa: BLE001
        await _set_agent(agent_id, "failed")
        await manager.broadcast({"type": "agent_run_completed",
                                 "data": {"agent_id": agent_id, "success": False, "error": str(e)}})
    finally:
        _RUNNING.pop(agent_id, None)


@router.post("/api/goal")
async def set_goal(request: GoalRequest):
    """设置目标"""
    state.goal = request.goal
    state.running = False
    state.progress = 0
    state.iterations = 0

    await manager.broadcast({
        "type": "goal_set",
        "data": {"goal": request.goal}
    })

    return {"success": True, "goal": request.goal}


@router.post("/api/start")
async def start_execution():
    """（已退役）此前触发 5 角色批处理流水线（需求→架构→迭代开发）。

    路线 A（2026-07）已退役该流水线；接口保留、返回退役说明避免旧 UI 404。
    项目级自动开发请改用交互式主 agent（vc tui / vc agent）或隔离 dev 流水线
    （dev_isolated / dev_auto）。
    """
    return {"success": False,
            "error": "5 角色批处理流水线已退役；请用 vc tui / vc agent（隔离 dev 流水线）"}


@router.post("/api/stop")
async def stop_execution():
    """停止执行"""
    state.running = False

    await manager.broadcast({
        "type": "execution_stopped",
        "data": {}
    })

    return {"success": True}


@router.post("/api/reset")
async def reset_state():
    """重置状态"""
    state.running = False
    state.goal = ""
    state.tasks = []
    state.agents = {}
    state.logs = []
    state.progress = 0
    state.iterations = 0

    await manager.broadcast({
        "type": "state_reset",
        "data": {}
    })

    return {"success": True}


@router.post("/api/tasks/{task_id}/update")
async def update_task(task_id: str, update: TaskUpdate):
    """更新任务状态"""
    for task in state.tasks:
        if task.get("id") == task_id:
            task["status"] = update.status
            if update.output:
                task["output"] = update.output

            await manager.broadcast({
                "type": "task_updated",
                "data": task
            })
            break

    return {"success": True}


# 注：5 角色批处理流水线（_run_phase + execute_tasks：需求→架构→迭代开发闭环）
# 已随路线 A（2026-07）退役删除。/api/start 现返回退役说明，不再拉起该流程。
# 主线自动开发走交互式主 agent（vc tui / vc agent）与隔离 dev 流水线（dev_isolated/dev_auto）。
