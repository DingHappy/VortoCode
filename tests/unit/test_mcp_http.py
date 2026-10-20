"""Bounded Streamable HTTP wire contracts using an owned loopback server."""
import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from src.agents.gate import make_confirm_gate
from src.tools.mcp_http import StreamableHTTPMCPClient, VERSION, header_paths, header_value
from src.tools.mcp_client import MCPToolCall, MCPTransport, create_mcp_client

SCHEMA={'type':'object','properties':{'count':{'type':'integer','x-mcp-header':'Count'}},'required':['count']}

@pytest.fixture
def endpoint():
    state={'calls':[],'mode':'json'}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            p=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['calls'].append((p,dict(self.headers)))
            if p['method'].startswith('notifications/'):
                self.send_response(202);self.send_header('Content-Length','0');self.end_headers();return
            if p['method']=='initialize':
                result={'protocolVersion':p['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}}
            elif p['method']=='tools/list':
                result={'tools':[{'name':'count','inputSchema':SCHEMA}]} if 'cursor' not in p['params'] else {'tools':[]}
                if 'cursor' not in p['params']:result['nextCursor']='page2'
            else:result={'content':[{'type':'text','text':'wire verified'}],'isError':p['params'].get('arguments',{}).get('count')==999}
            msg={'jsonrpc':'2.0','id':p['id'],'result':result}
            status=200;mime='application/json';body=json.dumps(msg).encode()
            if state['mode']=='sse':
                mime='text/event-stream'
                body=b': keepalive\r\n\r\ndata: '+json.dumps({'jsonrpc':'2.0','method':'notifications/progress','params':{}}).encode()+b'\r\n\r\ndata: '+body+b'\r\n\r\n'
            if state.get('reply'):status,mime,body=state['reply'](p,msg)
            self.send_response(status)
            if p['method']=='initialize' and state.get('session_id'):
                self.send_header('MCP-Session-Id',state['session_id'])
            self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_DELETE(self):
            state.setdefault('deletes',[]).append(dict(self.headers))
            self.send_response(204);self.send_header('Content-Length','0');self.end_headers()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=lambda:server.serve_forever(poll_interval=.01),daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}/mcp',state
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize('version', ['2025-06-18', VERSION])
@pytest.mark.parametrize('server_error', [False, True])
async def test_structured_result_survives_original_manager_executor_and_agent_wrapper(endpoint, tmp_path, version, server_error):
    from src.agents.mcp_tools import connect_mcp
    import yaml
    url, state = endpoint
    structured = {'count': 2, 'matched': True, 'next': None}
    content = [{'type': 'text', 'text': '结构化结果'},
               {'type': 'resource_link', 'uri': 'https://resource.example.test/item', 'name': 'fixture'}]
    def reply(payload, message):
        if payload['method'] == 'tools/call':
            message['result'] = {'content': content, 'structuredContent': structured, 'isError': server_error}
        return 200, 'application/json', json.dumps(message).encode()
    state['reply'] = reply
    config = tmp_path / 'config'; config.mkdir()
    (config / 'mcp.yaml').write_text(yaml.safe_dump({
        'servers': [{'name': 'fixture', 'transport': 'http', 'protocol': 'streamable_http',
                     'protocol_version': version, 'url': url, 'credentialed': False}],
        'permissions': {'rules': [{'id': 'fixture-count', 'scope': 'global', 'action': 'allow',
                                   'server_names': ['fixture'], 'tool_names': ['count'], 'priority': 100}]}}))
    manager, tools = await connect_mcp(str(tmp_path), capability_profile='external', confirm=make_confirm_gate(auto_approve=True))
    try:
        result = await tools[0].handler({'count': 2})
        if server_error:
            assert result['ok'] is False
        else:
            assert json.loads(result) == {'content': content, 'structuredContent': structured}
        assert sum(payload['method'] == 'tools/call' for payload, _ in state['calls']) == 1
        assert tools[0].untrusted_source and tools[0].external_content and not tools[0].read_only
    finally:
        await manager.shutdown()

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['json','sse'])
async def test_exact_endpoint_metadata_pagination_and_error_state(endpoint,mode):
    url,state=endpoint;state['mode']=mode
    client=StreamableHTTPMCPClient('fixture',url)
    try:
        assert await client.connect()
        assert [t.name for t in await client.list_tools()]==['count']
        assert (await client.call_tool(MCPToolCall(tool_name='count',arguments={'count':2}))).success
        assert not (await client.call_tool(MCPToolCall(tool_name='count',arguments={'count':999}))).success
        assert len(state['calls'])==4
        assert state['calls'][1][0]['params']['cursor']=='page2'
        for p,h in state['calls']:
            assert h['MCP-Protocol-Version']==p['params']['_meta']['io.modelcontextprotocol/protocolVersion']==VERSION
            assert h['Mcp-Method']==p['method']
            assert 'application/json' in h['Accept'] and 'text/event-stream' in h['Accept']
        assert state['calls'][2][1]['Mcp-Name']=='count'
        assert state['calls'][2][1]['Mcp-Param-Count']=='2'
    finally:await client.disconnect()
    assert not client.connected and client.session is None


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['json', 'sse'])
@pytest.mark.parametrize('server_error', [False, True])
async def test_inline_media_crosses_http_manager_and_original_image_queue(endpoint, tmp_path, mode, server_error):
    import base64
    import yaml
    from src.agents.agent_loop import MainAgent, _to_native_messages
    from src.agents.mcp_tools import connect_mcp
    from src.agents.taint import reset_taint, is_tainted
    url, state = endpoint
    encoded = base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode()
    def reply(payload, message):
        if payload['method'] == 'tools/call':
            message['result'] = {'content': [{'type': 'text', 'text': 'fixture screenshot'},
                                            {'type': 'image', 'mimeType': 'image/png', 'data': encoded}],
                                 'structuredContent': {'captured': True}, 'isError': server_error}
        body = json.dumps(message).encode()
        return (200, 'text/event-stream', b'data: ' + body + b'\n\n') if mode == 'sse' else (200, 'application/json', body)
    state['reply'] = reply
    config = tmp_path / 'config'; config.mkdir()
    (config / 'mcp.yaml').write_text(yaml.safe_dump({
        'servers': [{'name': 'fixture', 'transport': 'http', 'protocol': 'streamable_http',
                     'protocol_version': '2025-06-18', 'url': url, 'credentialed': False}],
        'permissions': {'rules': [{'id': 'fixture-count', 'scope': 'global', 'action': 'allow',
                                   'server_names': ['fixture'], 'tool_names': ['count'], 'priority': 100}]}}))
    manager, tools = await connect_mcp(str(tmp_path), capability_profile='external', confirm=make_confirm_gate(auto_approve=True))
    traces = []
    agent = MainAgent(tools, on_tool_event=lambda *args: traces.append(args))
    reset_taint()
    try:
        result = await agent._run_tools([(tools[0].name, {'count': 2})], 'build', lambda _: None)
        assert encoded not in str(result) and encoded not in json.dumps(traces) and is_tainted()
        agent._flush_pending_images()
        if server_error:
            assert not agent.history and any(args[1]['status'] == 'failed' for args in traces if args[0] == 'finish')
        else:
            assert '"captured": true' in result[0][1]
            assert _to_native_messages(agent.history)[-1]['content'][1]['image_url']['url'] == 'data:image/png;base64,' + encoded
        assert sum(payload['method'] == 'tools/call' for payload, _ in state['calls']) == 1
    finally:
        reset_taint()
        await manager.shutdown()

