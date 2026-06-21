"""Web 控制台的主 agent WebSocket 处理（src/web/routers/realtime.py）单测。

用假 WebSocket + 假 LLM 直接驱动 handle_agent_message，不起真实服务、不触网。
"""

import asyncio
import contextlib

import pytest

pytest.importorskip("fastapi")


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


async def _drain(ws):
    """重构后 handle_agent_message 立即返回、回合在后台跑；等后台任务跑完再断言。"""
    from src.web.routers import realtime
    task = realtime._WS_AGENT_TASKS.get(id(ws))
    if task is not None:
        await asyncio.wait_for(task, 5)


class _SlowAgent:
    """模拟一个长跑回合：发一条工具提示后睡很久，便于测中断。"""
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = False

    async def run_turn(self, text, mode, say, emit, stream_cb):
        say("🔧 working")
        self.started.set()
        try:
            await asyncio.sleep(30)
            emit("不该到这")
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.mark.asyncio
async def test_ws_agent_handler_streams_reply(monkeypatch):
    import src.llm.client as llmmod
    from src.web.routers.realtime import handle_agent_message

    class FakeLLM:
        async def chat(self, messages, **k):
            return {"content": "网页 agent 回复。"}

    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    ws = _FakeWS()
    await handle_agent_message(ws, {"type": "agent", "text": "你好", "mode": "plan"})
    await _drain(ws)
    types = [m["type"] for m in ws.sent]
    assert "agent_done" in types                                   # 收尾事件
    assert any("网页 agent 回复" in m.get("text", "") for m in ws.sent)


@pytest.mark.asyncio
async def test_ws_agent_handler_invokes_read_tool(monkeypatch):
    # 网页 agent 能调只读工具（list_files），结果回灌后给最终回复
    import src.llm.client as llmmod
    from src.web.routers.realtime import handle_agent_message

    class FakeLLM:
        def __init__(self, *a, **k):
            self.n = 0

        async def chat(self, messages, **k):
            self.n += 1
            if self.n == 1:
                return {"content": '{"tool":"list_files","args":{}}'}
            return {"content": "仓库里有这些文件。"}

    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    ws = _FakeWS()
    await handle_agent_message(ws, {"type": "agent", "text": "列一下文件", "mode": "plan"})
    await _drain(ws)
    types = [m["type"] for m in ws.sent]
    assert "agent_say" in types                                    # 工具调用提示
    assert any("仓库里有这些文件" in m.get("text", "") for m in ws.sent)


@pytest.mark.asyncio
async def test_ws_agent_no_key_degrades(monkeypatch):
    from src.web.routers.realtime import handle_agent_message

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ws = _FakeWS()
    await handle_agent_message(ws, {"type": "agent", "text": "你好"})
    assert any("OPENAI_API_KEY" in m.get("text", "") for m in ws.sent)
    assert ws.sent[-1]["type"] == "agent_done"


@pytest.mark.asyncio
async def test_ws_agent_say_strips_rich_markup(monkeypatch):
    # 工具提示行在主 agent 里带 Rich 标记（🔧 [b]name[/b][dim]…[/dim]）；Web 端必须剥成纯文本，
    # 否则 agent.html 会原样显示 [b]/[dim] 标签。
    import src.llm.client as llmmod
    from src.web.routers.realtime import handle_agent_message

    class FakeLLM:
        def __init__(self, *a, **k):
            self.n = 0

        async def chat(self, messages, **k):
            self.n += 1
            if self.n == 1:
                return {"content": '{"tool":"list_files","args":{}}'}
            return {"content": "好了。"}

    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    ws = _FakeWS()
    await handle_agent_message(ws, {"type": "agent", "text": "列文件", "mode": "plan"})
    await _drain(ws)
    says = [m["text"] for m in ws.sent if m["type"] == "agent_say"]
    assert says and any("list_files" in s for s in says)       # 工具名还在
    assert all("[b]" not in s and "[dim]" not in s and "[/" not in s for s in says)  # 但 Rich 标记没了


# ---- 回合在后台跑 + 可中断（agent_cancel）----

@pytest.mark.asyncio
async def test_agent_turn_runs_in_background_and_can_cancel(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS()
    agent = _SlowAgent()
    realtime._WS_AGENTS[id(ws)] = agent
    try:
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "hi", "mode": "plan"})
        task = realtime._WS_AGENT_TASKS.get(id(ws))
        assert task is not None and not task.done()            # handle 立即返回，回合在后台
        await asyncio.wait_for(agent.started.wait(), 2)        # 回合真的开跑了
        await realtime.handle_websocket_message(ws, {"type": "agent_cancel"})   # 停止
        await asyncio.wait_for(task, 2)
        assert agent.cancelled                                 # run_turn 收到取消
        assert any(m["type"] == "agent_cancelled" for m in ws.sent)
        assert id(ws) not in realtime._WS_AGENT_TASKS          # 注册表清理
    finally:
        realtime._WS_AGENTS.pop(id(ws), None)
        realtime._WS_AGENT_TASKS.pop(id(ws), None)


@pytest.mark.asyncio
async def test_agent_turn_rejects_concurrent(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS()
    agent = _SlowAgent()
    realtime._WS_AGENTS[id(ws)] = agent
    try:
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "a", "mode": "plan"})
        await asyncio.wait_for(agent.started.wait(), 2)
        ws.sent.clear()
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "b", "mode": "plan"})
        assert any("还在跑" in m.get("text", "") for m in ws.sent)    # 第二条被拒，不并发
    finally:
        realtime._cancel_agent_turn(ws)
        task = realtime._WS_AGENT_TASKS.get(id(ws))
        if task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, 2)
        realtime._WS_AGENTS.pop(id(ws), None)
        realtime._WS_AGENT_TASKS.pop(id(ws), None)


@pytest.mark.asyncio
async def test_cancel_with_no_running_turn_is_noop():
    from src.web.routers import realtime
    ws = _FakeWS()
    assert realtime._cancel_agent_turn(ws) is False     # 没有在跑的回合 → False，不报错
