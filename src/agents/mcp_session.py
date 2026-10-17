"""Session-owned MCP attachment, explicit retries and redacted observations."""
import asyncio
from datetime import datetime, timezone
from src.utils.async_ops import timeout_scope


def snapshot(agent):
    profile = agent._capabilities.profile
    state = getattr(agent, '_mcp_observation', None)
    manager = getattr(agent, '_mcp_mgr', None)
    if state is None and manager is not None:
        state = {'status': 'connected', 'detail': '此前已接入工具；尚未记录显式观察时间。',
                 'tool_count': len(manager.list_tools()), 'observed_at': None}
    if state and state['status'] in ('connected', 'partial') and manager is not None:
        alive = sum(bool(c.connected) for c in manager.mcp_clients.values())
        if alive < len(manager.mcp_clients):
            state = {**state, 'status': 'partial' if alive else 'error',
                     'detail': '服务连接已失效；原工具记录保留，请手动重试。'}
    if profile not in ('external', 'unattended'):
        return {'status': 'blocked', 'detail': '当前项目会话不允许外部 MCP；请使用 external 会话。',
                'tool_count': 0, 'observed_at': None, 'profile': profile}
    return {**(state or {'status': 'disconnected', 'detail': '尚未连接；连接只读取配置和目录，不调用模型或工具。',
                        'tool_count': 0, 'observed_at': None}), 'profile': profile}


async def connect_agent(agent, root, *, current=lambda: True, confirm=None, workspace_scope="project", no_config_ok=False):
    """Attach an explicitly requested catalogue; scope comes from trusted entrypoint assembly.

    Named credential connectors are exclusive to General, just as in the Web
    opt-in path. Missing/unknown scope must never inherit process environment.
    """
    from src.agents.mcp_tools import connect_mcp, MCP_CLEANUP_TIMEOUT, MCP_CLEANUP_UNCONFIRMED
    from src.gateway.workspace_scope import GENERAL, normalize_workspace_scope
    if getattr(agent, '_mcp_connecting', False):
        raise RuntimeError('MCP connection already in progress')
    if agent._capabilities.profile not in ('external', 'unattended'):
        raise PermissionError('当前会话不允许外部 MCP')
    agent._mcp_connecting = True
    revision = getattr(agent, '_mcp_revision', 0)
    published_revision = None
    previous = snapshot(agent)
    agent._mcp_observation = {**previous, 'status': 'connecting', 'detail': '正在连接并读取工具目录…'}
    manager = None
    committed = False
    try:
        async with timeout_scope(30):
            manager, tools = await connect_mcp(
                root, capability_profile=agent._capabilities.profile,
                confirm=getattr(agent, '_confirm_gate', None) or confirm,
                allow_connectors=normalize_workspace_scope(workspace_scope) == GENERAL,
            )
        if not current() or getattr(agent, '_mcp_revision', 0) != revision:
            raise RuntimeError('会话正在执行或已切换；未接入新工具，请稍后重试')
        if manager is None:
            if no_config_ok:
                agent._mcp_observation = previous
                return snapshot(agent)
            raise RuntimeError('没有可连接的配置，请检查启用状态、HTTP 限制及连接器凭据')
        if not manager.mcp_clients:
            raise RuntimeError('配置中的服务均未连接成功，请检查协议版本及服务状态')
        old = getattr(agent, '_mcp_mgr', None)
        old_names = getattr(agent, '_mcp_tool_names', set())
        if old is not None and not old_names:
            from src.agents.mcp_tools import wrap_mcp_manager
            old_names = {t.name for t in wrap_mcp_manager(old)}
        agent._tool_list = [t for t in agent._tool_list if t.name not in old_names]
        for name in old_names:
            agent.tools.pop(name, None)
        agent.add_tools(tools)
        agent._mcp_mgr = manager
        agent._mcp_tool_names = {t.name for t in tools}
        agent._mcp_tried = True
        published_revision = agent._mcp_revision = revision + 1
        connected = len(manager.mcp_clients)
        configured = len(manager.config.get('servers', []))
        agent._mcp_observation = {'status': 'connected' if connected == configured else 'partial',
                                 'detail': f'{connected}/{configured} 个服务已连接；目录读取成功不代表工具执行验收。',
                                 'tool_count': len(tools), 'observed_at': datetime.now(timezone.utc).isoformat()}
        committed = True
        manager = None  # Ownership transferred to the original session cleanup path.
        if old is not None:
            try:
                async with timeout_scope(MCP_CLEANUP_TIMEOUT):
                    await old.shutdown()
            except Exception:
                if getattr(agent, '_mcp_revision', 0) == published_revision:
                    agent._mcp_observation['detail'] += ' 旧连接清理未确认。'
        return snapshot(agent)
    except BaseException as error:
        if committed:
            if getattr(agent, '_mcp_revision', 0) == published_revision:
                agent._mcp_observation["detail"] += " 旧连接清理未确认。"
            raise
        if getattr(agent, '_mcp_revision', 0) == revision:
            agent._mcp_observation = {**previous, 'status': 'error' if not getattr(agent, '_mcp_mgr', None) else previous['status'],
                                     'detail': '连接未更新；请检查配置、协议版本和服务状态后重试。',
                                     'attempted_at': datetime.now(timezone.utc).isoformat()}
            if MCP_CLEANUP_UNCONFIRMED in getattr(error, '__notes__', ()):
                agent._mcp_observation['detail'] += ' 新连接清理未确认。'
        raise
    finally:
        try:
            if manager is not None:
                try:
                    async with timeout_scope(MCP_CLEANUP_TIMEOUT):
                        await manager.shutdown()
                except asyncio.CancelledError:
                    if getattr(agent, '_mcp_revision', 0) == revision:
                        agent._mcp_observation['detail'] += ' 新连接清理未确认。'
                    raise
                except Exception:
                    if getattr(agent, '_mcp_revision', 0) == revision:
                        agent._mcp_observation['detail'] += ' 新连接清理未确认。'
        finally:
            agent._mcp_connecting = False


