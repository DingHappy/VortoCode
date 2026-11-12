"""2.4: actual assembled background agents and SDK, with an offline HTTP transport."""
import asyncio
import json

import httpx2
import openai
import pytest

from src.agents.agent_loop import MainAgent
from src.agents.team_coordinator import coordinator_turn
from src.agents.team_members import run_member
from tests.unit.test_team_runtime import _root


@pytest.fixture
def transport(monkeypatch):
    outcomes, requests, clients = [], [], []
    monkeypatch.setenv('OPENAI_API_BASE', 'https://model.invalid/v1')
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-fixture')
    monkeypatch.setenv('OPENAI_MAX_RETRIES', '0')
    original = openai.AsyncOpenAI
    def respond(request):
        requests.append(json.loads(request.content))
        outcome = outcomes.pop(0) if outcomes else 503
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, httpx2.Response):
            return outcome
        status = 200 if isinstance(outcome, dict) else outcome
        if status != 200:
            return httpx2.Response(status, json={'error': {'message': 'fixture failure', 'type': 'fixture'}})
        return httpx2.Response(200, json={'id': 'offline', 'object': 'chat.completion', 'created': 0,
            'model': 'offline-fixture', 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': outcome if isinstance(outcome, dict) else {'role': 'assistant', 'content': '离线核对完成'}}],
            'usage': {'prompt_tokens': 8, 'completion_tokens': 4, 'total_tokens': 12}})
    def create(**kwargs):
        kwargs['http_client'] = openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(respond))
        sdk = original(**kwargs)
        sdk._calculate_retry_timeout = lambda *args, **kwargs: 0  # No wall-clock backoff in tests.
        clients.append(sdk)
        return sdk
    monkeypatch.setattr(openai, 'AsyncOpenAI', create)
    return outcomes, requests, clients


def factory(tools, **kwargs):
    kwargs.update(native=False, env_context=False, auto_recall=False)
    return MainAgent(tools, **kwargs)


@pytest.mark.parametrize('kind', ['member', 'coordinator', 'staff'])
async def test_one_transient_503_does_not_fail_a_background_turn(tmp_path, transport, kind):
    outcomes, requests, clients = transport
    outcomes.extend([503, 200])
    try:
        if kind == 'member':
            service, root = _root(tmp_path)
            child = service.assign(root.id, '审查员', '核对公开材料', '结论')
            finished = await run_member(str(tmp_path), root.id, child.id, agent_factory=factory)
            assert finished.status == 'done'
        elif kind == 'coordinator':
            _, root = _root(tmp_path)
            error = await coordinator_turn(str(tmp_path), root.id, ['核对进度'], kick=lambda _: None,
                                           agent_factory=factory)
            assert error is None
        else:
            from src.agents.staff import build_staff_member
            from src.workflow.staff.registry import StaffRegistry
            role = StaffRegistry(str(tmp_path)).role('reviewer')
            agent = build_staff_member(str(tmp_path), role, {'model': ''}, check=lambda: None, agent_factory=factory)
            assert await agent.run_turn('核对公开材料', mode='plan') == '离线核对完成'
        assert len(requests) == 2 and requests[0] == requests[1]
    finally:
        for client in clients:
            await client.close()


@pytest.mark.parametrize('native', [False, True])
async def test_model_retry_after_a_tool_result_never_executes_the_tool_twice(transport, native):
    from src.agents.tool import Tool
    from src.llm.background import background_client
    outcomes, requests, clients = transport
    call = {'role': 'assistant', 'content': '{"tool":"lookup","args":{}}'}
    if native:
        call = {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'call-offline', 'type': 'function', 'function': {'name': 'lookup', 'arguments': '{}'}}]}
    outcomes.extend([call, 503, 200])
    effects = []
    async def lookup(args):
        effects.append('read')
        return '一次读取的结果'
    agent = MainAgent([Tool('lookup', '查资料', {}, lookup, operation_kind='read')],
        llm=background_client(), native=native, env_context=False, auto_recall=False, raise_llm_errors=True)
    try:
        assert await agent.run_turn('查资料并核对', mode='plan') == '离线核对完成'
        assert effects == ['read'] and len(requests) == 3
        assert requests[1] == requests[2]
    finally:
        await agent._client().close()