@pytest.mark.asyncio
@pytest.mark.parametrize('defect',['wrong_id','rpc_error','malformed','oversized','redirect','mrtr','server_request','unfinished'])
async def test_wire_failures_never_retry_or_become_empty_catalogues(endpoint,defect):
    url,state=endpoint
    def reply(p,msg):
        status=200;mime='application/json'
        if defect=='wrong_id':msg['id']+=1
        if defect=='rpc_error':msg={'jsonrpc':'2.0','id':p['id'],'error':{'code':-32601,'message':'unsupported'}}
        if defect=='mrtr':msg['result']={'inputRequests':[]}
        if defect=='server_request':msg={'jsonrpc':'2.0','id':p['id'],'method':'sampling/createMessage'}
        body=json.dumps(msg).encode()
        if defect=='malformed':body=b'{'
        if defect=='oversized':body=b' '*1048577
        if defect=='redirect':status=307
        if defect=='unfinished':mime='text/event-stream';body=b': keepalive\n\n'
        return status,mime,body
    state['reply']=reply;client=StreamableHTTPMCPClient('fixture',url)
    try:
        await client.connect()
        with pytest.raises((ValueError,RuntimeError)):
            await client.list_tools()
        assert len(state['calls'])==1 and client.tools==[]
    finally:await client.disconnect()

