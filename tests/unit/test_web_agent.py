"""Web 控制台的主 agent WebSocket 处理（src/web/routers/realtime.py）单测。

用假 WebSocket + 假 LLM 直接驱动 handle_agent_message，不起真实服务、不触网。
"""

import asyncio
import contextlib

import pytest

pytest.importorskip("fastapi")


class _FakeWS:
    def __init__(self, sid=None):
        self.sent = []
        if sid is not None:
            self.query_params = {"sid": sid}    # dict 有 .get，够 _session_key 用

    async def send_json(self, data):
        self.sent.append(data)


def _key(ws):
    from src.web.routers import realtime
    return realtime._session_key(ws)


def _inject_session(ws, agent):
    """把假 agent 塞进会话表（重构后 agent 按会话键存活，不再按 id(ws)）。"""
    from src.web.routers import realtime
    realtime._SESSIONS[_key(ws)] = {"agent": agent, "transcript": [], "last": 0.0}


def _cleanup(ws):
    from src.web.routers import realtime
    realtime._SESSIONS.pop(_key(ws), None)
    realtime._WS_AGENT_TASKS.pop(_key(ws), None)


async def _drain(ws):
    """重构后 handle_agent_message 立即返回、回合在后台跑；等后台任务跑完再断言。"""
    from src.web.routers import realtime
    task = realtime._WS_AGENT_TASKS.get(_key(ws))
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
    _cleanup(ws)


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
    _cleanup(ws)


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
    _cleanup(ws)


# ---- 回合在后台跑 + 可中断（agent_cancel）----

@pytest.mark.asyncio
async def test_agent_turn_runs_in_background_and_can_cancel(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS()
    agent = _SlowAgent()
    _inject_session(ws, agent)
    try:
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "hi", "mode": "plan"})
        task = realtime._WS_AGENT_TASKS.get(_key(ws))
        assert task is not None and not task.done()            # handle 立即返回，回合在后台
        await asyncio.wait_for(agent.started.wait(), 2)        # 回合真的开跑了
        await realtime.handle_websocket_message(ws, {"type": "agent_cancel"})   # 停止
        await asyncio.wait_for(task, 2)
        assert agent.cancelled                                 # run_turn 收到取消
        assert any(m["type"] == "agent_cancelled" for m in ws.sent)
        assert _key(ws) not in realtime._WS_AGENT_TASKS        # 任务注册表清理
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_agent_turn_rejects_concurrent(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS()
    agent = _SlowAgent()
    _inject_session(ws, agent)
    try:
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "a", "mode": "plan"})
        await asyncio.wait_for(agent.started.wait(), 2)
        ws.sent.clear()
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "b", "mode": "plan"})
        assert any("还在跑" in m.get("text", "") for m in ws.sent)    # 第二条被拒，不并发
    finally:
        realtime._cancel_agent_turn(ws)
        task = realtime._WS_AGENT_TASKS.get(_key(ws))
        if task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, 2)
        _cleanup(ws)


@pytest.mark.asyncio
async def test_cancel_with_no_running_turn_is_noop():
    from src.web.routers import realtime
    ws = _FakeWS()
    assert realtime._cancel_agent_turn(ws) is False     # 没有在跑的回合 → False，不报错


def test_web_agent_includes_isolated_dev():
    from src.web.routers import realtime
    agent = realtime._new_agent()                       # 网页 agent 也有隔离 dev（build 门控）
    assert "dev_isolated" in agent.tools
    assert agent.tools["dev_isolated"].read_only is False


# ---- 会话持久化：同 sid 跨重连复用 agent + 回放对话 ----

class _EchoAgent:
    async def run_turn(self, text, mode, say, emit, stream_cb):
        emit("回复：" + text)


@pytest.mark.asyncio
async def test_session_reused_and_transcript_replayed_across_reconnect(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws1 = _FakeWS(sid="s-persist")
    _inject_session(ws1, _EchoAgent())          # 预置带 echo agent 的会话
    try:
        await realtime.handle_agent_message(ws1, {"type": "agent", "text": "你好", "mode": "plan"})
        await _drain(ws1)
        # 一回合后，会话 transcript 记下了用户输入 + 最终回复
        sess = realtime._SESSIONS[_key(ws1)]
        assert sess["transcript"] == [
            {"role": "user", "text": "你好"},
            {"role": "assistant", "text": "回复：你好"},
        ]

        # 「重连」：同 sid 的新连接 → 同一会话键、同一 agent
        ws2 = _FakeWS(sid="s-persist")
        assert _key(ws2) == _key(ws1)
        assert realtime._ws_agent(ws2) is sess["agent"]

        # 回放：把之前对话发回前端
        await realtime._replay_history(ws2)
        hist = [m for m in ws2.sent if m["type"] == "agent_history"]
        assert hist and hist[0]["items"] == sess["transcript"]
    finally:
        _cleanup(ws1)


@pytest.mark.asyncio
async def test_no_history_replayed_for_fresh_session():
    from src.web.routers import realtime
    ws = _FakeWS(sid="s-fresh")                 # 没跑过回合 → 无 transcript
    try:
        await realtime._replay_history(ws)
        assert not any(m["type"] == "agent_history" for m in ws.sent)
    finally:
        _cleanup(ws)


class _PlanAgent:
    """跑一回合时设置计划并触发 on_plan（模拟主 agent 调 update_plan）。"""
    def __init__(self):
        self._on_plan = None
        self.plan = []

    async def run_turn(self, text, mode, say, emit, stream_cb):
        self.plan = [{"step": "x", "status": "in_progress"}]
        if self._on_plan:
            self._on_plan(self.plan)
        emit("好了")


@pytest.mark.asyncio
async def test_agent_plan_event_streamed(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS(sid="s-plan")
    _inject_session(ws, _PlanAgent())
    try:
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "hi", "mode": "plan"})
        await _drain(ws)
        plans = [m for m in ws.sent if m["type"] == "agent_plan"]
        assert plans and plans[0]["items"] == [{"step": "x", "status": "in_progress"}]
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_replay_includes_current_plan():
    from src.web.routers import realtime
    ws = _FakeWS(sid="s-planreplay")
    _inject_session(ws, _PlanAgent())
    realtime._SESSIONS[_key(ws)]["agent"].plan = [{"step": "y", "status": "completed"}]
    try:
        await realtime._replay_history(ws)         # 重连：把当前计划面板也恢复
        plans = [m for m in ws.sent if m["type"] == "agent_plan"]
        assert plans and plans[0]["items"] == [{"step": "y", "status": "completed"}]
    finally:
        _cleanup(ws)


def test_sessions_evict_oldest_over_cap(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.setattr(realtime, "_MAX_SESSIONS", 3)
    monkeypatch.setattr(realtime, "_new_agent", lambda: object())   # 不建真 agent，纯测淘汰
    saved = dict(realtime._SESSIONS)
    realtime._SESSIONS.clear()
    try:
        keys = []
        for i in range(4):                      # 第 4 个进来时，超额淘汰最久未活动的第 1 个
            ws = _FakeWS(sid=f"evict-{i}")
            realtime._get_session(ws)
            keys.append(_key(ws))
        assert keys[0] not in realtime._SESSIONS         # 最旧被淘汰
        assert len(realtime._SESSIONS) == 3
        assert keys[-1] in realtime._SESSIONS
    finally:
        realtime._SESSIONS.clear()
        realtime._SESSIONS.update(saved)
