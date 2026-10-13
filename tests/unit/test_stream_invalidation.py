"""Execution stops must cross the real streaming client's display boundary."""
from types import SimpleNamespace
import asyncio

import pytest

from src.agents.agent_loop import MainAgent
from src.workflow.tasks.errors import CollaborationConflict
from src.llm.client import LLMClient, LLMConfig


class SDKStream:
    def __init__(self, state, channel):
        self.state, self.channel = state, channel
        self.reads, self.closes = 0, 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.reads == 3:
            raise StopAsyncIteration
        self.reads += 1
        if self.reads == 2:
            self.state["valid"] = False
        delta = SimpleNamespace(**{self.channel: f"chunk-{self.reads}"})
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

    async def close(self):
        self.closes += 1


def sdk_client(monkeypatch, stream):
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return stream

    async def get_client():
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    client = LLMClient(LLMConfig(api_key="offline-fixture", base_url="http://offline.invalid/v1"))
    monkeypatch.setattr(client, "_get_client", get_client)
    return client, calls


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("channel", ["content", "reasoning_content"])
@pytest.mark.parametrize("observe_reasoning", [False, True])
@pytest.mark.asyncio
async def test_stop_crosses_sdk_callbacks_without_consuming_late_chunks(monkeypatch, native, channel, observe_reasoning):
    state = {"valid": True}
    stream = SDKStream(state, channel)
    client, calls = sdk_client(monkeypatch, stream)
    content, reasoning = [], []
    failure = CollaborationConflict("已消费的前置结果版本变化")

    def check():
        if not state["valid"]:
            raise failure

    agent = MainAgent([], llm=client, execution_check=check)
    on_reasoning = reasoning.append if observe_reasoning else None
    with pytest.raises(CollaborationConflict) as caught:
        if native:
            await agent._native_complete([], [], content.append, on_reasoning, [])
        else:
            await agent._complete([], content.append, on_reasoning)
    assert caught.value is failure
    assert stream.reads == 2 and stream.closes == 1 and len(calls) == 1
    assert content == (["chunk-1"] if channel == "content" else [])
    assert reasoning == (["chunk-1"] if channel == "reasoning_content" and observe_reasoning else [])


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.asyncio
async def test_display_failure_still_does_not_interrupt_model(monkeypatch, native):
    stream = SDKStream({"valid": True}, "reasoning_content")
    client, calls = sdk_client(monkeypatch, stream)

    def broken_display(_):
        raise RuntimeError("display disconnected")

    if native:
        result = await client.stream_chat([], on_reasoning=broken_display)
        assert result["reasoning"] == "chunk-1chunk-2chunk-3"
    else:
        assert [part async for part in client.stream([], on_reasoning=broken_display)] == []
    assert stream.reads == 3 and stream.closes == 1 and len(calls) == 1


@pytest.mark.asyncio
async def test_prompt_body_guard_closes_custom_async_generator():
    state = {"valid": True, "closed": False}

    def check():
        if not state["valid"]:
            raise CollaborationConflict("前置版本变化")

    class Client:
        async def stream(self, *args, **kwargs):
            try:
                state["valid"] = False
                yield "迟到正文"
                pytest.fail("must not read another chunk")
            finally:
                state["closed"] = True

    agent = MainAgent([], llm=Client(), execution_check=check)
    with pytest.raises(CollaborationConflict):
        await agent._complete([], lambda _: pytest.fail("late output"))
    assert state["closed"]


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("failure", [RuntimeError("SDK read failed"), asyncio.CancelledError()])
@pytest.mark.asyncio
async def test_sdk_read_error_and_cancellation_close_stream(monkeypatch, native, failure):
    class BrokenStream(SDKStream):
        async def __anext__(self):
            raise failure

    stream = BrokenStream({"valid": True}, "content")
    client, calls = sdk_client(monkeypatch, stream)
    with pytest.raises(type(failure)) as caught:
        if native:
            await client.stream_chat([])
        else:
            _ = [chunk async for chunk in client.stream([])]
    assert caught.value is failure
    assert stream.closes == 1 and len(calls) == 1


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.asyncio
async def test_cleanup_failure_retains_original_execution_stop(monkeypatch, caplog, native):
    state = {"valid": True}

    class BrokenClose(SDKStream):
        async def close(self):
            self.closes += 1
            raise RuntimeError("close failed")

    stream = BrokenClose(state, "reasoning_content")
    client, _ = sdk_client(monkeypatch, stream)
    failure = OSError("失效状态无法保存，禁止继续输出")

    def check():
        if not state["valid"]:
            raise failure

    agent = MainAgent([], llm=client, execution_check=check)
    with pytest.raises(OSError) as caught:
        if native:
            await agent._native_complete([], [], lambda _: None, None, [])
        else:
            await agent._complete([], lambda _: None)
    assert caught.value is failure and stream.reads == 2 and stream.closes == 1
    assert "LLM stream cleanup failed" in caplog.text


