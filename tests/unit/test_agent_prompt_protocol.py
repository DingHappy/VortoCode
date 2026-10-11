"""Prompt contracts at the actual request boundary; no provider inference."""
import pytest

from src.agents.main_agent import MainAgent, Tool


def test_recovery_fingerprint_tracks_both_protocol_contracts_not_runtime_fallback(monkeypatch):
    import src.agents.agent_loop as loop

    agent = MainAgent([], native=True)
    before = agent.behavior_fingerprint()
    agent._native = False
    assert agent.behavior_fingerprint() == before
    monkeypatch.setattr(loop, "_NATIVE_TOOL_PROTOCOL", loop._NATIVE_TOOL_PROTOCOL + " changed")
    assert agent.behavior_fingerprint() != before
    native_changed = agent.behavior_fingerprint()
    monkeypatch.setattr(loop, "_PROMPTED_TOOL_PROTOCOL", loop._PROMPTED_TOOL_PROTOCOL + " changed")
    assert agent.behavior_fingerprint() != native_changed


def test_protocol_is_specific_and_stable_without_changing_mode_permissions():
    for native in (False, True):
        agent = MainAgent([], native=native)
        for mode in ("plan", "build"):
            prompt = agent._system(mode)
            assert prompt == agent._system(mode)
            assert "绝不编造分支名或测试结果" in prompt
            assert "不是用户发来的新消息" in prompt
            if native:
                assert "结构化 tool_calls" in prompt
                assert "只输出 JSON、不要任何多余文字" not in prompt
            else:
                assert "只输出 JSON、不要任何多余文字" in prompt
                assert '{"tool": "工具名", "args": {参数...}}' in prompt
            if mode == "plan":
                assert "plan 模式下写/重型工具" in prompt
                assert "request_build" in prompt
            else:
                assert "现有权限、确认门和 capability 限制仍然生效" in prompt


@pytest.mark.asyncio
async def test_permanent_fallback_changes_prompt_in_the_same_turn_and_preserves_tool_result():
    requests = []
    executions = []

    async def read(args):
        executions.append(args)
        return "verified-fixture"

    class UnsupportedTools:
        async def chat(self, messages, tools=None, **kwargs):
            requests.append((messages[0]["content"], tools, messages))
            if tools is not None:
                raise RuntimeError("model has no tools support")
            if len(requests) == 2:
                return {"content": '{"tool":"read_file","args":{"path":"README.md"}}'}
            return {"content": "已读取 verified-fixture。"}

    agent = MainAgent([Tool("read_file", "read", {"path": "file"}, read, read_only=True)],
                      llm=UnsupportedTools(), native=True)
    final = await agent.run_turn("读取 README", mode="plan")
    assert "verified-fixture" in final
    assert executions == [{"path": "README.md"}]
    assert len(requests) == 3
    assert "结构化 tool_calls" in requests[0][0]
    assert requests[0][1] is not None
    for prompt, schema, _ in requests[1:]:
        assert "只输出 JSON、不要任何多余文字" in prompt
        assert schema is None
    assert "verified-fixture" in str(requests[-1][2])
    for message in requests[-1][2]:
        if isinstance(message.get("content"), str) and message["content"].startswith("[工具 "):
            assert "【当前日期】" not in message["content"]
    assert agent._native is False


@pytest.mark.asyncio
async def test_transient_failure_does_not_switch_protocol_or_send_fallback_request():
    prompts = []

    class Timeout:
        async def chat(self, messages, tools=None, **kwargs):
            prompts.append((messages[0]["content"], tools))
            raise TimeoutError("temporary timeout")

    async def read(args):
        pytest.fail("failed model request must not execute tools")

    agent = MainAgent([Tool("read_file", "read", {}, read, read_only=True)], llm=Timeout(), native=True)
    assert await agent.run_turn("读取", mode="plan") == ""
    assert agent._native is True
    assert len(prompts) == 1
    assert "结构化 tool_calls" in prompts[0][0]
    assert prompts[0][1] is not None


@pytest.mark.asyncio
async def test_no_directory_conversation_receives_current_date_without_host_paths():
    from datetime import datetime
    requests = []

    class Capture:
        async def chat(self, messages, **kwargs):
            requests.append(messages)
            return {"content": "已记录日期。"}

    agent = MainAgent([], llm=Capture(), native=False, env_context=False)
    await agent.run_turn("现在有哪些最新模型？")
    user = requests[0][-1]["content"]
    assert f"【当前日期】{datetime.now():%Y-%m-%d}" in user
    assert "用户指定历史范围" in user
    assert "工作目录" not in user and "<env>" not in user
    assert "【当前日期】" not in requests[0][0]["content"]
    assert all("【当前日期】" not in str(message["content"]) for message in agent.history)
