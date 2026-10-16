"""Read-only, redacted project extension inventory for Desktop."""
from __future__ import annotations

import os

from src.web.deps import APIRouter

router = APIRouter()


@router.get("/api/extensions/inspect")
async def inspect_project_extensions():
    from src.workflow.workbench.extensions_inspect import inspect_extensions

    return inspect_extensions(os.getcwd())


def _mcp_session(session: str):
    from src.web.deps import HTTPException
    from src.web.routers.realtime import _SESSIONS, _ACTORS
    key = f'sid-{session}'
    record = _SESSIONS.get(key)
    if not session or len(session) > 180 or record is None or record.get('agent') is None:
        raise HTTPException(404, '会话尚未初始化，请先打开对应会话。')
    return key, record, record['agent'], _ACTORS


@router.get('/api/extensions/mcp')
async def mcp_connection_status(session: str):
    from src.agents.mcp_session import snapshot
    _, _, agent, _ = _mcp_session(session)
    return snapshot(agent)


@router.post('/api/extensions/mcp/connect')
async def connect_session_mcp(session: str):
    from src.web.deps import HTTPException
    from src.web.routers.realtime import _SESSIONS
    from src.agents.mcp_session import connect_agent
    from src.gateway.workspace_scope import current_workspace_scope
    key, record, agent, actors = _mcp_session(session)
    if actors.busy(key):
        raise HTTPException(409, '请等待当前任务结束后连接 MCP。')
    holder = getattr(agent, '_web_confirm_holder', None)
    async def confirm(message):
        fn = holder.get('fn') if holder else None
        return bool(await fn(message)) if fn else False
    try:
        return await connect_agent(agent, os.getcwd(), confirm=confirm, workspace_scope=current_workspace_scope(),
                                   current=lambda: _SESSIONS.get(key) is record and record.get('agent') is agent and not actors.busy(key))
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except Exception:
        raise HTTPException(409, 'MCP 未更新；检查配置、服务及会话状态后可手动重试。') from None
