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


@router.post("/api/agents/{agent_id}/run")
async def run_agent(agent_id: str, request: RunAgentRequest):
    """运行一个已创建的 agent：用它的 system_prompt 执行任务，结果流式广播到 /ws。"""
    a = state.agent_manager.get_agent(agent_id)
    if not a:
        return {"success": False, "error": "Agent not found"}
    asyncio.create_task(_run_single_agent(agent_id, a.config, request.task))
    return {"success": True, "agent_id": agent_id}


async def _run_single_agent(agent_id, config, task):
    """后台执行单个配置 agent，并实时广播状态/输出。"""
    from src.agents.config_agent import build_config_agent
    from src.web.streaming import TokenBatcher

    await _set_agent(agent_id, "running", task[:60])
    add_log("info", f"运行 agent「{config.name}」：{task[:50]}")

    async def _emit_token(text):
        await manager.broadcast({"type": "token", "data": {"role": agent_id, "text": text}})

    batcher = TokenBatcher(_emit_token, max_chars=48)
    agent = build_config_agent(config.name, config.role, config.system_prompt, config.model)
    try:
        result = await agent.execute(task, context={"on_token": batcher.feed})
        await batcher.flush()
        await _set_agent(agent_id, "completed" if result.success else "failed")
        out = str(result.output)[:1000] if result.output else (result.error or "")
        await manager.broadcast({"type": "agent_run_completed",
                                 "data": {"agent_id": agent_id, "success": result.success, "output": out}})
        add_log("success" if result.success else "error",
                f"agent「{config.name}」{'完成' if result.success else '失败'}")
    except Exception as e:  # noqa: BLE001
        await _set_agent(agent_id, "failed")
        await manager.broadcast({"type": "agent_run_completed",
                                 "data": {"agent_id": agent_id, "success": False, "error": str(e)}})


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
    """开始执行"""
    if not state.goal:
        raise HTTPException(status_code=400, detail="Goal not set")

    if state.running:
        raise HTTPException(status_code=400, detail="Already running")

    state.running = True

    # 广播开始事件
    await manager.broadcast({
        "type": "execution_started",
        "data": {"goal": state.goal}
    })

    # 后台执行
    asyncio.create_task(execute_tasks())

    return {"success": True}


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


async def _run_phase(ctx, task, agent, instruction):
    """跑一个单 Agent 阶段并广播；成功则把产出登记进共享上下文。"""
    role = task["agent"]
    task["status"] = "running"
    await _set_agent(role, "running", task["title"])
    await manager.broadcast({"type": "task_started", "data": {"task": task, "agent": role}})
    add_log("info", f"{role} Agent 开始：{task['title']}")
    try:
        result = await agent.execute(instruction, context=ctx)
        if result.success and result.output is not None:
            ctx["artifacts"][role] = result.output
        task["status"] = "completed" if result.success else "failed"
        task["output"] = (str(result.output)[:500] if result.output
                           else ("完成" if result.success else (result.error or "失败")))
        await _set_agent(role, task["status"])
        await manager.broadcast({
            "type": "task_completed" if result.success else "task_failed",
            "data": {"task": task, "agent": role},
        })
        add_log("success" if result.success else "error",
                f"{task['title']} {'完成' if result.success else '失败'}")
        return result
    except Exception as e:
        task["status"] = "failed"
        task["output"] = str(e)
        await _set_agent(role, "failed")
        await manager.broadcast({"type": "task_failed",
                                 "data": {"task": task, "agent": role, "error": str(e)}})
        add_log("error", f"{task['title']} 失败: {e}")
        return None


async def execute_tasks():
    """真实流程：需求 → 架构 → 迭代开发闭环，全程广播进度。"""
    import uuid as _uuid
    from pathlib import Path as _Path
    from src.core.tracing import set_trace_id
    set_trace_id()   # 本次执行一个 trace_id，贯穿日志
    from src.agents import (
        ProductAgent, ArchitectAgent, DeveloperAgent, ReviewerAgent, TesterAgent,
    )
    from src.orchestrator import IterativeDevLoop

    workspace = str(_Path(".auto-dev-crew") / "workspaces" / f"server-{_uuid.uuid4().hex[:8]}")
    ctx = {"task": state.goal, "goal": state.goal, "workspace": workspace, "artifacts": {}}

    # 高层阶段（供 UI 展示）
    tasks = [
        {"id": "task-1", "title": "需求分析", "type": "requirement",
         "agent": "product", "status": "pending", "output": ""},
        {"id": "task-2", "title": "架构设计", "type": "architecture",
         "agent": "architect", "status": "pending", "output": ""},
        {"id": "task-3", "title": "迭代开发（开发→测试→审查→修复）", "type": "implementation",
         "agent": "developer", "status": "pending", "output": ""},
    ]
    state.tasks = tasks
    await manager.broadcast({"type": "tasks_initialized", "data": {"tasks": tasks}})

    try:
        # 1. 需求分析
        if not state.running:
            return
        await _run_phase(ctx, tasks[0], ProductAgent(), state.goal)

        # 2. 架构设计
        if not state.running:
            return
        await _run_phase(ctx, tasks[1], ArchitectAgent(), state.goal)

        # 3. 迭代开发闭环
        if state.running:
            t3 = tasks[2]
            t3["status"] = "running"
            await _set_agent("developer", "running", t3["title"])
            await manager.broadcast({"type": "task_started", "data": {"task": t3, "agent": "developer"}})
            add_log("info", "进入迭代开发闭环：开发→测试→审查→修复")

            loop = IterativeDevLoop(
                DeveloperAgent(), TesterAgent(), ReviewerAgent(), max_iterations=3,
            )

            async def _on_iter(rec):
                state.iterations = rec.iteration
                state.progress = min(95, 30 + rec.iteration * 20)
                add_log("info" if rec.tests_passed else "warning",
                        f"第 {rec.iteration} 轮：测试{'通过' if rec.tests_passed else '未过'}，"
                        f"审查={rec.review_verdict or '?'}")
                await manager.broadcast({"type": "dev_iteration", "data": rec.model_dump()})

            from src.web.streaming import TokenBatcher

            async def _emit_token(text):
                await manager.broadcast({"type": "token", "data": {"role": "developer", "text": text}})

            _batcher = TokenBatcher(_emit_token, max_chars=48)  # 批量节流，避免逐 token 洪泛

            async def _on_token(tok):
                await _batcher.feed(tok)

            result = await loop.run(
                state.goal, workspace=workspace,
                spec=ctx["artifacts"].get("product"),
                architecture=ctx["artifacts"].get("architect"),
                on_iteration=_on_iter,
                on_token=_on_token,
            )
            await _batcher.flush()   # 冲掉末尾余量
            t3["status"] = "completed" if result.success else "failed"
            t3["output"] = f"{result.reason}；文件：{result.files}"
            await _set_agent("developer", t3["status"])
            await manager.broadcast({
                "type": "task_completed" if result.success else "task_failed",
                "data": {"task": t3, "agent": "developer", "result": result.model_dump()},
            })
    finally:
        state.running = False
        if all(t["status"] == "completed" for t in state.tasks):
            state.progress = 100
        await manager.broadcast({
            "type": "execution_completed",
            "data": {"progress": state.progress, "iterations": state.iterations},
        })
        add_log("success", "执行流程结束")
