"""MCP server identity must survive collisions and failed catalogue refreshes."""
import pytest
from src.agents.gate import make_confirm_gate
from src.agents.mcp_tools import wrap_mcp_manager
from src.tools.registry import DynamicToolRegistry, Tool
from src.tools.mcp_client import MCPTool, MCPToolResult, MCPTransport
from src.tools.executor import ToolExecutor
from src.tools.permission import ToolPermissionManager, PermissionRule, PermissionAction

class Client:
    def __init__(self, server):
        self.transport=MCPTransport.HTTP
        self.server_name=server; self.names=['search']; self.calls=[]; self.closed=False
    async def connect(self): return True
    async def disconnect(self): self.closed=True
    async def list_tools(self):
        return [MCPTool(name=n,server_name=self.server_name) for n in self.names]
    async def call_tool(self, call):
        self.calls.append(call.tool_name)
        return MCPToolResult(success=True,output=self.server_name)

@pytest.mark.asyncio
async def test_same_name_servers_do_not_overwrite_or_cross_route():
    r=DynamicToolRegistry();a=Client('alpha');b=Client('beta')
    assert await r.register_mcp_server(a)
    assert await r.register_mcp_server(b)
    assert len(r.list_tools())==2
    assert r.get('search') is None
    assert (await r.call_tool('mcp__alpha__search',{})).output=='alpha'
    assert (await r.call_tool('mcp__beta__search',{})).output=='beta'
    assert a.calls==b.calls==['search']
    await r.unregister_mcp_server('alpha')
    assert r.get('search').server_name=='beta'

@pytest.mark.asyncio
async def test_failed_refresh_preserves_catalogue():
    r=DynamicToolRegistry();a=Client('alpha');await r.register_mcp_server(a)
    async def fail(): raise RuntimeError('offline')
    a.list_tools=fail
    assert await r.refresh_server_tools('alpha') is False
    assert r.get('mcp__alpha__search') is not None

@pytest.mark.asyncio
async def test_identity_keeps_raw_permission_rules_and_alias_rate_counts():
    r=DynamicToolRegistry();a=Client('alpha');await r.register_mcp_server(a)
    pm=ToolPermissionManager();pm.add_rule(PermissionRule(id='deny',tool_names=['search'],action=PermissionAction.DENY,priority=200))
    ex=ToolExecutor(r,pm,confirm=make_confirm_gate(auto_approve=True));ex.mcp_clients={'alpha':a}
    assert not (await ex.execute('mcp__alpha__search',{})).success
    assert a.calls==[]
    pm.add_rule(PermissionRule(id='allow',tool_names=['search'],server_names=['alpha'],action=PermissionAction.ALLOW,priority=300))
    assert (await ex.execute('search',{})).success
    assert (await ex.execute('mcp__alpha__search',{})).success
    assert a.calls==['search','search']
    assert 'search' not in pm.call_counts

@pytest.mark.asyncio
async def test_wrapper_uses_one_prefix_and_original_route():
    r=DynamicToolRegistry();a=Client('alpha');await r.register_mcp_server(a)
    class Manager:
        def list_tools(self):return r.list_tools()
        async def execute_tool(self,name,args):return await r.call_tool(name,args)
    tool=wrap_mcp_manager(Manager())[0]
    assert tool.name=='mcp__alpha__search'
    assert await tool.handler({})=='alpha'


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [["search","search"],["bad__name"],["a"*65]])
async def test_invalid_catalogue_is_not_partially_published(names):
    r=DynamicToolRegistry();a=Client('alpha');a.names=names
    assert not await r.register_mcp_server(a)
    assert r.list_tools()==[] and not r.mcp_clients and a.closed

@pytest.mark.asyncio
async def test_duplicate_server_does_not_replace_original_client():
    r=DynamicToolRegistry();a=Client('alpha');b=Client('alpha')
    assert await r.register_mcp_server(a)
    assert not await r.register_mcp_server(b)
    assert r.mcp_clients['alpha'] is a and not a.closed

