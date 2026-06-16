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

@router.get("/api/agents/custom")
async def get_custom_agents():
    """获取自定义 Agent 列表"""
    agents = state.custom_agent_manager.list_agents()
    return {
        "agents": [
            {
                "id": a.id,
                "name": a.name,
                "role": a.role.value,
                "description": a.description,
                "capabilities": [c.value for c in a.capabilities],
                "model": a.model,
                "is_active": a.is_active,
                "created_at": a.created_at.isoformat()
            }
            for a in agents
        ]
    }

@router.post("/api/agents/custom")
async def create_custom_agent(request: CreateAgentRequest):
    """创建自定义 Agent"""
    try:
        # 转换角色
        role = AgentRole(request.role)
        
        # 转换能力
        capabilities = []
        for cap in request.capabilities:
            try:
                capabilities.append(CustomAgentCapability(cap))
            except ValueError:
                pass
        
        agent = state.custom_agent_manager.create_agent(
            name=request.name,
            role=role,
            description=request.description,
            system_prompt=request.system_prompt,
            capabilities=capabilities,
            tools=request.tools,
            model=request.model
        )
        
        return {
            "success": True,
            "agent": {
                "id": agent.id,
                "name": agent.name,
                "role": agent.role.value
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/agents/templates/{template_name}")
async def create_agent_from_template(template_name: str):
    """从模板创建 Agent"""
    agent = state.custom_agent_manager.create_from_template(template_name)
    if agent:
        return {
            "success": True,
            "agent": {
                "id": agent.id,
                "name": agent.name,
                "role": agent.role.value
            }
        }
    return {"success": False, "error": f"Template not found: {template_name}"}

@router.delete("/api/agents/custom/{agent_id}")
async def delete_custom_agent(agent_id: str):
    """删除自定义 Agent"""
    success = state.custom_agent_manager.delete_agent(agent_id)
    return {"success": success}

@router.post("/api/agents/custom/{agent_id}/activate")
async def activate_agent(agent_id: str):
    """激活 Agent"""
    success = state.custom_agent_manager.activate_agent(agent_id)
    return {"success": success}

@router.post("/api/agents/custom/{agent_id}/deactivate")
async def deactivate_agent(agent_id: str):
    """停用 Agent"""
    success = state.custom_agent_manager.deactivate_agent(agent_id)
    return {"success": success}

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
