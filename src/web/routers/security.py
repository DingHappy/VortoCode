"""安全 / 审批 / 自动批准 路由（从 server.py 拆出；共享状态统一来自 src.web.state）。"""
from fastapi import APIRouter
from src.web.state import state, manager

router = APIRouter()

@router.get("/api/permissions")
async def get_permissions():
    """获取所有 Agent 权限"""
    permissions = {}
    for agent_id, perms in state.permission_manager.agent_permissions.items():
        permissions[agent_id] = {
            "role": perms.role,
            "permissions": [
                {
                    "name": p.permission.value,
                    "allowed": p.allowed,
                    "risk_level": p.risk_level.value,
                    "require_approval": p.require_approval
                }
                for p in perms.permissions.values()
            ]
        }
    return {"permissions": permissions}

@router.get("/api/approvals")
async def get_pending_approvals():
    """获取待审批请求"""
    requests = state.permission_manager.get_pending_requests()
    return {
        "requests": [
            {
                "id": r.id,
                "agent_id": r.agent_id,
                "action": r.action,
                "target": r.target,
                "risk_level": r.risk_level.value,
                "description": r.description
            }
            for r in requests
        ]
    }

@router.post("/api/approvals/{request_id}")
async def approve_request(request_id: str, approved: bool = True):
    """审批请求"""
    success = state.permission_manager.approve_request(request_id, approved)
    if success:
        await manager.broadcast({
            "type": "approval_resolved",
            "data": {"request_id": request_id, "approved": approved}
        })
    return {"success": success}

@router.get("/api/security/stats")
async def get_security_stats():
    """获取安全统计"""
    return {
        "violations": state.safety_guard.get_violation_stats(),
        "banned_agents": list(state.safety_guard.banned_agents)
    }

@router.post("/api/security/unban/{agent_id}")
async def unban_agent(agent_id: str):
    """解禁 Agent"""
    state.safety_guard.unban_agent(agent_id)
    return {"success": True}

@router.post("/api/auto-approve")
async def set_auto_approve(enabled: bool = True):
    """设置自动批准"""
    state.auto_approve = enabled
    return {"success": True, "auto_approve": enabled}
