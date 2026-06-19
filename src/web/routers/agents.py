"""agents 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

# 自定义 Agent API
@router.get("/api/agents/templates")
async def get_agent_templates():
    """获取 Agent 模板列表"""
    templates = state.custom_agent_manager.list_templates()
    return {
        "templates": [
            {
                "name": t.name,
                "role": t.role.value,
                "description": t.description,
                "capabilities": [c.value for c in t.capabilities],
                "tools": t.tools,
                "icon": t.icon
            }
            for t in templates
        ]
    }

# 统一：custom 与 advanced 共用同一个 agent_manager 存储（重启不丢、两页互通）。
# custom 端点保留原响应形状（前端无感），但读写都打到 state.agent_manager。
def _to_custom_shape(a):
    return {
        "id": a.config.id,
        "name": a.config.name,
        "role": a.config.role,
        "description": a.config.description,
        "capabilities": [c.value for c in a.config.capabilities],
        "model": a.config.model,
        "is_active": a.config.is_active,
        "created_at": a.config.created_at.isoformat(),
    }


def _map_caps(values):
    """把字符串能力映射到 AgentManager 的 AgentCapability，跳过无法识别的。"""
    from src.agents.manager import AgentCapability as _Cap
    out = []
    for v in values or []:
        try:
            out.append(_Cap(v.value if hasattr(v, "value") else v))
        except ValueError:
            pass
    return out


@router.get("/api/agents/custom")
async def get_custom_agents():
    """获取 Agent 列表（与 advanced 同一份存储）"""
    return {"agents": [_to_custom_shape(a) for a in state.agent_manager.list_agents()]}

@router.post("/api/agents/custom")
async def create_custom_agent(request: CreateAgentRequest):
    """创建 Agent（写入统一存储）"""
    try:
        agent = state.agent_manager.create_agent(
            name=request.name,
            role=str(request.role),
            description=request.description,
            capabilities=_map_caps(request.capabilities),
            tools=request.tools,
            system_prompt=request.system_prompt,
            model=request.model,
        )
        return {"success": True, "agent": {"id": agent.config.id, "name": agent.config.name,
                                           "role": agent.config.role}}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/agents/templates/{template_name}")
async def create_agent_from_template(template_name: str):
    """从（custom 那套）模板创建 Agent，写入统一存储"""
    tmpl = next((t for t in state.custom_agent_manager.list_templates()
                 if t.name == template_name), None)
    if not tmpl:
        return {"success": False, "error": f"Template not found: {template_name}"}
    agent = state.agent_manager.create_agent(
        name=tmpl.name,
        role=tmpl.role.value,
        description=tmpl.description,
        capabilities=_map_caps(tmpl.capabilities),
        tools=tmpl.tools,
        system_prompt=tmpl.system_prompt,
    )
    return {"success": True, "agent": {"id": agent.config.id, "name": agent.config.name,
                                       "role": agent.config.role}}

@router.delete("/api/agents/custom/{agent_id}")
async def delete_custom_agent(agent_id: str):
    """删除 Agent（统一存储）"""
    return {"success": state.agent_manager.delete_agent(agent_id)}

@router.post("/api/agents/custom/{agent_id}/activate")
async def activate_agent(agent_id: str):
    """激活 Agent"""
    return {"success": state.agent_manager.set_active(agent_id, True)}

@router.post("/api/agents/custom/{agent_id}/deactivate")
async def deactivate_agent(agent_id: str):
    """停用 Agent"""
    return {"success": state.agent_manager.set_active(agent_id, False)}

# 高级 Agent 管理 API
@router.get("/api/agents/advanced")
async def list_advanced_agents(role: str = None, active_only: bool = False):
    """列出所有 Agent"""
    agents = state.agent_manager.list_agents(role, active_only)
    return {
        "agents": [a.to_dict() for a in agents],
        "stats": state.agent_manager.get_performance_stats()
    }

@router.get("/api/agents/advanced/{agent_id}")
async def get_advanced_agent(agent_id: str):
    """获取 Agent 详情"""
    agent = state.agent_manager.get_agent(agent_id)
    if agent:
        return {"success": True, "agent": agent.to_dict()}
    return {"success": False, "error": "Agent not found"}

@router.post("/api/agents/advanced")
async def create_advanced_agent(request: CreateAdvancedAgentRequest):
    """创建 Agent"""
    try:
        from src.agents.manager import AgentCapability
        
        # 转换能力
        capabilities = []
        for cap in request.capabilities:
            try:
                capabilities.append(AgentCapability(cap))
            except ValueError:
                pass
        
        agent = state.agent_manager.create_agent(
            name=request.name,
            role=request.role,
            description=request.description,
            avatar=request.avatar,
            color=request.color,
            capabilities=capabilities,
            tools=request.tools,
            system_prompt=request.system_prompt,
            model=request.model
        )
        
        return {"success": True, "agent": agent.to_dict()}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/agents/advanced/from-template/{template_name}")
async def create_agent_from_template(template_name: str):
    """从模板创建 Agent"""
    agent = state.agent_manager.create_from_template(template_name)
    if agent:
        return {"success": True, "agent": agent.to_dict()}
    return {"success": False, "error": "Template not found"}

@router.delete("/api/agents/advanced/{agent_id}")
async def delete_advanced_agent(agent_id: str):
    """删除 Agent"""
    success = state.agent_manager.delete_agent(agent_id)
    return {"success": success}

@router.post("/api/agents/advanced/{agent_id}/toggle")
async def toggle_advanced_agent(agent_id: str):
    """切换 Agent 状态"""
    success = state.agent_manager.toggle_agent(agent_id)
    return {"success": success}

@router.get("/api/agents/advanced/{agent_id}/performance")
async def get_agent_performance(agent_id: str):
    """获取 Agent 性能统计"""
    agent = state.agent_manager.get_agent(agent_id)
    if agent:
        return {
            "success": True,
            "performance": {
                "total_tasks": agent.performance.total_tasks,
                "successful_tasks": agent.performance.successful_tasks,
                "failed_tasks": agent.performance.failed_tasks,
                "success_rate": agent.performance.success_rate,
                "avg_duration": agent.performance.avg_task_duration,
                "total_tokens": agent.performance.total_tokens,
                "history": agent.history[-20:]  # 最近20条
            }
        }
    return {"success": False, "error": "Agent not found"}

@router.get("/api/agents/advanced/stats")
async def get_agents_stats():
    """获取所有 Agent 统计"""
    stats = state.agent_manager.get_performance_stats()
    return {"stats": stats}