@pytest.mark.asyncio
async def test_repeated_cursor_and_page_budget_preserve_old_catalogue(endpoint):
    url,state=endpoint;client=StreamableHTTPMCPClient('fixture',url)
    try:
        await client.connect();old=await client.list_tools()
        def reply(p,msg):
            msg['result']={'tools':[],'nextCursor':'repeat'}
            return 200,'application/json',json.dumps(msg).encode()
        state['reply']=reply
        with pytest.raises(ValueError,match='cursor'):await client.list_tools()
        assert client.tools is old
        def endless(p,msg):
            msg['result']={'tools':[],'nextCursor':str(p['id'])}
            return 200,'application/json',json.dumps(msg).encode()
        state['reply']=endless
        with pytest.raises(ValueError,match='page budget'):await client.list_tools()
        assert client.tools is old
    finally:await client.disconnect()

@pytest.mark.asyncio
async def test_unknown_tool_and_invalid_header_argument_send_nothing(endpoint):
    url,state=endpoint;client=StreamableHTTPMCPClient('fixture',url)
    try:
        await client.connect();await client.list_tools();n=len(state['calls'])
        for name,value in [('missing',1),('count',True),('count',2**53),('count',1.2)]:
            result=await client.call_tool(MCPToolCall(tool_name=name,arguments={'count':value}))
            assert not result.success
        assert len(state['calls'])==n
    finally:await client.disconnect()

@pytest.mark.parametrize('schema',[
    {'properties':{'x':{'type':'string','x-mcp-header':'bad\nName'}}},
    {'properties':{'x':{'type':'number','x-mcp-header':'X'}}},
    {'items':{'type':'string','x-mcp-header':'X'}},
    {'properties':{'x':{'type':'string','x-mcp-header':'X'},'y':{'type':'string','x-mcp-header':'x'}}},
])
def test_header_annotation_constraints(schema):
    with pytest.raises(ValueError):header_paths(schema)

@pytest.mark.parametrize('value',['中文',' padded ','line\nbreak','=?base64?abc?='])
def test_header_sentinel_avoids_injection_and_ambiguity(value):
    import base64
    encoded=header_value(value)
    assert encoded.startswith('=?base64?') and encoded.endswith('?=')
    assert base64.b64decode(encoded[9:-2]).decode()==value


def test_protocol_selection_is_explicit_and_fail_closed():
    from src.tools.mcp_client import HTTPMCPClient
    assert isinstance(create_mcp_client('x',MCPTransport.HTTP,base_url='http://fixture.invalid'),HTTPMCPClient)
    assert isinstance(create_mcp_client('x',MCPTransport.HTTP,base_url='http://fixture.invalid',protocol='streamable_http'),StreamableHTTPMCPClient)
    with pytest.raises(ValueError):create_mcp_client('x',MCPTransport.HTTP,base_url='http://fixture.invalid',protocol='typo')
    with pytest.raises(ValueError):StreamableHTTPMCPClient('x','http://fixture.invalid',protocol_version='2024-11-05')

@pytest.mark.asyncio
async def test_timeout_and_cancel_close_response_without_retry(endpoint):
    from threading import Event
    url,state=endpoint;entered=Event();release=Event()
    def blocked(p,msg):
        entered.set();release.wait(timeout=30)
        return 200,'application/json',json.dumps(msg).encode()
    state['reply']=blocked;client=StreamableHTTPMCPClient('fixture',url,timeout=.05)
    try:
        await client.connect()
        with pytest.raises(TimeoutError):await client.list_tools()
        assert len(state['calls'])==1
        # 超时给足：慢的 CI 上取消晚到时，过了期限的计时器会把取消改报成 TimeoutError（2026-10-20 CI 偶发红）
        client.timeout=60
        task=asyncio.create_task(client.list_tools())
        for _ in range(1000):
            if len(state['calls'])==2:break
            await asyncio.sleep(.01)
        assert len(state['calls'])==2
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert len(state['calls'])==2
    finally:release.set();await client.disconnect()

@pytest.mark.asyncio
async def test_cr_only_sse_and_malformed_annotation_exclusion(endpoint):
    url,state=endpoint
    def reply(p,msg):
        msg['result']={'tools':[
            {'name':'bad','inputSchema':{'properties':{'x':{'type':'number','x-mcp-header':'X'}}}},
            {'name':'count','inputSchema':SCHEMA},
        ]}
        return 200,'text/event-stream',b'data: '+json.dumps(msg).encode()+b'\r\r'
    state['reply']=reply;client=StreamableHTTPMCPClient('fixture',url)
    try:
        await client.connect()
        assert [t.name for t in await client.list_tools()]==['count']
    finally:await client.disconnect()

