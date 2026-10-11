"""Bound one human request, across batches/protocols/build continuations."""
import json

import pytest
from src.agents.agent_loop import MainAgent
from src.agents.tool import Tool


def test_batch_prefix_is_bounded_and_does_not_admit_later_writes(monkeypatch):
    monkeypatch.setenv("VORTOCODE_MAX_WEB_TOOL_CALLS", "2")
    agent = MainAgent([])
    calls, used, exhausted = agent._limit_tool_calls([
        ("read_file", {}), ("web_search", {"query": "one"}),
        ("web_fetch", {"url": "https://example.com"}), ("edit_file", {})], "build", 0, lambda _: None)
    assert [n for n, _ in calls] == ["read_file", "web_search", "web_fetch"]
    assert used == 3 and exhausted
    assert not agent._should_auto_continue_build("build", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_changed_queries_stop_across_build_segments_and_reset_for_new_turn(monkeypatch, native):
    monkeypatch.setenv("VORTOCODE_MAX_WEB_TOOL_CALLS", "3")
    monkeypatch.setenv("VORTOCODE_BUILD_AUTO_CONTINUES", "3")
    executed, requests, notices = [], [], []
    async def search(args):
        executed.append(args)
        return "verified-fixture-" + str(len(executed))
    class Looping:
        async def chat(self, messages, tools=None, **kwargs):
            requests.append((messages, tools))
            if "【收尾】" in messages[0]["content"]:
                assert tools is None
                return {"content": "资料不足，尚未核实。"}
            query = "different-" + str(len(requests))
            if tools is not None:
                return {"content": "", "tool_calls": [{"name": "web_search", "arguments": json.dumps({"query": query})}]}
            return {"content": json.dumps({"tool": "web_search", "args": {"query": query}})}
    agent = MainAgent([Tool("web_search", "search", {"query":"text"}, search, read_only=True)],
                      llm=Looping(), native=native, max_steps=1, env_context=False)
    assert await agent.run_turn("研究", mode="build", say=notices.append) == "资料不足，尚未核实。"
    assert len(executed) == 3
    assert sum("【收尾】" in m[0]["content"] for m, _ in requests) == 1
    assert any("调用上限" in n for n in notices)
    assert "verified-fixture-3" in str(requests[-1][0])
    await agent.run_turn("下一项", mode="build")
    assert len(executed) == 6


@pytest.mark.asyncio
async def test_failed_tools_consume_dispatch_budget_without_accepting_results(monkeypatch):
    monkeypatch.setenv("VORTOCODE_MAX_WEB_TOOL_CALLS", "1")
    events, confirms = [], []
    async def failing(args): raise RuntimeError("fixture failure")
    class Looping:
        async def chat(self, messages, **kwargs):
            return {"content": '{"tool":"web_fetch","args":{}}'}
    async def confirm(*args): confirms.append(args); return True
    agent = MainAgent([Tool("web_fetch", "fetch", {}, failing, read_only=True)],
                      llm=Looping(), native=False, on_escalate=confirm, on_tool_event=lambda stage, payload: events.append((stage,payload)))
    await agent.run_turn("读取", mode="plan")
    assert agent._web_tool_calls == 1
    assert not confirms
    assert any(stage == "finish" and p["status"] == "failed" for stage,p in events)
    assert "资料不足" in agent._budget_exhausted_message("plan")


def test_non_web_tools_keep_original_execution_budget(monkeypatch):
    monkeypatch.setenv("VORTOCODE_MAX_WEB_TOOL_CALLS", "1")
    a=MainAgent([]); calls=[("read_file",{})]*20
    assert a._limit_tool_calls(calls,"build",0,lambda _:None)==(calls,20,False)


def test_recovery_identity_tracks_cap_but_not_consumed_count(monkeypatch):
    monkeypatch.setenv("VORTOCODE_MAX_WEB_TOOL_CALLS", "2")
    a = MainAgent([])
    before = a.behavior_fingerprint()
    a._web_tool_calls = 1
    assert a.behavior_fingerprint() == before
    a.max_web_tool_calls = 3
    assert a.behavior_fingerprint() != before

@pytest.mark.parametrize("native", [False, True])
@pytest.mark.asyncio
async def test_batch_history_contains_only_the_admitted_prefix(monkeypatch, native):
    monkeypatch.setenv("VORTOCODE_MAX_WEB_TOOL_CALLS", "2")
    executions = []
    calls = [{"tool": "web_search", "args": {"query": str(i)}} for i in range(3)]
    async def handler(args): executions.append(args); return "fixture result"
    class Batch:
        async def chat(self, messages, tools=None, **kwargs):
            if "【收尾】" in messages[0]["content"]:
                assert '"query": "2"' not in str(messages)
                return {"content":"资料不足，尚未核实。"}
            if tools:
                return {"content":"", "tool_calls":[{"name":c["tool"],"arguments":json.dumps(c["args"])} for c in calls]}
            return {"content":json.dumps(calls)}
    a=MainAgent([Tool("web_search","search",{},handler,read_only=True)],llm=Batch(),native=native)
    await a.run_turn("查询",mode="build")
    assert executions == [{"query":"0"},{"query":"1"}]


def test_config_cannot_disable_or_unbound_the_dispatch_cap(monkeypatch):
    for value, expected in [("0",1),("100000",64),("invalid",12)]:
        monkeypatch.setenv("VORTOCODE_MAX_WEB_TOOL_CALLS",value)
        assert MainAgent([]).max_web_tool_calls == expected
