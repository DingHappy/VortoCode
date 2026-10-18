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


# ── MCP 服务管理（Desktop 插件页）：看清会不会启动、增删启停免凭据 HTTP 服务、测试连接 ──

def _mcp_config_call(fn, *args):
    from src.web.deps import HTTPException
    from src.workflow.workbench.mcp_config import McpConfigError
    try:
        return fn(os.getcwd(), *args)
    except McpConfigError as error:
        raise HTTPException(409 if "已经有" in str(error) else 400, str(error)) from None


@router.get("/api/extensions/mcp/servers")
async def list_mcp_servers():
    from src.workflow.workbench.mcp_config import list_servers
    return _mcp_config_call(list_servers)


@router.post("/api/extensions/mcp/servers")
async def add_mcp_server(body: dict):
    from src.workflow.workbench.mcp_config import add_http_server
    return _mcp_config_call(add_http_server, str((body or {}).get("name") or ""), str((body or {}).get("url") or ""))


@router.put("/api/extensions/mcp/servers/{name}/enabled")
async def set_mcp_server_enabled(name: str, body: dict):
    from src.web.deps import HTTPException
    from src.workflow.workbench.mcp_config import set_enabled
    enabled = (body or {}).get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(400, "enabled 需要是 true 或 false")
    return _mcp_config_call(set_enabled, name, enabled)


@router.delete("/api/extensions/mcp/servers/{name}")
async def remove_mcp_server(name: str):
    from src.workflow.workbench.mcp_config import remove_server
    _mcp_config_call(remove_server, name)
    return {"ok": True}


@router.post("/api/extensions/mcp/servers/{name}/test")
async def test_mcp_server(name: str):
    from src.web.deps import HTTPException
    from src.workflow.workbench.mcp_config import McpConfigError, test_server
    try:
        return await test_server(os.getcwd(), name)
    except McpConfigError as error:
        raise HTTPException(400, str(error)) from None


# ── 项目技能管理（Desktop 插件页）：skills/（随仓库）与 .vortocode/skills/（只在本机） ──

def _skills_call(fn, *args):
    from src.web.deps import HTTPException
    from src.workflow.workbench.project_skills import SkillConflict, SkillError
    try:
        return fn(os.getcwd(), *args)
    except SkillConflict as error:
        raise HTTPException(409, str(error)) from None
    except SkillError as error:
        raise HTTPException(404 if str(error).startswith("没有这个技能") else 400, str(error)) from None


@router.get("/api/extensions/skills")
async def list_project_skills():
    from src.workflow.workbench.project_skills import list_skills
    return _skills_call(list_skills)


@router.get("/api/extensions/skills/{scope}/{skill_id}")
async def get_project_skill(scope: str, skill_id: str):
    from src.workflow.workbench.project_skills import get_skill
    return _skills_call(get_skill, scope, skill_id)


@router.put("/api/extensions/skills/{scope}/{skill_id}")
async def save_project_skill(scope: str, skill_id: str, body: dict):
    from src.workflow.workbench.project_skills import save_skill
    return _skills_call(save_skill, scope, skill_id, body or {})


@router.delete("/api/extensions/skills/{scope}/{skill_id}")
async def delete_project_skill(scope: str, skill_id: str, revision: str = ""):
    from src.workflow.workbench.project_skills import delete_skill
    _skills_call(delete_skill, scope, skill_id, revision)
    return {"ok": True}


# ── 精选目录：审核过的 MCP 服务与技能，一键装进当前范围 ──

@router.get("/api/extensions/catalog")
async def extensions_catalog():
    from src.workflow.workbench.catalog import catalog
    return catalog(os.getcwd())


@router.post("/api/extensions/catalog/mcp/{item_id}/install")
async def install_catalog_mcp(item_id: str):
    from src.workflow.workbench.catalog import install_mcp
    return _mcp_config_call(install_mcp, item_id)


@router.post("/api/extensions/catalog/skills/{item_id}/install")
async def install_catalog_skill(item_id: str, body: dict):
    from src.gateway.workspace_scope import GENERAL, current_workspace_scope
    from src.web.deps import HTTPException
    from src.workflow.workbench.catalog import install_skill
    if current_workspace_scope() == GENERAL:      # 私人助理没有项目目录，技能走个人技能（要审阅启用）
        raise HTTPException(409, "私人助理请在「个人技能」里添加；项目技能需要先打开一个项目")
    return _skills_call(install_skill, item_id, str((body or {}).get("scope") or "local"))
