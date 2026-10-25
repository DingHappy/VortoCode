"""Session-owned MCP attachment, explicit retries and redacted observations."""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from src.utils.async_ops import timeout_scope


@dataclass
class McpAttachment:
    """One session's MCP state. MainAgent owns the instance; only this module mutates it."""
    manager: Any = None
    tool_names: set = field(default_factory=set)
    tried: bool = False
    connecting: bool = False
    revision: int = 0
    observation: dict | None = None


def session_manager(agent):
    """The live manager for session cleanup; tolerant of agents without MCP state."""
    attachment = getattr(agent, 'mcp', None)
    return attachment.manager if attachment is not None else None


def claim_auto_attach(agent) -> bool:
    """Opt-in auto attach runs at most once per session and never during an explicit connect."""
    mcp = getattr(agent, 'mcp', None)
    if mcp is None or mcp.connecting or mcp.tried:
        return False
    mcp.tried = True
    return True


def snapshot(agent):
    profile = agent._capabilities.profile
    state = agent.mcp.observation
    manager = agent.mcp.manager
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
    mcp = agent.mcp
    if mcp.connecting:
        raise RuntimeError('MCP connection already in progress')
    if agent._capabilities.profile not in ('external', 'unattended'):
        raise PermissionError('当前会话不允许外部 MCP')
    mcp.connecting = True
    revision = mcp.revision
    published_revision = None
    previous = snapshot(agent)
    mcp.observation = {**previous, 'status': 'connecting', 'detail': '正在连接并读取工具目录…'}
    manager = None
    committed = False
    try:
        async with timeout_scope(30):
            manager, tools = await connect_mcp(
                root, capability_profile=agent._capabilities.profile,
                confirm=agent.confirm_gate or confirm,
                allow_connectors=normalize_workspace_scope(workspace_scope) == GENERAL,
            )
        if not current() or mcp.revision != revision:
            raise RuntimeError('会话正在执行或已切换；未接入新工具，请稍后重试')
        if manager is None:
            if no_config_ok:
                mcp.observation = previous
                return snapshot(agent)
            raise RuntimeError('没有可连接的配置，请检查启用状态、HTTP 限制及连接器凭据')
        if not manager.mcp_clients:
            raise RuntimeError('配置中的服务均未连接成功，请检查协议版本及服务状态')
        old = mcp.manager
        old_names = mcp.tool_names
        if old is not None and not old_names:
            from src.agents.mcp_tools import wrap_mcp_manager
            old_names = {t.name for t in wrap_mcp_manager(old)}
        agent.remove_tools(old_names)
        agent.add_tools(tools)
        mcp.manager = manager
        mcp.tool_names = {t.name for t in tools}
        mcp.tried = True
        published_revision = mcp.revision = revision + 1
        connected = len(manager.mcp_clients)
        configured = len(manager.config.get('servers', []))
        mcp.observation = {'status': 'connected' if connected == configured else 'partial',
                           'detail': f'{connected}/{configured} 个服务已连接；目录读取成功不代表工具执行验收。',
                           'tool_count': len(tools), 'observed_at': datetime.now(timezone.utc).isoformat()}
        committed = True
        manager = None  # Ownership transferred to the original session cleanup path.
        if old is not None:
            try:
                async with timeout_scope(MCP_CLEANUP_TIMEOUT):
                    await old.shutdown()
            except Exception:
                if mcp.revision == published_revision:
                    mcp.observation['detail'] += ' 旧连接清理未确认。'
        return snapshot(agent)
    except BaseException as error:
        if committed:
            if mcp.revision == published_revision:
                mcp.observation["detail"] += " 旧连接清理未确认。"
            raise
        if mcp.revision == revision:
            mcp.observation = {**previous, 'status': 'error' if not mcp.manager else previous['status'],
                               'detail': '连接未更新；请检查配置、协议版本和服务状态后重试。',
                               'attempted_at': datetime.now(timezone.utc).isoformat()}
            if MCP_CLEANUP_UNCONFIRMED in getattr(error, '__notes__', ()):
                mcp.observation['detail'] += ' 新连接清理未确认。'
        raise
    finally:
        try:
            if manager is not None:
                try:
                    async with timeout_scope(MCP_CLEANUP_TIMEOUT):
                        await manager.shutdown()
                except asyncio.CancelledError:
                    if mcp.revision == revision:
                        mcp.observation['detail'] += ' 新连接清理未确认。'
                    raise
                except Exception:
                    if mcp.revision == revision:
                        mcp.observation['detail'] += ' 新连接清理未确认。'
        finally:
            mcp.connecting = False


def detach_connector(agent, revocation) -> int | None:
    """Drop exact source tools now; fence any candidate read before this revocation."""
    mcp = agent.mcp
    manager = mcp.manager
    servers = {server for entry in revocation.managers if entry.manager is manager for server in entry.servers}
    if not servers and not mcp.connecting:
        return None
    revision = mcp.revision = mcp.revision + 1
    prefixes = tuple(f'mcp__{server}__' for server in servers)
    removed = agent.remove_tools([name for name in agent.tools if prefixes and name.startswith(prefixes)])
    mcp.tool_names = set(mcp.tool_names) - removed
    mcp.tried = True
    state = snapshot(agent)
    state.update(status='partial' if manager and manager.mcp_clients else 'disconnected',
                 tool_count=len(mcp.tool_names), cleanup_status='pending',
                 detail='目标连接器后续调用已禁用；其余服务保留。正在清理本地连接，远端已发请求未确认。')
    mcp.observation = state
    return revision


def finish_connector_detach(agent, revision: int, cleanup_status: str):
    if agent.mcp.revision != revision:
        return
    agent.mcp.observation.update(cleanup_status=cleanup_status,
        detail=('目标连接器后续调用已禁用；本地连接清理已确认。' if cleanup_status == 'confirmed'
                else '目标连接器后续调用已禁用；本地连接清理未确认，请核对运行实例及凭据清理状态。')
               + ' 远端已发请求及供应商授权未确认。')