@pytest.mark.asyncio
async def test_duplicate_tool_and_count_budget_fail_closed(endpoint):
    url,state=endpoint;client=StreamableHTTPMCPClient('fixture',url)
    try:
        await client.connect()
        for duplicate in (True,False):
            def reply(p,msg):
                msg['result']={'tools':[{'name':'count' if duplicate else f'tool{i}','inputSchema':SCHEMA} for i in range(257)]}
                return 200,'application/json',json.dumps(msg).encode()
            state['reply']=reply
            with pytest.raises(ValueError,match='Duplicate tool'):await client.list_tools()
            assert client.tools==[]
    finally:await client.disconnect()

@pytest.mark.asyncio
async def test_manager_explicit_protocol_respects_original_permission_gate(endpoint):
    from src.tools.manager import ToolManager
    url,state=endpoint;manager=ToolManager(confirm=make_confirm_gate(auto_approve=True))
    manager.config={'servers':[{'name':'fixture','transport':'http','protocol':'streamable_http','url':url}],
                    'permissions':{'rules':[{'id':'only-count','action':'allow','tool_names':['count'],'priority':200}]}}
    try:
        assert await manager.initialize()
        assert isinstance(manager.mcp_clients['fixture'],StreamableHTTPMCPClient)
        result=await manager.execute_tool('mcp__fixture__count',{'count':2})
        assert result.success
        assert state['calls'][-1][0]['params']['name']=='count'
    finally:await manager.shutdown()

@pytest.mark.asyncio
async def test_wrong_configured_version_never_contacts_endpoint(endpoint):
    from src.tools.manager import ToolManager
    url,state=endpoint;manager=ToolManager(confirm=make_confirm_gate(auto_approve=True))
    manager.config={'servers':[{'name':'fixture','transport':'http','protocol':'streamable_http',
                                'protocol_version':'2024-11-05','url':url}]}
    try:
        await manager.initialize()
        assert not manager.mcp_clients and not state['calls']
    finally:await manager.shutdown()

@pytest.mark.asyncio
async def test_sse_notification_budget_is_bounded(endpoint):
    url,state=endpoint
    def reply(p,msg):
        notification=json.dumps({'jsonrpc':'2.0','method':'notifications/progress'}).encode()
        body=(b'data: '+notification+b'\n\n')*257+b'data: '+json.dumps(msg).encode()+b'\n\n'
        return 200,'text/event-stream',body
    state['reply']=reply;client=StreamableHTTPMCPClient('fixture',url)
    try:
        await client.connect()
        with pytest.raises(ValueError,match='event budget'):await client.list_tools()
        assert len(state['calls'])==1
    finally:await client.disconnect()

@pytest.mark.asyncio
async def test_nested_unicode_and_boolean_header_mirroring(endpoint):
    url,state=endpoint;client=StreamableHTTPMCPClient('fixture',url)
    try:
        await client.connect();tools=await client.list_tools()
        tools[0].input_schema={'properties':{'nested':{'type':'object','properties':{
            'text':{'type':'string','x-mcp-header':'Text'},
            'flag':{'type':'boolean','x-mcp-header':'Flag'},
        }}}}
        result=await client.call_tool(MCPToolCall(tool_name='count',arguments={'nested':{'text':'中文\n内容','flag':True}}))
        assert result.success
        assert state['calls'][-1][1]['Mcp-Param-Text']==header_value('中文\n内容')
        assert state['calls'][-1][1]['Mcp-Param-Flag']=='true'
    finally:await client.disconnect()

@pytest.mark.asyncio
@pytest.mark.parametrize('version',['2025-03-26','2025-06-18','2025-11-25'])
async def test_legacy_handshake_session_and_expiry_never_replay_tools(endpoint,version):
    url,state=endpoint;state['session_id']='fixture-session'
    client=StreamableHTTPMCPClient('fixture',url,protocol_version=version)
    try:
        await client.connect();await client.list_tools()
        assert state['calls'][0][0]['method']=='initialize'
        assert state['calls'][1][0]['method']=='notifications/initialized'
        assert all(h['MCP-Session-Id']=='fixture-session' for _,h in state['calls'][1:])
        assert all('_meta' not in p.get('params',{}) for p,_ in state['calls'])
        assert (await client.call_tool(MCPToolCall(tool_name='count',arguments={'count':2}))).success
        n=len(state['calls'])
        state['reply']=lambda p,msg:(404,'application/json',b'{}')
        assert not (await client.call_tool(MCPToolCall(tool_name='count',arguments={'count':2}))).success
        assert not client.connected and len(state['calls'])==n+1
        assert not (await client.call_tool(MCPToolCall(tool_name='count',arguments={'count':2}))).success
        assert len(state['calls'])==n+1
    finally:await client.disconnect()
    assert state['deletes'][0]['MCP-Session-Id']=='fixture-session'

