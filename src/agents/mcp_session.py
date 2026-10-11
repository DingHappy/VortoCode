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


async def connect_agent(agent, root, *, current=lambda: True, confirm=None):
    from src.agents.mcp_tools import connect_mcp, MCP_CLEANUP_TIMEOUT, MCP_CLEANUP_UNCONFIRMED
    if getattr(agent, '_mcp_connecting', False):
        raise RuntimeError('MCP connection already in progress')
    if agent._capabilities.profile not in ('external', 'unattended'):
        raise PermissionError('当前会话不允许外部 MCP')
    agent._mcp_connecting = True
    previous = snapshot(agent)
    agent._mcp_observation = {**previous, 'status': 'connecting', 'detail': '正在连接并读取工具目录…'}
    manager = None
    committed = False
    try:
        async with timeout_scope(30):
            manager, tools = await connect_mcp(root, capability_profile=agent._capabilities.profile, confirm=confirm)
        if not current():
            raise RuntimeError('会话正在执行或已切换；未接入新工具，请稍后重试')
        if manager is None:
            raise RuntimeError('没有可连接的配置，请检查启用状态及无凭据 HTTP 限制')
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
                agent._mcp_observation['detail'] += ' 旧连接清理未确认。' 
        return snapshot(agent)
    except BaseException as error:
        if committed:
            agent._mcp_observation["detail"] += " 旧连接清理未确认。"
            raise
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
                    agent._mcp_observation['detail'] += ' 新连接清理未确认。'
                    raise
                except Exception:
                    agent._mcp_observation['detail'] += ' 新连接清理未确认。'
        finally:
            agent._mcp_connecting = False
