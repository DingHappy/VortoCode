"""Execution stops must cross the real streaming client's display boundary."""
from types import SimpleNamespace
import asyncio

import pytest

from src.agents.agent_loop import MainAgent
from src.gateway.collaboration import CollaborationConflict
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
