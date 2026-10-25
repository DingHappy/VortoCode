import asyncio
from types import SimpleNamespace
import pytest
from src.agents.agent_loop import MainAgent
from src.agents.tool import Tool
from src.agents.mcp_session import connect_agent, snapshot
from src.utils.exc_utils import add_note

class Manager:
    def __init__(self):
        self.mcp_clients={'fixture':SimpleNamespace(connected=True)}
        self.config={'servers':[{}]};self.closed=False
    async def shutdown(self):self.closed=True
    def list_tools(self):return []

def agent(profile='external'):
    a=MainAgent([]);a._capabilities=SimpleNamespace(profile=profile);return a

def tool(name):return Tool(name,'fixture',{},lambda _:None)

@pytest.mark.asyncio
async def test_retry_replaces_owned_tools_and_closes_old_manager(monkeypatch):
    import src.agents.mcp_tools as module
    a=agent();managers=[]
    async def connect(*args,**kwargs):
        m=Manager();managers.append(m);return m,[tool('mcp__fixture__count')]
    monkeypatch.setattr(module,'connect_mcp',connect)
    for _ in range(2):assert (await connect_agent(a,'.'))['status']=='connected'
    assert [t.name for t in a._tool_list].count('mcp__fixture__count')==1
    assert managers[0].closed and not managers[1].closed
    managers[1].mcp_clients['fixture'].connected=False
    assert snapshot(a)['status']=='error'

@pytest.mark.asyncio
async def test_late_connection_is_not_attached(monkeypatch):
    import src.agents.mcp_tools as module
    a=agent();m=Manager()
    async def connect(*args,**kwargs):return m,[tool('mcp__fixture__count')]
    monkeypatch.setattr(module,'connect_mcp',connect)
    with pytest.raises(RuntimeError):await connect_agent(a,'.',current=lambda:False)
    assert m.closed and 'mcp__fixture__count' not in a.tools
    assert not a.mcp.connecting

@pytest.mark.asyncio
async def test_local_boundary_and_overlapping_requests(monkeypatch):
    import src.agents.mcp_tools as module
    a=agent('local');assert snapshot(a)['status']=='blocked'
    with pytest.raises(PermissionError):await connect_agent(a,'.')
    a=agent();entered=asyncio.Event();release=asyncio.Event();m=Manager()
    async def connect(*args,**kwargs):entered.set();await release.wait();return m,[]
    monkeypatch.setattr(module,'connect_mcp',connect)
    pending=asyncio.create_task(connect_agent(a,'.'));await entered.wait()
    with pytest.raises(RuntimeError):await connect_agent(a,'.')
    release.set();assert (await pending)['status']=='connected'

@pytest.mark.asyncio
async def test_failed_retry_retains_prior_tools_and_redacts_error(monkeypatch):
    import src.agents.mcp_tools as module
    a=agent();m=Manager()
    async def connect(*args,**kwargs):return m,[tool('mcp__fixture__count')]
    monkeypatch.setattr(module,'connect_mcp',connect);await connect_agent(a,'.')
    async def fail(*args,**kwargs):raise ValueError('secret endpoint fixture')
    monkeypatch.setattr(module,'connect_mcp',fail)
    with pytest.raises(ValueError):await connect_agent(a,'.')
    assert a.mcp.manager is m and not m.closed and 'mcp__fixture__count' in a.tools
    assert 'secret' not in snapshot(a)['detail']


def test_general_rest_exception_is_exact_and_does_not_expose_project_inspection():
    from src.gateway.workspace_scope import http_workspace_requirement
    assert http_workspace_requirement('/api/extensions/mcp','general') is None
    assert http_workspace_requirement('/api/extensions/mcp/connect','general') is None
    assert http_workspace_requirement('/api/extensions/inspect','general')=='project'
    assert http_workspace_requirement('/api/extensions/mcp/anything','general')=='project'

@pytest.mark.asyncio
async def test_failed_retry_preserves_catalogue_observation_time(monkeypatch):
    import src.agents.mcp_tools as module
    a=agent();m=Manager()
    async def connect(*args,**kwargs):return m,[tool('mcp__fixture__count')]
    monkeypatch.setattr(module,'connect_mcp',connect);await connect_agent(a,'.')
    a.mcp.observation['observed_at']='2026-01-01T00:00:00+00:00'
    async def fail(*args,**kwargs):raise ValueError('fixture offline')
    monkeypatch.setattr(module,'connect_mcp',fail)
    with pytest.raises(ValueError):await connect_agent(a,'.')
    assert snapshot(a)['observed_at']=='2026-01-01T00:00:00+00:00'
    assert snapshot(a)['attempted_at']!='2026-01-01T00:00:00+00:00'

@pytest.mark.asyncio
async def test_cancel_after_publish_does_not_revert_new_connection_observation(monkeypatch):
    import src.agents.mcp_tools as module
    a=agent();old=Manager();new=Manager();managers=iter([old,new])
    async def connect(*args,**kwargs):return next(managers),[tool('mcp__fixture__count')]
    monkeypatch.setattr(module,'connect_mcp',connect);await connect_agent(a,'.')
    a.mcp.observation['observed_at']='2026-01-01T00:00:00+00:00'
    entered=asyncio.Event()
    async def blocked():entered.set();await asyncio.Event().wait()
    old.shutdown=blocked
    task=asyncio.create_task(connect_agent(a,'.'));await entered.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert a.mcp.manager is new and snapshot(a)['status']=='connected'
    assert snapshot(a)['observed_at']!='2026-01-01T00:00:00+00:00'
    assert '清理未确认' in snapshot(a)['detail']
    assert not a.mcp.connecting

@pytest.mark.asyncio
async def test_failed_candidate_cleanup_cannot_leave_retry_latched(monkeypatch):
    import src.agents.mcp_tools as module
    a=agent();m=Manager()
    async def connect(*args,**kwargs):return m,[]
    async def fail_cleanup():raise ValueError('cleanup failed')
    m.shutdown=fail_cleanup;monkeypatch.setattr(module,'connect_mcp',connect)
    with pytest.raises(RuntimeError):await connect_agent(a,'.',current=lambda:False)
    assert not a.mcp.connecting and '清理未确认' in snapshot(a)['detail']


@pytest.mark.asyncio
async def test_cancel_during_candidate_cleanup_records_unknown_and_releases_retry(monkeypatch):
    import src.agents.mcp_tools as module
    a=agent();m=Manager();entered=asyncio.Event()
    async def connect(*args,**kwargs):return m,[]
    async def blocked_cleanup():entered.set();await asyncio.Event().wait()
    m.shutdown=blocked_cleanup;monkeypatch.setattr(module,'connect_mcp',connect)
    task=asyncio.create_task(connect_agent(a,'.',current=lambda:False))
    await entered.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert not a.mcp.connecting and '清理未确认' in snapshot(a)['detail']
    assert not m.closed and a.mcp.manager is None


@pytest.mark.asyncio
async def test_factory_cleanup_unknown_is_visible_without_exposing_error(monkeypatch):
    import src.agents.mcp_tools as module
    a=agent()
    error=ValueError('secret service detail');add_note(error, 'MCP cleanup unconfirmed')
    async def fail(*args,**kwargs):raise error
    monkeypatch.setattr(module,'connect_mcp',fail)
    with pytest.raises(ValueError):await connect_agent(a,'.')
    assert not a.mcp.connecting and '清理未确认' in snapshot(a)['detail']
    assert 'secret' not in snapshot(a)['detail']