@pytest.mark.asyncio
async def test_unsupported_negotiated_version_closes_session(endpoint):
    url,state=endpoint;state['session_id']='fixture-session'
    def reply(p,msg):
        msg['result']['protocolVersion']='1999-01-01'
        return 200,'application/json',json.dumps(msg).encode()
    state['reply']=reply;client=StreamableHTTPMCPClient('fixture',url,protocol_version='2025-11-25')
    with pytest.raises(ValueError):await client.connect()
    assert client.session is None and not client.connected
    assert len(state['calls'])==1 and len(state['deletes'])==1

@pytest.mark.asyncio
async def test_advertised_resources_and_prompts_are_explicit_tools(endpoint):
    url,state=endpoint
    def reply(p,msg):
        method=p['method']
        if method=='initialize':msg['result']['capabilities'].update(resources={},prompts={})
        elif method in ('resources/list','resources/read','prompts/list','prompts/get'):
            msg['result']={ {'resources/list':'resources','resources/read':'contents','prompts/list':'prompts','prompts/get':'messages'}[method]:[] }
        return 200,'application/json',json.dumps(msg).encode()
    state['reply']=reply;client=StreamableHTTPMCPClient('fixture',url,protocol_version='2025-11-25')
    try:
        await client.connect();names=[t.name for t in await client.list_tools()]
        assert 'mcp_resource_read' in names and 'mcp_prompt_get' in names
        for name,args in [('mcp_resources_list',{}),('mcp_resource_read',{'uri':'fixture://read'}),('mcp_prompts_list',{}),('mcp_prompt_get',{'name':'helper','arguments':{'topic':'fixture'}})]:
            assert (await client.call_tool(MCPToolCall(tool_name=name,arguments=args))).success
        assert [p['method'] for p,h in state['calls'][-4:]]==['resources/list','resources/read','prompts/list','prompts/get']
    finally:await client.disconnect()

@pytest.mark.asyncio
async def test_legacy_cancel_notifies_original_request_without_replay(endpoint):
    from threading import Event
    url,state=endpoint;release=Event()
    def reply(p,msg):
        if p['method']=='tools/call':release.wait(timeout=30)
        return 200,'application/json',json.dumps(msg).encode()
    state['reply']=reply;client=StreamableHTTPMCPClient('fixture',url,protocol_version='2025-11-25',timeout=60)
    try:
        await client.connect();await client.list_tools()
        task=asyncio.create_task(client.call_tool(MCPToolCall(tool_name='count',arguments={'count':2})))
        for _ in range(1000):                       # 等请求真的到了服务端再取消；慢 CI 上 100ms 不够
            if any(p['method']=='tools/call' for p,h in state['calls']):break
            await asyncio.sleep(.01)
        assert any(p['method']=='tools/call' for p,h in state['calls'])
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        calls=[p for p,h in state['calls'] if p['method']=='tools/call']
        notice=next(p for p,h in state['calls'] if p['method']=='notifications/cancelled')
        assert len(calls)==1 and notice['params']['requestId']==calls[0]['id']
    finally:release.set();await client.disconnect()

@pytest.mark.asyncio
@pytest.mark.parametrize('capability',['resources','prompts'])
async def test_feature_only_server_never_requires_tools_list(endpoint,capability):
    url,state=endpoint
    def reply(p,msg):
        if p['method']=='initialize':msg['result']['capabilities']={capability:{}}
        elif p['method']=='tools/list':msg={'jsonrpc':'2.0','id':p['id'],'error':{'code':-32601,'message':'tools unsupported'}}
        else:msg['result']={'contents':[]} if p['method']=='resources/read' else {'messages':[]}
        return 200,'application/json',json.dumps(msg).encode()
    state['reply']=reply;client=StreamableHTTPMCPClient('fixture',url,protocol_version='2025-11-25')
    try:
        await client.connect();tools=await client.list_tools()
        assert len(tools)==2
        assert not any(p['method']=='tools/list' for p,h in state['calls'])
        name,args=('mcp_resource_read',{'uri':'fixture://guide'}) if capability=='resources' else ('mcp_prompt_get',{'name':'helper'})
        assert (await client.call_tool(MCPToolCall(tool_name=name,arguments=args))).success
    finally:await client.disconnect()