async def test_role_model_copy_and_retry_bound_do_not_change_a_cached_client(tmp_path, transport, monkeypatch):
    from src.agents.team_members import build_member
    from src.llm.client import LLMClient, LLMConfig
    outcomes, requests, clients = transport
    shared = LLMClient(LLMConfig(base_url='https://role.invalid/v1', api_key='offline-fixture',
                                 model='role-original', max_retries=9))
    monkeypatch.setattr('src.llm.providers.client_for_role', lambda _: shared)
    agent = build_member('reviewer', 'role-selected', max_steps=3, agent_factory=factory)
    try:
        with pytest.raises(openai.InternalServerError):
            await agent.run_turn('核对材料', mode='plan')
        assert len(requests) == 3 and all(req['model'] == 'role-selected' for req in requests)
        assert shared.config.model == 'role-original' and shared.config.max_retries == 9
        assert agent._client().config is not shared.config
    finally:
        await agent._client().close()


@pytest.mark.parametrize('statuses, count, success', [([503, 200], 2, True), ([503] * 4, 3, False), ([401, 200], 1, False)])
async def test_sdk_unavailable_fallback_has_the_same_background_bound(statuses, count, success, monkeypatch):
    from src.llm.background import background_client
    client, calls = background_client(), []
    client.config.retry_base_delay = 0
    class Response:
        def __init__(self, status):
            self.status = status
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def text(self): return 'offline failure'
        async def json(self):
            return {'model': 'offline-fixture', 'choices': [{'message': {'content': '完成'}}],
                    'usage': {'prompt_tokens': 8, 'completion_tokens': 4, 'total_tokens': 12}}
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        def post(self, *args, **kwargs):
            calls.append(kwargs['json'])
            return Response(statuses.pop(0))
    async def no_sdk(): return None
    monkeypatch.setattr(client, '_get_client', no_sdk)
    monkeypatch.setattr(client, '_endpoint_session', lambda **kwargs: Session())
    if success:
        assert (await client.chat([{'role': 'user', 'content': '核对'}]))['content'] == '完成'
    else:
        with pytest.raises(Exception, match='LLM request failed'):
            await client.chat([{'role': 'user', 'content': '核对'}])
    assert len(calls) == count


@pytest.mark.parametrize('cancelled', [False, True])
async def test_stream_interruption_or_cancellation_does_not_replay_partial_output(transport, cancelled):
    from src.llm.background import background_client
    outcomes, requests, clients = transport
    closed = []
    error = asyncio.CancelledError() if cancelled else httpx2.ReadError('offline disconnect')
    class Partial(httpx2.AsyncByteStream):
        async def __aiter__(self):
            chunk = {'id': 'offline', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'offline-fixture',
                     'choices': [{'index': 0, 'finish_reason': None, 'delta': {'content': '部分结果'}}]}
            yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
            raise error
        async def aclose(self):
            closed.append(True)
    outcomes.append(httpx2.Response(200, headers={'content-type': 'text/event-stream'}, stream=Partial()))
    client = background_client()
    stream = client.stream([{'role': 'user', 'content': '核对'}])
    try:
        assert await anext(stream) == '部分结果'
        with pytest.raises(asyncio.CancelledError if cancelled else openai.APIConnectionError):
            await anext(stream)
        assert len(requests) == 1 and closed
    finally:
        await stream.aclose()
        await client.close()


@pytest.mark.parametrize('status, expected', [(503, 3), (401, 1), (400, 1)])
async def test_background_retries_are_bounded_and_permanent_errors_fail_immediately(tmp_path, transport, status, expected):
    outcomes, requests, clients = transport
    outcomes.extend([status] * 5)
    service, root = _root(tmp_path)
    child = service.assign(root.id, '审查员', '核对公开材料', '结论')
    try:
        finished = await run_member(str(tmp_path), root.id, child.id, agent_factory=factory)
        assert finished.status == 'failed' and str(status) in finished.error
        assert len(requests) == expected
    finally:
        for client in clients:
            await client.close()


async def test_interactive_turn_still_reports_the_first_sent_request_error(transport):
    outcomes, requests, clients = transport
    outcomes.extend([503, 200])
    try:
        agent = factory([])
        emitted = []
        assert await agent.run_turn('核对材料', mode='plan', emit=emitted.append) == ''
        assert len(requests) == 1 and any('对话出错' in text for text in emitted)
    finally:
        for client in clients:
            await client.close()