@pytest.mark.asyncio
async def test_refresh_does_not_delete_another_server_or_local_tool():
    r=DynamicToolRegistry();a=Client('alpha');b=Client('beta')
    r.register(Tool(name='search',category='read'))
    await r.register_mcp_server(a);await r.register_mcp_server(b)
    a.names=['fetch'];assert await r.refresh_server_tools('alpha')
    assert r.get('search').category=='read'
    assert r.get('mcp__beta__search').server_name=='beta'
    assert r.get('mcp__alpha__search') is None
    assert r.get('mcp__alpha__fetch').server_name=='alpha'

@pytest.mark.asyncio
async def test_late_refresh_cannot_publish_after_disconnect():
    import asyncio
    r=DynamicToolRegistry();a=Client('alpha');await r.register_mcp_server(a)
    entered=asyncio.Event();release=asyncio.Event()
    async def slow():
        entered.set();await release.wait()
        return [MCPTool(name='late',server_name='alpha')]
    a.list_tools=slow
    pending=asyncio.create_task(r.refresh_server_tools('alpha'))
    await entered.wait();await r.unregister_mcp_server('alpha');release.set()
    assert await pending is False
    assert r.list_tools()==[]

@pytest.mark.asyncio
async def test_qualified_server_permission_does_not_authorize_other_source():
    r=DynamicToolRegistry();a=Client('alpha');b=Client('beta')
    await r.register_mcp_server(a);await r.register_mcp_server(b)
    pm=ToolPermissionManager();pm.add_rule(PermissionRule(id='alpha-only',tool_names=['mcp__alpha__search'],action=PermissionAction.ALLOW,priority=200))
    ex=ToolExecutor(r,pm,confirm=make_confirm_gate(auto_approve=True));ex.mcp_clients={'alpha':a,'beta':b}
    assert (await ex.execute('mcp__alpha__search',{})).success
    assert not (await ex.execute('mcp__beta__search',{})).success
    assert not (await ex.execute('search',{})).success
    assert b.calls==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('status,data', [(503,{}),(200,{}),(200,{'tools':'broken'})])
async def test_http_catalogue_errors_are_not_empty_success(status,data):
    from src.tools.mcp_client import HTTPMCPClient
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def json(self):return data
    response=Response();response.status=status
    class Session:
        def get(self,*args):return response
    client=HTTPMCPClient('alpha','http://fixture.invalid');client.session=Session();client.connected=True
    with pytest.raises((RuntimeError,ValueError)):
        await client.list_tools()

@pytest.mark.asyncio
async def test_invalid_refresh_is_atomic_and_explicit_empty_is_allowed():
    r=DynamicToolRegistry();a=Client('alpha');await r.register_mcp_server(a)
    a.names=['fetch','fetch'];assert not await r.refresh_server_tools('alpha')
    assert r.get('mcp__alpha__search') is not None
    a.names=[];assert await r.refresh_server_tools('alpha')
    assert r.list_tools()==[] and r.mcp_clients['alpha'] is a

@pytest.mark.asyncio
async def test_empty_catalogue_still_requires_valid_server_identity():
    r=DynamicToolRegistry();a=Client('bad__server');a.names=[]
    assert not await r.register_mcp_server(a)
    assert not r.mcp_clients and a.closed

@pytest.mark.asyncio
async def test_refresh_cannot_publish_while_disconnect_is_waiting():
    import asyncio
    r=DynamicToolRegistry();a=Client('alpha');await r.register_mcp_server(a)
    reading=asyncio.Event();release_read=asyncio.Event()
    closing=asyncio.Event();release_close=asyncio.Event()
    async def slow_list():
        reading.set();await release_read.wait()
        return [MCPTool(name='late',server_name='alpha')]
    async def slow_close():
        closing.set();await release_close.wait()
    a.list_tools=slow_list;a.disconnect=slow_close
    refresh=asyncio.create_task(r.refresh_server_tools('alpha'));await reading.wait()
    unregister=asyncio.create_task(r.unregister_mcp_server('alpha'));await closing.wait()
    release_read.set()
    try:
        assert await refresh is False and r.list_tools()==[]
    finally:
        release_close.set();await unregister