class FilteredStream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = self.closes = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.reads == len(self.chunks):
            raise StopAsyncIteration
        chunk = self.chunks[self.reads]
        self.reads += 1
        return chunk

    async def close(self):
        self.closes += 1


def filter_chunk(delta=None, *, usage=None, filtered=False):
    return SimpleNamespace(model='mimo-response', usage=usage,
                           choices=[SimpleNamespace(delta=delta,
                                                    finish_reason='content_filter' if filtered else None)])


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('delta_present', [False, True])
@pytest.mark.parametrize('usage', [None, SimpleNamespace(prompt_tokens=7, completion_tokens=1, total_tokens=8)])
@pytest.mark.asyncio
async def test_stream_filter_closes_before_callbacks_or_late_chunks(monkeypatch, native, delta_present, usage):
    from src.llm.client import ProviderContentFiltered, get_usage, reset_usage

    delta = SimpleNamespace(content='blocked text', reasoning_content='blocked reasoning',
                            tool_calls=[SimpleNamespace(index=0, id='blocked-call',
                                                        function=SimpleNamespace(name='write_file', arguments='{}'))])
    stream = FilteredStream([filter_chunk(delta if delta_present else None, usage=usage, filtered=True),
                             filter_chunk(SimpleNamespace(content='late text'))])
    client, calls = sdk_client(monkeypatch, stream)
    content, reasoning = [], []
    reset_usage()
    with pytest.raises(ProviderContentFiltered) as caught:
        if native:
            await client.stream_chat([], on_content=content.append, on_reasoning=reasoning.append)
        else:
            content.extend([part async for part in client.stream([], on_reasoning=reasoning.append)])
    assert caught.value.response_model == 'mimo-response'
    assert caught.value.usage == ({'prompt_tokens': 7, 'completion_tokens': 1, 'total_tokens': 8} if usage else {})
    assert content == reasoning == []
    assert stream.reads == stream.closes == len(calls) == 1
    assert get_usage()['total_tokens'] == (8 if usage else 0)


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.asyncio
async def test_late_filter_keeps_earlier_display_partial_and_usage_unknown(monkeypatch, native):
    from src.llm.client import ProviderContentFiltered, get_usage, reset_usage

    stream = FilteredStream([filter_chunk(SimpleNamespace(content='partial reply')),
                             filter_chunk(filtered=True),
                             SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=7, completion_tokens=1, total_tokens=8))])
    client, calls = sdk_client(monkeypatch, stream)
    seen = []
    reset_usage()
    with pytest.raises(ProviderContentFiltered) as caught:
        if native:
            await client.stream_chat([], on_content=seen.append)
        else:
            async for part in client.stream([]):
                seen.append(part)
    assert seen == ['partial reply']  # Already emitted text cannot be retracted by the client.
    assert caught.value.usage == {} and get_usage()['calls'] == 0
    assert stream.reads == 2 and stream.closes == len(calls) == 1


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.asyncio
async def test_agent_filtered_tool_fragments_never_execute_or_retry(monkeypatch, native, strict):
    from src.agents.main_agent import Tool
    from src.llm.client import ProviderContentFiltered

    executions = []
    async def handler(args):
        executions.append(args)
        return 'done'

    if native:
        delta = SimpleNamespace(tool_calls=[SimpleNamespace(index=0, id='call-1',
                                function=SimpleNamespace(name='read_file', arguments='{}'))])
    else:
        delta = SimpleNamespace(content='{"tool":"read_file","args":{}}')
    stream = FilteredStream([filter_chunk(delta), filter_chunk(filtered=True)])
    client, calls = sdk_client(monkeypatch, stream)
    agent = MainAgent([Tool('read_file', 'read', {}, handler, read_only=True)], llm=client,
                      native=native, raise_llm_errors=strict)
    emitted, shown = [], []
    if strict:
        with pytest.raises(ProviderContentFiltered):
            await agent.run_turn('read file', emit=emitted.append, stream_cb=shown.append)
    else:
        result = await agent.run_turn('read file', emit=emitted.append, stream_cb=shown.append)
        assert result == '' and any('content_filter' in message for message in emitted)
    assert executions == shown == []
    assert len(calls) == 1 and stream.reads == 2 and stream.closes == 1
    assert agent._native is native