def detach_connector(agent, revocation) -> int | None:
    """Drop exact source tools now; fence any candidate read before this revocation."""
    manager = getattr(agent, '_mcp_mgr', None)
    servers = {server for entry in revocation.managers if entry.manager is manager for server in entry.servers}
    if not servers and not getattr(agent, '_mcp_connecting', False):
        return None
    revision = agent._mcp_revision = getattr(agent, '_mcp_revision', 0) + 1
    prefixes = tuple(f'mcp__{server}__' for server in servers)
    removed = {tool.name for tool in agent._tool_list if prefixes and tool.name.startswith(prefixes)}
    agent._tool_list = [tool for tool in agent._tool_list if tool.name not in removed]
    for name in removed:
        agent.tools.pop(name, None)
    agent._mcp_tool_names = set(getattr(agent, '_mcp_tool_names', ())) - removed
    agent._mcp_tried = True
    state = snapshot(agent)
    state.update(status='partial' if manager and manager.mcp_clients else 'disconnected',
                 tool_count=len(agent._mcp_tool_names), cleanup_status='pending',
                 detail='目标连接器后续调用已禁用；其余服务保留。正在清理本地连接，远端已发请求未确认。')
    agent._mcp_observation = state
    return revision


def finish_connector_detach(agent, revision: int, cleanup_status: str):
    if getattr(agent, '_mcp_revision', 0) != revision:
        return
    agent._mcp_observation.update(cleanup_status=cleanup_status,
        detail=('目标连接器后续调用已禁用；本地连接清理已确认。' if cleanup_status == 'confirmed'
                else '目标连接器后续调用已禁用；本地连接清理未确认，请核对运行实例及凭据清理状态。')
               + ' 远端已发请求及供应商授权未确认。')
