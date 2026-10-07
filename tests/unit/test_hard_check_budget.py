import asyncio

import pytest

from src.agents.agent_loop import MainAgent
from src.agents.bounded_check import run_bounded_check
from src.agents.tool import Tool
from src.llm.hard_budget import BudgetSupport, CheckLimits, HardBudgetedLLM, HardBudgetError


class Adapter:
    budget_support = BudgetSupport(True, True, True, True)

    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [{"content": "已检查证据并说明边界"}])
        self.inputs = []

    def count_input_tokens(self, messages, tools):
        self.inputs.append((messages, tools))
        return 4 + (3 if tools else 0)

    async def chat(self, messages, **kwargs):
        self.calls.append(kwargs)
        await asyncio.sleep(0)
        response = self.responses.pop(0)
        return {"usage": {"prompt_tokens": 4 + (3 if kwargs.get("tools") else 0),
                          "completion_tokens": 2, "total_tokens": 6 + (3 if kwargs.get("tools") else 0)}, **response}


def test_generic_clients_and_bad_limits_fail_before_any_call():
    with pytest.raises(HardBudgetError, match="关闭"):
        HardBudgetedLLM(object())
    for kwargs in ({"tokens": True}, {"seconds": float("nan")}, {"requests": 8}, {"tool_calls": 7}):
        with pytest.raises(ValueError):
            CheckLimits(**kwargs)


@pytest.mark.asyncio
async def test_input_schema_and_output_are_reserved_before_request_and_reconciled():
    adapter = Adapter([{"content": "one"}, {"content": "two"}])
    guard = HardBudgetedLLM(adapter, CheckLimits(tokens=14, output_tokens=6))
    await guard.chat([{"role": "user", "content": "read"}], tools=[{"schema": "included"}])
    assert adapter.calls[0]["max_tokens"] == 6
    assert adapter.inputs[0][1] == [{"schema": "included"}]
    assert guard.spent_tokens == guard.committed_tokens == 9
    with pytest.raises(HardBudgetError, match="违反"):
        await guard.chat([{"role": "user", "content": "finish"}])
    assert adapter.calls[1]["max_tokens"] == 1
    assert guard.reason


@pytest.mark.asyncio
async def test_parallel_requests_cannot_double_spend_remaining_tokens():
    adapter = Adapter()
    guard = HardBudgetedLLM(adapter, CheckLimits(tokens=6, output_tokens=2))
    results = await asyncio.gather(guard.chat([]), guard.chat([]), return_exceptions=True)
    assert len(adapter.calls) == 1
    assert sum(isinstance(result, HardBudgetError) for result in results) == 1
    assert guard.committed_tokens == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    {"usage": None}, {"usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 99}},
])
async def test_unknown_or_invalid_usage_blocks_every_following_request(response):
    adapter = Adapter([response])
    guard = HardBudgetedLLM(adapter)
    with pytest.raises(HardBudgetError):
        await guard.chat([])
    reserved = guard.committed_tokens
    with pytest.raises(HardBudgetError):
        await guard.chat([])
    assert len(adapter.calls) == 1 and reserved == 1028
    assert guard.snapshot()["usage_complete"] is False


@pytest.mark.asyncio
async def test_failed_request_is_not_retried_or_refunded():
    adapter = Adapter()
    async def fail(*args, **kwargs):
        adapter.calls.append(kwargs)
        raise OSError("network failed after dispatch")
    adapter.chat = fail
    guard = HardBudgetedLLM(adapter)
    with pytest.raises(OSError):
        await guard.chat([])
    with pytest.raises(HardBudgetError):
        await guard.chat([])
    assert len(adapter.calls) == 1 and guard.committed_tokens == 1028


@pytest.mark.asyncio
async def test_streaming_and_media_cannot_bypass_guard():
    for operation in (lambda guard: guard.stream_chat([]),
                      lambda guard: anext(guard.stream([])),
                      lambda guard: guard.chat([{"content": [{"type": "image_url"}]}])):
        adapter = Adapter()
        guard = HardBudgetedLLM(adapter)
        with pytest.raises(HardBudgetError):
            await operation(guard)
        assert adapter.calls == []


@pytest.mark.asyncio
async def test_wall_timeout_covers_tools_as_well_as_model_requests():
    guard = HardBudgetedLLM(Adapter(), CheckLimits(seconds=0.01))
    with pytest.raises(HardBudgetError, match="时间") as caught:
        await guard.run(lambda: asyncio.sleep(10))
    assert guard.requests == 0
    assert caught.value.budget["requests"] == 0


@pytest.mark.asyncio
async def test_tool_batch_cannot_expand_limit_and_swallowed_error_is_not_success():
    executed = []
    async def read(args):
        executed.append(args)
        return "evidence"
    guard = HardBudgetedLLM(Adapter(), CheckLimits(tool_calls=2))
    agent = MainAgent([Tool("read", "read", {}, read)], llm=guard)
    async def operation():
        # MainAgent normally renders a batch's individual exceptions as data.
        await agent._run_tools([("read", {"i": i}) for i in range(8)], "plan", lambda _: None)
        return "fake success"
    with pytest.raises(HardBudgetError, match="工具调度"):
        await guard.run(operation)
    assert len(executed) == 2 and guard.tool_calls == 2
    # ContextVar is restored: normal interactive turns keep their old behavior.
    await agent._run_tool("read", {}, "plan", lambda _: None)
    assert len(executed) == 3


@pytest.mark.asyncio
async def test_readonly_check_is_real_agent_execution_with_unsupported_provider_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("VORTOCODE_AGENT_MAX_STEPS", "100")
    result = await run_bounded_check(str(tmp_path), "核对交接", Adapter())
    assert result["result"] == "已检查证据并说明边界"
    assert result["budget"]["requests"] == 1
    assert result["budget"]["spent_tokens"] in (6, 9)
    assert result["budget"]["usage_complete"] is True
    with pytest.raises(HardBudgetError, match="关闭"):
        await run_bounded_check(str(tmp_path), "核对", object())


@pytest.mark.asyncio
async def test_bounded_context_rejects_readonly_named_delegation_and_write_tools():
    for tool in (Tool("task", "spawn", {}, lambda _: None),
                 Tool("write", "write", {}, lambda _: None, read_only=False)):
        guard = HardBudgetedLLM(Adapter())
        async def operation():
            guard.claim_tool(tool)
        with pytest.raises(HardBudgetError, match="不允许"):
            await guard.run(operation)


@pytest.mark.asyncio
async def test_oversized_input_is_blocked_before_dispatch():
    adapter = Adapter()
    adapter.count_input_tokens = lambda messages, tools: 8000
    guard = HardBudgetedLLM(adapter)
    with pytest.raises(HardBudgetError, match="未发送"):
        await guard.chat([])
    assert guard.requests == 0 and adapter.calls == []


@pytest.mark.asyncio
async def test_force_finish_cannot_issue_an_extra_request_after_limit(tmp_path):
    (tmp_path / "evidence.txt").write_text("evidence")
    adapter = Adapter([{"content": '{"tool":"read_file","args":{"path":"evidence.txt"}}'}])
    with pytest.raises(HardBudgetError, match="未发送"):
        await run_bounded_check(str(tmp_path), "检查文件", adapter,
                                limits=CheckLimits(tool_calls=1, requests=1))
    assert len(adapter.calls) == 1


@pytest.mark.asyncio
async def test_cancellation_retains_unknown_reservation_and_prevents_reuse():
    adapter = Adapter()
    started = asyncio.Event()
    async def blocked(messages, **kwargs):
        adapter.calls.append(kwargs)
        started.set()
        await asyncio.Future()
    adapter.chat = blocked
    guard = HardBudgetedLLM(adapter)
    running = asyncio.create_task(guard.run(lambda: guard.chat([])))
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert guard.committed_tokens == 1028
    with pytest.raises(HardBudgetError):
        await guard.chat([])
    assert len(adapter.calls) == 1
