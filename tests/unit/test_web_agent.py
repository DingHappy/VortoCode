"""Web 控制台的主 agent WebSocket 处理（src/web/routers/realtime.py）单测。

用假 WebSocket + 假 LLM 直接驱动 handle_agent_message，不起真实服务、不触网。
"""

import asyncio
import contextlib
import subprocess

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
    realtime._SESSIONS[_key(ws)] = {
        "agent": agent, "transcript": [], "last": 0.0, "persist_events": False,
    }


def _cleanup(ws):
    from src.web.routers import realtime
    realtime._SESSIONS.pop(_key(ws), None)
    realtime._WS_AGENT_TASKS.pop(_key(ws), None)
    realtime._WS_AGENT_RUNNING.pop(_key(ws), None)
    realtime._WS_AGENT_PRIORITY.pop(_key(ws), None)
    realtime._WS_AGENT_STOP_REASONS.pop(_key(ws), None)
    realtime._SESSION_SUBSCRIBERS.pop(_key(ws), None)
    realtime._SESSION_EVENT_LOGS.pop(_key(ws), None)
    realtime._SESSION_EVENT_SEQS.pop(_key(ws), None)
    realtime._SESSION_EVENT_LOCKS.pop(_key(ws), None)
    realtime._SESSION_EVENT_LOADED.discard(_key(ws))
    realtime._SESSION_EVENT_ROOTS.pop(_key(ws), None)
    watcher = realtime._GIT_REVIEW_WATCHERS.pop(_key(ws), None)
    if watcher is not None:
        watcher.cancel()
    realtime._GIT_REVIEW_WATCH_STATES.pop(_key(ws), None)
    realtime._DETACHED_WEBSOCKETS.discard(ws)


async def _drain(ws):
    """重构后 handle_agent_message 立即返回、回合在后台跑；等后台任务跑完再断言。"""
    from src.web.routers import realtime
    task = realtime._WS_AGENT_TASKS.get(_key(ws))
    if task is not None:
        await asyncio.wait_for(task, 5)


async def _drain_all(ws):
    """等待当前回合及其自动启动的 FIFO 后继全部完成。"""
    from src.web.routers import realtime
    for _ in range(30):
        task = realtime._WS_AGENT_TASKS.get(_key(ws))
        if task is None:
            return
        await asyncio.wait_for(asyncio.shield(task), 5)
        await asyncio.sleep(0)
    raise AssertionError("prompt queue did not drain")


class _SlowAgent:
    """模拟一个长跑回合：发一条工具提示后睡很久，便于测中断。"""
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = False

    async def run_turn(self, text, mode, say, emit, stream_cb, images=None, audio=None):
        say("🔧 working")
        self.started.set()
        try:
            await asyncio.sleep(30)
            emit("不该到这")
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class _QueueAgent:
    """首回合受闸门控制，后续立即完成；记录顺序与最大并发数。"""
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.seen = []
        self.active = 0
        self.max_active = 0

    async def run_turn(self, text, mode, say, emit, stream_cb, images=None, audio=None):
        self.seen.append(text)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if text == "a":
                self.started.set()
                await self.release.wait()
            emit("完成：" + text)
        finally:
            self.active -= 1


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
    await handle_agent_message(ws, {"type": "agent", "text": "介绍一下这个功能", "mode": "plan"})
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
    # 工具调用由结构化的 agent_tool 事件表达（带状态/耗时/可展开的参数），
    # 逐个工具的 `🔧 name args` 回显只留给终端——见 tests/unit/test_timeline_noise.py。
    says = [m["text"] for m in ws.sent if m["type"] == "agent_say"]
    assert not any("list_files" in text for text in says), says
    tool_events = [m for m in ws.sent if m["type"] == "agent_tool"]
    assert [item["status"] for item in tool_events] == ["running", "succeeded"]
    assert tool_events[0]["id"] == tool_events[1]["id"]
    assert tool_events[0]["summary"] == "浏览文件"
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
    # 真会露到界面上的 say 是 dev 流水线的进度行（"⚙️ 隔离实现…"）。它们经 _web_progress_holder
    # 走同一条 agent_say，必须剥成纯文本，否则 agent.html / Desktop 会原样显示 [b]/[dim] 标签。
    import src.llm.client as llmmod
    from src.web.routers.realtime import _ws_agent, handle_agent_message

    ws = _FakeWS()

    class FakeLLM:
        def __init__(self, *a, **k):
            self.n = 0

        async def chat(self, messages, **k):
            self.n += 1
            if self.n == 1:                     # 回合已开始 → 进度回调已绑到本回合的队列
                _ws_agent(ws)._web_progress_holder["fn"]("⚙️ [b]隔离实现[/b][dim] 第 1 件[/dim]")
            return {"content": "好了。"}

    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    await handle_agent_message(ws, {"type": "agent", "text": "列文件", "mode": "plan"})
    await _drain(ws)
    says = [m["text"] for m in ws.sent if m["type"] == "agent_say"]
    assert any("隔离实现" in text for text in says), says            # 进度行到得了前端
    assert all("[b]" not in t and "[dim]" not in t and "[/" not in t for t in says), says
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
async def test_agent_turn_queues_concurrent_prompt(monkeypatch):
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
        snapshots = [m for m in ws.sent if m["type"] == "agent_queue"]
        assert snapshots and [item["text"] for item in snapshots[-1]["items"]] == ["b"]
        assert snapshots[-1]["running"]["text"] == "a"              # 第二条排队，不并发
    finally:
        realtime._cancel_agent_turn(ws)
        task = realtime._WS_AGENT_TASKS.get(_key(ws))
        if task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, 2)
        _cleanup(ws)


@pytest.mark.asyncio
async def test_prompt_queue_auto_drains_fifo_without_concurrency(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS()
    agent = _QueueAgent()
    _inject_session(ws, agent)
    try:
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "a", "rid": "qa"})
        await asyncio.wait_for(agent.started.wait(), 2)
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "b", "rid": "qb"})
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "b", "rid": "qb"})
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "c", "rid": "qc"})
        agent.release.set()
        await _drain_all(ws)
        assert agent.seen == ["a", "b", "c"]
        assert agent.max_active == 1
        assert realtime._SESSIONS[_key(ws)]["prompt_queue"] == []
        assert ws.sent[-1]["type"] == "agent_queue" and ws.sent[-1]["items"] == []
        assert isinstance(ws.sent[-1]["seq"], int)
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_prompt_queue_send_now_interrupts_then_preserves_fifo(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS()
    agent = _QueueAgent()
    _inject_session(ws, agent)
    try:
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "a", "rid": "qa"})
        await asyncio.wait_for(agent.started.wait(), 2)
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "b", "rid": "qb"})
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "c", "rid": "qc"})
        await realtime.handle_websocket_message(ws, {"type": "agent_queue_send_now", "id": "qc"})
        await _drain_all(ws)
        assert agent.seen == ["a", "c", "b"]
        assert agent.max_active == 1
        cancelled = [event for event in ws.sent if event["type"] == "agent_cancelled"]
        assert cancelled and cancelled[0]["rid"] == "qa"
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_prompt_queue_stop_pauses_and_remove_is_authoritative(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS()
    agent = _QueueAgent()
    _inject_session(ws, agent)
    try:
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "a", "rid": "qa"})
        await asyncio.wait_for(agent.started.wait(), 2)
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "b", "rid": "qb"})
        await realtime.handle_websocket_message(ws, {"type": "agent_cancel"})
        await _drain_all(ws)
        assert agent.seen == ["a"]
        assert [item["id"] for item in realtime._SESSIONS[_key(ws)]["prompt_queue"]] == ["qb"]
        await realtime.handle_websocket_message(ws, {"type": "agent_queue_remove", "id": "qb"})
        assert realtime._SESSIONS[_key(ws)]["prompt_queue"] == []
        assert ws.sent[-1]["type"] == "agent_queue" and ws.sent[-1]["items"] == []
        assert isinstance(ws.sent[-1]["seq"], int)
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_cancel_with_no_running_turn_is_noop():
    from src.web.routers import realtime
    ws = _FakeWS()
    assert realtime._cancel_agent_turn(ws) is False     # 没有在跑的回合 → False，不报错


@pytest.mark.asyncio
async def test_session_turn_survives_detach_and_new_client_receives_completion(monkeypatch):
    """刷新/关窗只移除 subscriber；同 sid 新客户端接管实时流，回合不被取消。"""
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws1 = _FakeWS(sid="reattach")
    ws2 = _FakeWS(sid="reattach")
    agent = _QueueAgent()
    _inject_session(ws1, agent)
    try:
        cursor1 = await realtime._session_event_cursor(_key(ws1))
        await realtime._attach_session_subscriber(ws1, cursor1)
        await realtime.handle_agent_message(ws1, {"type": "agent", "text": "a", "rid": "qa"})
        await asyncio.wait_for(agent.started.wait(), 2)

        cursor2 = await realtime._session_event_cursor(_key(ws2))
        await realtime._detach_session_subscriber(ws1)
        await realtime._attach_session_subscriber(ws2, cursor2)
        first_count = len(ws1.sent)
        agent.release.set()
        await _drain_all(ws1)

        assert agent.seen == ["a"]
        assert any(event["type"] == "agent_done" for event in ws2.sent)
        assert any(event["type"] == "agent_emit" for event in ws2.sent)
        assert len(ws1.sent) == first_count             # 已离开的窗口不再接收事件
    finally:
        await realtime._detach_session_subscriber(ws2)
        _cleanup(ws1)
        realtime._DETACHED_WEBSOCKETS.discard(ws2)


@pytest.mark.asyncio
async def test_session_attach_replays_events_emitted_during_hydrate_gap():
    from src.gateway import protocol as P
    from src.web.routers import realtime

    ws = _FakeWS(sid="gap-replay")
    key = _key(ws)
    try:
        cursor = await realtime._session_event_cursor(key)
        event = P.make_event(P.AGENT_SAY, text="hydrate 期间完成的事件")
        await realtime._publish_session_event(key, event)
        await realtime._attach_session_subscriber(ws, cursor)
        assert ws.sent == [P.sequence_event(event, 1)]
    finally:
        await realtime._detach_session_subscriber(ws)
        _cleanup(ws)


@pytest.mark.asyncio
async def test_session_event_cursor_survives_memory_reset_and_pages_replay(tmp_path):
    from src.gateway import protocol as P
    from src.web.routers import realtime

    ws = _FakeWS(sid="durable-replay")
    _inject_session(ws, _PlanAgent())
    session = realtime._SESSIONS[_key(ws)]
    session.update(repo_root=str(tmp_path), persist_events=True)
    try:
        await realtime._publish_session_event(
            _key(ws), P.make_event(P.AGENT_SAY, text="one"), fallback=ws)
        await realtime._publish_session_event(
            _key(ws), P.make_event(P.AGENT_SAY, text="two"), fallback=ws)
        await realtime._publish_session_event(
            _key(ws), P.make_event(P.AGENT_DONE), fallback=ws)
        assert [event["seq"] for event in ws.sent] == [1, 2, 3]

        realtime._SESSION_EVENT_LOGS.pop(_key(ws), None)
        realtime._SESSION_EVENT_SEQS.pop(_key(ws), None)
        realtime._SESSION_EVENT_LOADED.discard(_key(ws))
        assert await realtime._session_event_cursor(_key(ws)) == 3

        replay = _FakeWS(sid="durable-replay")
        await realtime.handle_websocket_message(replay, {
            "type": P.AGENT_EVENTS_REPLAY, "after_seq": 1, "limit": 1,
        })
        first = replay.sent[-1]
        assert first["type"] == P.AGENT_EVENTS
        assert [item["seq"] for item in first["items"]] == [2]
        assert first["cursor"] == 2 and first["latest_seq"] == 3
        await realtime.handle_websocket_message(replay, {
            "type": P.AGENT_EVENTS_REPLAY, "after_seq": first["cursor"], "limit": 1,
        })
        assert replay.sent[-1]["items"][0]["seq"] == 3
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_nonrecoverable_permission_event_persists_only_cursor(tmp_path):
    from src.gateway import protocol as P
    from src.web.routers import realtime

    ws = _FakeWS(sid="permission-cursor")
    _inject_session(ws, _PlanAgent())
    realtime._SESSIONS[_key(ws)].update(repo_root=str(tmp_path), persist_events=True)
    try:
        await realtime._publish_session_event(
            _key(ws), P.make_event(P.AGENT_CONFIRM, id="c1", text="token=super-secret"),
            fallback=ws,
        )
        raw = "".join(
            path.read_text(encoding="utf-8")
            for path in (tmp_path / ".vortocode" / "session_events" / "permission-cursor").glob("*.jsonl")
        )
        assert "super-secret" not in raw and "cursor_only" in raw
        realtime._SESSION_EVENT_LOGS.pop(_key(ws), None)
        realtime._SESSION_EVENT_SEQS.pop(_key(ws), None)
        realtime._SESSION_EVENT_LOADED.discard(_key(ws))
        assert await realtime._session_event_cursor(_key(ws)) == 1
        assert realtime._SESSION_EVENT_LOGS[_key(ws)] == []
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_task_handoff_persists_without_loaded_session_actor(tmp_path, monkeypatch):
    """后台任务可在窗口关闭/Session actor 被逐出后完成，交接仍必须落到 sid journal。"""
    from src.gateway import protocol as P
    from src.web.routers import realtime

    monkeypatch.chdir(tmp_path)
    key = "sid-background-owner"
    realtime._SESSIONS.pop(key, None)
    realtime._SESSION_EVENT_ROOTS.pop(key, None)
    realtime._SESSION_EVENT_LOGS.pop(key, None)
    realtime._SESSION_EVENT_SEQS.pop(key, None)
    realtime._SESSION_EVENT_LOADED.discard(key)
    try:
        await realtime._publish_task_session_event(
            key,
            P.make_event(P.TASK_HANDOFF, data={"id": "task-1", "status": "done"}),
        )
        assert realtime._SESSION_EVENT_SEQS[key] == 1
        raw = "".join(
            path.read_text(encoding="utf-8")
            for path in (tmp_path / ".vortocode" / "session_events" / "background-owner").glob("*.jsonl")
        )
        assert '"task_handoff"' in raw and '"task-1"' in raw
    finally:
        realtime._SESSION_EVENT_ROOTS.pop(key, None)
        realtime._SESSION_EVENT_LOGS.pop(key, None)
        realtime._SESSION_EVENT_SEQS.pop(key, None)
        realtime._SESSION_EVENT_LOADED.discard(key)


def test_web_agent_includes_isolated_dev_and_command():
    from src.web.routers import realtime
    agent = realtime._new_agent()                       # 网页 agent：隔离 dev(单/并行) + 受确认门的 shell/PR
    for name in ("dev_isolated", "dev_parallel", "run_command", "open_pr"):
        assert name in agent.tools and agent.tools[name].read_only is False


def test_web_agent_includes_research_delegation():
    from src.web.routers import realtime
    agent = realtime._new_agent()                       # 网页 agent 现也带只读子 agent 委派（补齐与 TUI 差距）
    for name in ("task", "research_parallel"):
        assert name in agent.tools and agent.tools[name].read_only is True


def test_web_agent_has_progress_holder():
    from src.web.routers import realtime
    agent = realtime._new_agent()                       # dev 流水线进度 holder：_run_agent_turn 每回合重绑到 ws
    assert getattr(agent, "_web_progress_holder", None) == {"fn": None}


def test_web_agent_includes_web_fetch():
    from src.web.routers import realtime
    agent = realtime._new_agent()                       # 联网读取（查文档/issue）——三端都接
    assert "web_fetch" in agent.tools and agent.tools["web_fetch"].read_only is True


@pytest.mark.asyncio
async def test_web_ensure_mcp_connects_once(monkeypatch):
    # 网页会话首回合按需连 MCP、接入工具；再调一次 no-op（只试一次）
    import src.agents.mcp_tools as mt
    from src.agents.main_agent import MainAgent, Tool
    from src.web.routers import realtime
    calls = {"connect": 0}

    async def _h(a):
        return "ok"

    class FakeMgr:
        async def shutdown(self):
            pass

    async def fake_connect(repo, **kwargs):
        calls["connect"] += 1
        return FakeMgr(), [Tool("mcp__s__t", "[MCP:s] t", {}, _h, read_only=False)]
    monkeypatch.setattr(mt, "connect_mcp", fake_connect)
    monkeypatch.setenv("VORTOCODE_WEB_MCP", "1")        # 网页 MCP 是 opt-in

    agent = MainAgent([])
    says = []
    await realtime._ensure_mcp(agent, says.append)
    assert "mcp__s__t" in agent.tools and agent._mcp_mgr is not None
    assert calls["connect"] == 1 and any("MCP" in s for s in says)
    await realtime._ensure_mcp(agent, says.append)      # 第二回合不再连
    assert calls["connect"] == 1


@pytest.mark.asyncio
async def test_web_ensure_mcp_no_config_is_noop(monkeypatch):
    import src.agents.mcp_tools as mt
    from src.agents.main_agent import MainAgent
    from src.web.routers import realtime

    async def fake_connect(repo, **kwargs):
        return None, []
    monkeypatch.setattr(mt, "connect_mcp", fake_connect)
    monkeypatch.setenv("VORTOCODE_WEB_MCP", "1")        # opt-in 开着，但无配置 → 仍是 no-op
    agent = MainAgent([])
    await realtime._ensure_mcp(agent, lambda _m: None)
    assert agent._mcp_mgr is None and not any(n.startswith("mcp__") for n in agent.tools)


@pytest.mark.asyncio
async def test_web_ensure_mcp_default_off_never_connects(monkeypatch):
    # 默认（未设 VORTOCODE_WEB_MCP）绝不连——防回归：自动连会让坏 mcp.yaml 卡死每回合
    import src.agents.mcp_tools as mt
    from src.agents.main_agent import MainAgent
    from src.web.routers import realtime
    monkeypatch.delenv("VORTOCODE_WEB_MCP", raising=False)
    called = {"n": 0}

    async def boom_connect(repo):
        called["n"] += 1
        raise AssertionError("默认不应连 MCP")
    monkeypatch.setattr(mt, "connect_mcp", boom_connect)
    agent = MainAgent([])
    await realtime._ensure_mcp(agent, lambda _m: None)   # 不抛、不连
    assert called["n"] == 0 and agent._mcp_mgr is None


@pytest.mark.asyncio
async def test_web_ensure_mcp_failure_surfaces_exception_type(monkeypatch):
    """回归：MCP 连接失败时用户看到的文案必须带异常类型名（如 TimeoutError）。

    只写 `{e}` 的话，TimeoutError 的 str(e) 是空串，用户只看到「🔌 MCP 连接失败: 」——无从下手。
    此测试用真实异常对象驱动代码路径，断言类型名出现在 say 的消息中。
    """
    import src.agents.mcp_tools as mt
    from src.agents.main_agent import MainAgent
    from src.web.routers import realtime

    async def boom_connect(repo, **_kw):
        raise TimeoutError()  # str(e) == ''，裸 {e} 会丢信息

    monkeypatch.setattr(mt, "connect_mcp", boom_connect)
    monkeypatch.setenv("VORTOCODE_WEB_MCP", "1")

    agent = MainAgent([])
    agent._mcp_tried = False  # 重置，确保会走连接逻辑
    says: list[str] = []
    await realtime._ensure_mcp(agent, says.append)

    assert agent._mcp_mgr is None  # 连接确实失败了
    assert says, "_ensure_mcp 没有 say 任何消息"
    failure_msg = next(m for m in says if "MCP" in m and "失败" in m)
    assert "TimeoutError" in failure_msg, (
        f"异常类型名丢失——只写 {{e}} 的回归: {failure_msg!r}"
    )


@pytest.mark.asyncio
async def test_web_shutdown_mcp_async_runs_and_safe_without_mgr():
    import asyncio
    from src.agents.main_agent import MainAgent
    from src.web.routers import realtime
    flag = {"down": False}

    class FakeMgr:
        async def shutdown(self):
            flag["down"] = True

    agent = MainAgent([])
    agent._mcp_mgr = FakeMgr()
    realtime._shutdown_mcp_async(agent)
    await asyncio.sleep(0.05)                            # 让 fire-and-forget 的 shutdown task 跑完
    assert flag["down"] is True
    realtime._shutdown_mcp_async(MainAgent([]))          # 无 mgr → 安全 no-op，不抛


@pytest.mark.asyncio
async def test_ws_confirm_round_trip_allow_and_deny():
    from src.web.routers import realtime
    for ok in (True, False):
        ws = _FakeWS()
        q = asyncio.Queue()
        confirm = realtime._make_ws_confirm(ws, q)
        task = asyncio.create_task(confirm("跑命令？"))
        evt = await asyncio.wait_for(q.get(), 2)
        assert evt["type"] == "agent_confirm" and "跑命令" in evt["text"]
        await realtime.handle_websocket_message(
            ws, {"type": "agent_confirm_response", "id": evt["id"], "ok": ok})
        assert await asyncio.wait_for(task, 2) is ok    # 前端应答 → confirm 返回对应布尔
        assert evt["id"] not in realtime._PENDING_CONFIRMS   # 清理
        closed = await asyncio.wait_for(q.get(), 2)          # 应答后也要告知：多端附着时同步收卡片
        assert closed["type"] == "agent_confirm_closed"
        assert closed["id"] == evt["id"] and closed["reason"] == "answered"


@pytest.mark.asyncio
async def test_ws_confirm_cancel_is_reported_as_cancelled():
    """回归（2026-09-18 真机冒烟）：点「停止」时前端收到的是 reason=answered。

    原因是 `asyncio.CancelledError` 继承 BaseException 而不是 Exception，被 `except Exception`
    漏掉，reason 停在初值。行为本身一直是对的（命令没跑、回合解开），错的是这条事件在说谎。
    """
    from src.web.routers import realtime

    ws = _FakeWS()
    q = asyncio.Queue()
    task = asyncio.create_task(realtime._make_ws_confirm(ws, q)("跑命令？"))
    evt = await asyncio.wait_for(q.get(), 2)
    assert evt["type"] == "agent_confirm"

    task.cancel()                                   # = 用户点「停止」，回合整体被取消
    with pytest.raises(asyncio.CancelledError):
        await task                                  # 取消要照常往外传，别被吞掉

    closed = await asyncio.wait_for(q.get(), 2)
    assert closed["type"] == "agent_confirm_closed"
    assert closed["id"] == evt["id"] and closed["reason"] == "cancelled"
    assert evt["id"] not in realtime._PENDING_CONFIRMS


@pytest.mark.asyncio
async def test_ws_confirm_timeout_tells_the_client_it_stopped_waiting(monkeypatch):
    """回归（2026-09-17 真机诊断）：确认超时按拒绝往下走，但前端那张卡片一直挂着、按钮还能点。

    超时窗口 monkeypatch 成 0，避免测试真的等 300 秒。
    """
    import asyncio as _asyncio

    from src.web.routers import realtime

    real_wait_for = _asyncio.wait_for

    async def _instant_timeout(awaitable, timeout):
        return await real_wait_for(awaitable, 0.01 if timeout == 300 else timeout)

    monkeypatch.setattr(realtime.asyncio, "wait_for", _instant_timeout)

    ws = _FakeWS()
    q = asyncio.Queue()
    decided = await realtime._make_ws_confirm(ws, q)("跑命令？")

    assert decided is False                                   # 超时 = 拒绝（安全）
    evt = await real_wait_for(q.get(), 2)
    closed = await real_wait_for(q.get(), 2)
    assert evt["type"] == "agent_confirm"
    assert closed["type"] == "agent_confirm_closed"
    assert closed["id"] == evt["id"] and closed["reason"] == "timeout"
    assert evt["id"] not in realtime._PENDING_CONFIRMS


@pytest.mark.asyncio
async def test_ws_confirm_accepts_the_kernel_kind_argument():
    """内核确认门会带上操作类别（kind）；端不消费它，但不能因此炸。"""
    from src.web.routers import realtime

    ws = _FakeWS()
    q = asyncio.Queue()
    task = asyncio.create_task(realtime._make_ws_confirm(ws, q)("写文件？", "write"))
    evt = await asyncio.wait_for(q.get(), 2)
    await realtime.handle_websocket_message(
        ws, {"type": "agent_confirm_response", "id": evt["id"], "ok": True})
    assert await asyncio.wait_for(task, 2) is True


# ---- 会话持久化：同 sid 跨重连复用 agent + 回放对话 ----

class _EchoAgent:
    async def run_turn(self, text, mode, say, emit, stream_cb, images=None, audio=None):
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

    async def run_turn(self, text, mode, say, emit, stream_cb, images=None, audio=None):
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


@pytest.mark.asyncio
async def test_replay_includes_structured_activity_history():
    from src.web.routers import realtime
    ws = _FakeWS(sid="s-activity-replay")
    _inject_session(ws, _PlanAgent())
    activity = {
        "type": "agent_tool", "id": "tool-1", "name": "read_file",
        "status": "succeeded", "summary": "读取 README.md", "rid": "r1",
    }
    realtime._SESSIONS[_key(ws)]["activities"] = [activity]
    try:
        await realtime._replay_history(ws)
        histories = [m for m in ws.sent if m["type"] == "agent_activity_history"]
        assert histories and histories[0]["items"] == [activity]
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_get_status_hydrate_replays_version_history_and_plan():
    """Desktop 等晚挂监听的客户端能主动恢复握手首帧，且不改变普通 get_status。"""
    from src.web.routers import realtime
    ws = _FakeWS(sid="s-hydrate")
    _inject_session(ws, _PlanAgent())
    session = realtime._SESSIONS[_key(ws)]
    session["transcript"] = [{"role": "user", "text": "之前的消息"}]
    session["agent"].plan = [{"step": "继续", "status": "in_progress"}]
    try:
        await realtime.handle_websocket_message(ws, {"type": "get_status", "hydrate": True})
        assert [event["type"] for event in ws.sent] == ["status", "agent_history", "agent_plan", "agent_queue"]
        from src.gateway.protocol import PROTOCOL_VERSION
        # 跟着协议常量走：写死数字会让每次版本上调都红在一个与本用例无关的断言上。
        assert ws.sent[0]["v"] == PROTOCOL_VERSION and ws.sent[0]["cursor"] == 0
        assert ws.sent[1]["items"] == session["transcript"]
        assert ws.sent[2]["items"] == session["agent"].plan
    finally:
        _cleanup(ws)


class _DesktopContextAgent:
    def __init__(self):
        self.seen_text = ""
        self.read_args = []

    async def _run_tool(self, name, args, mode, say):
        assert name == "read_file" and mode == "plan"
        self.read_args.append(args)
        say(f"read_file {args['path']}")
        return f"内容:{args['path']}"

    async def run_turn(self, text, mode, say, emit, stream_cb, images=None, audio=None):
        self.seen_text = text
        emit("已分析")


@pytest.mark.asyncio
async def test_desktop_context_files_run_through_shared_read_tool(monkeypatch):
    """Desktop 不能把本地正文绕过权限内核直塞模型，文件必须逐个经过 agent._run_tool。"""
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS(sid="desktop-context")
    agent = _DesktopContextAgent()
    _inject_session(ws, agent)
    try:
        await realtime.handle_agent_message(ws, {
            "type": "agent",
            "text": "解释代码",
            "mode": "plan",
            "context_files": ["src/a.py", "src/b.py"],
            "context_selections": [{"path": "src/c.py", "start": 4, "end": 12}],
        })
        await _drain(ws)
        assert agent.read_args == [
            {"path": "src/a.py"},
            {"path": "src/b.py"},
            {"path": "src/c.py", "start": 4, "end": 12},
        ]
        assert "<selected_local_context>" in agent.seen_text
        assert "内容:src/a.py" in agent.seen_text and "内容:src/b.py" in agent.seen_text
        assert "# 文件 src/c.py:4-12" in agent.seen_text
        assert any(event["type"] == "agent_say" for event in ws.sent)
        assert realtime._SESSIONS[_key(ws)]["transcript"][0]["text"].endswith("📎×3")
    finally:
        _cleanup(ws)


def test_desktop_context_file_sanitizer_is_bounded_and_relative():
    from src.web.routers import realtime
    raw = ["src/a.py", "src/a.py", "../secret", "/etc/passwd", "a//b"]
    raw += [f"src/{index}.py" for index in range(20)]
    out = realtime._sanitize_context_files(raw)
    assert out[0] == "src/a.py"
    assert len(out) == realtime._MAX_CONTEXT_FILES
    assert "../secret" not in out and "/etc/passwd" not in out and "a//b" not in out


def test_desktop_context_selection_sanitizer_validates_and_bounds_ranges():
    from src.web.routers import realtime
    raw = [
        {"path": "src/a.py", "start": 8, "end": 9999},
        {"path": "../secret", "start": 1, "end": 2},
        {"path": "src/b.py", "start": 0, "end": 2},
        {"path": "src/c.py", "start": True, "end": 2},
        {"path": "src/d.py", "start": 8, "end": 7},
    ]
    out = realtime._sanitize_context_selections(raw)
    assert out == [{
        "path": "src/a.py",
        "start": 8,
        "end": 8 + realtime._MAX_CONTEXT_SELECTION_LINES - 1,
    }]


def test_desktop_context_items_share_one_eight_item_budget():
    from src.web.routers import realtime
    message = {
        "context_files": ["src/all.py"],
        "context_selections": [
            {"path": "src/all.py", "start": 1, "end": 2},
            *[
                {"path": f"src/{index}.py", "start": 1, "end": 2}
                for index in range(20)
            ],
        ],
    }
    out = realtime._sanitize_context_items(message)
    assert out[0] == {"path": "src/all.py"}
    assert len(out) == realtime._MAX_CONTEXT_FILES
    assert {"path": "src/all.py", "start": 1, "end": 2} not in out


class _DesktopEditAgent:
    def __init__(self):
        self._web_confirm_holder = {"fn": None}
        self.bound_tools = []
        self.confirm_messages = []

    async def _confirm_gate(self, message):
        self.confirm_messages.append(message)
        return await self._web_confirm_holder["fn"](message)

    async def _run_bound_tool(self, tool, args, mode, say):
        self.bound_tools.append((tool.name, args["path"], mode))
        say(f"{tool.name} {args['path']}")
        return await tool.handler(args)


async def _wait_for_event(ws, event_type):
    for _ in range(100):
        matches = [event for event in ws.sent if event["type"] == event_type]
        if matches:
            return matches[-1]
        await asyncio.sleep(0.01)
    raise AssertionError(f"没有收到 {event_type}")


@pytest.mark.asyncio
async def test_desktop_workspace_edit_sends_diff_then_confirms_and_saves(monkeypatch, tmp_path):
    import subprocess
    from src.agents.workspace_editor import content_sha256
    from src.gateway import protocol as P
    from src.web.routers import realtime

    original = "answer = 1\n"
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    path = tmp_path / "main.py"
    path.write_text(original, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    ws = _FakeWS(sid="desktop-edit")
    agent = realtime._new_agent()
    _inject_session(ws, agent)
    try:
        await realtime.handle_websocket_message(ws, {
            "type": P.WORKSPACE_EDIT,
            "path": "main.py",
            "content": "answer = 2\n",
            "expected_sha256": content_sha256(original.encode()),
            "rid": "save-1",
        })
        confirm = await _wait_for_event(ws, P.AGENT_CONFIRM)
        await realtime.handle_websocket_message(ws, {
            "type": P.AGENT_CONFIRM_RESPONSE,
            "id": confirm["id"],
            "ok": True,
        })
        await _drain(ws)

        kinds = [event["type"] for event in ws.sent]
        assert kinds.index(P.AGENT_DIFF) < kinds.index(P.AGENT_CONFIRM) < kinds.index(P.WORKSPACE_EDIT_RESULT)
        result = [event for event in ws.sent if event["type"] == P.WORKSPACE_EDIT_RESULT][-1]
        assert result["ok"] is True and result["rid"] == "save-1" and len(result["sha256"]) == 64
        assert path.read_text(encoding="utf-8") == "answer = 2\n"
        assert agent._web_confirm_holder["fn"] is None
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_desktop_workspace_edit_rejects_stale_hash_without_confirmation(monkeypatch, tmp_path):
    import subprocess
    from src.gateway import protocol as P
    from src.web.routers import realtime

    path = tmp_path / "main.py"
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    path.write_text("current\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    ws = _FakeWS(sid="desktop-edit-stale")
    agent = _DesktopEditAgent()
    _inject_session(ws, agent)
    try:
        await realtime.handle_websocket_message(ws, {
            "type": P.WORKSPACE_EDIT,
            "path": "main.py",
            "content": "replacement\n",
            "expected_sha256": "0" * 64,
        })
        await _drain(ws)
        assert not any(event["type"] == P.AGENT_CONFIRM for event in ws.sent)
        result = [event for event in ws.sent if event["type"] == P.WORKSPACE_EDIT_RESULT][-1]
        assert result["ok"] is False and "编辑期间" in result["message"]
        assert path.read_text(encoding="utf-8") == "current\n"
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_desktop_workspace_edit_denial_keeps_disk_unchanged(monkeypatch, tmp_path):
    import subprocess
    from src.agents.workspace_editor import content_sha256
    from src.gateway import protocol as P
    from src.web.routers import realtime

    original = "before\n"
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    path = tmp_path / "main.py"
    path.write_text(original, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    ws = _FakeWS(sid="desktop-edit-denied")
    _inject_session(ws, realtime._new_agent())
    try:
        await realtime.handle_websocket_message(ws, {
            "type": P.WORKSPACE_EDIT,
            "path": "main.py",
            "content": "after\n",
            "expected_sha256": content_sha256(original.encode()),
        })
        confirm = await _wait_for_event(ws, P.AGENT_CONFIRM)
        await realtime.handle_websocket_message(ws, {
            "type": P.AGENT_CONFIRM_RESPONSE,
            "id": confirm["id"],
            "ok": False,
        })
        await _drain(ws)
        result = [event for event in ws.sent if event["type"] == P.WORKSPACE_EDIT_RESULT][-1]
        assert result["ok"] is False and "取消" in result["message"]
        assert path.read_text(encoding="utf-8") == original
    finally:
        _cleanup(ws)


# ---- 图片输入 ----

def test_sanitize_images():
    from src.web.routers import realtime
    big = "data:image/png;base64," + ("A" * (realtime._MAX_IMG_CHARS + 5))
    out = realtime._sanitize_images([
        "data:image/png;base64,AAAA",     # 收
        "https://x/y.jpg",                # 收
        {"url": "data:image/gif;base64,BB"},   # dict 形式也收
        "ftp://nope", "data:text/html,x", 42,  # 丢
        big,                              # 超大丢
    ])
    assert out == ["data:image/png;base64,AAAA", "https://x/y.jpg", "data:image/gif;base64,BB"]
    # 张数封顶
    many = realtime._sanitize_images(["data:image/png;base64,A"] * 20)
    assert len(many) == realtime._MAX_IMAGES
    assert realtime._sanitize_images("不是列表") == [] and realtime._sanitize_images(None) == []


class _CaptureAgent:
    """记下收到的 images/audio，便于断言附件透传到 run_turn。"""
    def __init__(self):
        self._on_plan = None
        self.plan = []
        self.got_images = "unset"
        self.got_audio = "unset"

    async def run_turn(self, text, mode, say, emit, stream_cb, images=None, audio=None):
        self.got_images = images
        self.got_audio = audio
        emit("收到。")


@pytest.mark.asyncio
async def test_images_passed_to_run_turn_and_recorded(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS(sid="s-img")
    agent = _CaptureAgent()
    _inject_session(ws, agent)
    try:
        await realtime.handle_agent_message(ws, {
            "type": "agent", "text": "看这张图", "mode": "plan",
            "images": ["data:image/png;base64,AAAA", "ftp://drop"]})
        await _drain(ws)
        assert agent.got_images == ["data:image/png;base64,AAAA"]    # 已过滤、透传
        transcript = realtime._SESSIONS[_key(ws)]["transcript"]
        assert any("🖼×1" in m["text"] for m in transcript if m["role"] == "user")  # 回放带图标记
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_image_only_turn_gets_default_prompt(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS(sid="s-imgonly")
    agent = _CaptureAgent()
    _inject_session(ws, agent)
    try:
        await realtime.handle_agent_message(ws, {
            "type": "agent", "mode": "plan", "images": ["data:image/png;base64,AAAA"]})
        await _drain(ws)
        assert agent.got_images == ["data:image/png;base64,AAAA"]     # 纯图片轮也能跑
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_empty_with_no_images_rejected(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS(sid="s-empty")
    try:
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "  ", "mode": "plan"})
        assert any(m["type"] == "agent_error" and "空输入" in m.get("text", "") for m in ws.sent)
    finally:
        _cleanup(ws)


# ---- 音频输入 + 语音回复(TTS) ----

def test_sanitize_audio():
    from src.web.routers import realtime
    big = "data:audio/wav;base64," + ("A" * (realtime._MAX_IMG_CHARS + 5))
    out = realtime._sanitize_audio([
        "data:audio/wav;base64,AAAA",     # 收
        {"url": "data:audio/mp3;base64,BB"},   # dict 形式也收
        "data:image/png;base64,CC",       # 图不是音频，丢
        "https://x/y.mp3",                # input_audio 不收 http，丢
        big,                              # 超大丢
    ])
    assert out == ["data:audio/wav;base64,AAAA", "data:audio/mp3;base64,BB"]
    assert len(realtime._sanitize_audio(["data:audio/wav;base64,A"] * 20)) == realtime._MAX_IMAGES
    assert realtime._sanitize_audio("x") == [] and realtime._sanitize_audio(None) == []


@pytest.mark.asyncio
async def test_audio_passed_to_run_turn_and_recorded(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS(sid="s-aud")
    agent = _CaptureAgent()
    _inject_session(ws, agent)
    try:
        await realtime.handle_agent_message(ws, {
            "type": "agent", "mode": "plan",
            "audio": ["data:audio/wav;base64,AAAA", "https://drop"]})
        await _drain(ws)
        # CaptureAgent.run_turn 把 images 记下来；audio 走 kwargs，这里验证它被透传
        assert agent.got_audio == ["data:audio/wav;base64,AAAA"]
        transcript = realtime._SESSIONS[_key(ws)]["transcript"]
        assert any("🎧×1" in m["text"] for m in transcript if m["role"] == "user")
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_handle_tts_message(monkeypatch):
    import src.llm.client as llmmod
    from src.web.routers import realtime

    class FakeLLM:
        async def tts(self, text, voice=None, model=None):
            assert text == "读这句"
            return b"RIFFfakewav"

    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS(sid="s-tts")
    await realtime.handle_tts_message(ws, {"type": "agent_tts", "id": "t1", "text": "读这句"})
    audio = [m for m in ws.sent if m["type"] == "agent_tts_audio"]
    assert audio and audio[0]["id"] == "t1"
    assert audio[0]["data"].startswith("data:audio/wav;base64,")


@pytest.mark.asyncio
async def test_handle_tts_no_key(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ws = _FakeWS(sid="s-tts2")
    await realtime.handle_tts_message(ws, {"type": "agent_tts", "id": "t2", "text": "x"})
    assert any(m["type"] == "agent_tts_error" for m in ws.sent)


def test_web_agent_loads_project_instructions(monkeypatch, tmp_path):
    # 网页主 agent 也读 AGENTS.md（按当前工作目录）
    (tmp_path / "AGENTS.md").write_text("网页项目约定：保持简洁。", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    from src.web.routers import realtime
    agent = realtime._new_agent()
    assert "项目指令" in agent._system("plan") and "保持简洁" in agent._system("plan")


@pytest.mark.asyncio
async def test_session_persists_and_restores_across_restart(monkeypatch, tmp_path):
    # 一轮跑完 → 落盘；清空内存（=服务器重启）→ 同 sid 新连接从磁盘复原对话
    import src.llm.client as llmmod
    from src.web.routers import realtime
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "x")

    class FakeLLM:
        async def chat(self, messages, **k):
            return {"content": "持久化回复。"}

    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)
    ws = _FakeWS(sid="persist-1")
    try:
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "记住这句", "mode": "plan"})
        await _drain(ws)
        key = _key(ws)
        assert realtime._SESSIONS[key]["transcript"]               # 内存里有了
        # 磁盘也写了
        from src.web.session_store import load_session
        saved = load_session(str(tmp_path), key)
        assert saved and any("记住这句" in m["text"] for m in saved["transcript"])

        realtime._SESSIONS.clear()                                  # 模拟服务器重启：内存全没了
        ws2 = _FakeWS(sid="persist-1")                             # 同 sid 重新连
        sess = realtime._get_session(ws2)                          # 应从磁盘复原
        assert any("记住这句" in m["text"] for m in sess["transcript"])
        assert any("记住这句" in m.get("content", "") for m in sess["agent"].history)
    finally:
        _cleanup(ws)
        realtime._SESSIONS.pop(_key(ws), None)


# ---- 协议冻结（PR-1）：rid 回带 + 不合法入站按旧行为静默忽略 ----

@pytest.mark.asyncio
async def test_rid_echoed_on_all_turn_events(monkeypatch):
    """入站 agent 带 rid → 本回合全部出站事件（say/plan/emit/done）都回带同一 rid。"""
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS(sid="s-rid")
    _inject_session(ws, _PlanAgent())
    try:
        await realtime.handle_agent_message(
            ws, {"type": "agent", "text": "hi", "mode": "plan", "rid": "r-42"})
        await _drain(ws)
        turn_events = [m for m in ws.sent if m["type"].startswith("agent_") and m["type"] != "agent_queue"]
        assert turn_events and all(m.get("rid") == "r-42" for m in turn_events), turn_events
        assert any(m["type"] == "agent_done" for m in turn_events)
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_no_rid_means_no_rid_field(monkeypatch):
    """不带 rid 的客户端（现网 agent.html）：事件里**不出现** rid 字段，与从前逐字节一致。"""
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS(sid="s-norid")
    _inject_session(ws, _EchoAgent())
    try:
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "hi", "mode": "plan"})
        await _drain(ws)
        assert ws.sent and all("rid" not in m for m in ws.sent), ws.sent
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_rid_echoed_on_early_errors(monkeypatch):
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS(sid="s-riderr")
    try:
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "  ", "rid": "r-e"})
        err = [m for m in ws.sent if m["type"] == "agent_error"]
        assert err and err[0]["rid"] == "r-e"                  # 空输入的早退错误也回带
    finally:
        _cleanup(ws)


class _ThinkingAgent:
    """带思维链的假 agent：验证 want_reasoning 时 reasoning_cb 被接上、事件发出。"""
    async def run_turn(self, text, mode, say, emit, stream_cb, images=None, audio=None,
                       reasoning_cb=None):
        if reasoning_cb:
            reasoning_cb("想一想")
        emit("答案")


class _HookLifecycleAgent:
    """Emit one Hook lifecycle through the same holder used by MainAgent."""

    def __init__(self):
        self._web_tool_event_holder = {"fn": None}
        self.plan = []

    async def run_turn(self, text, mode, say, emit, stream_cb, images=None, audio=None,
                       reasoning_cb=None):
        callback = self._web_tool_event_holder["fn"]
        callback("hook_start", {
            "id": "hook-visible-1", "name": "format", "event": "post_tool_use",
            "tool": "edit_file", "status": "running",
        })
        callback("hook_finish", {
            "id": "hook-visible-1", "name": "format", "event": "post_tool_use",
            "tool": "edit_file", "status": "succeeded", "message": "formatted",
            "duration_ms": 12, "stop_execution": False,
        })
        emit("完成")


@pytest.mark.asyncio
async def test_hook_lifecycle_is_a_structured_persisted_activity(monkeypatch):
    from src.web.routers import realtime

    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS(sid="s-hook-lifecycle")
    _inject_session(ws, _HookLifecycleAgent())
    try:
        await realtime.handle_agent_message(
            ws, {"type": "agent", "text": "format", "rid": "r-hook"})
        await _drain(ws)
        hook_events = [message for message in ws.sent if message["type"] == "agent_hook"]
        assert [message["status"] for message in hook_events] == ["running", "succeeded"]
        assert hook_events[0]["id"] == hook_events[1]["id"] == "hook-visible-1"
        assert hook_events[1]["message"] == "formatted"
        assert all(message["rid"] == "r-hook" and message["seq"] > 0 for message in hook_events)
        saved = realtime._SESSIONS[realtime._session_key(ws)]["activities"]
        assert [message["status"] for message in saved if message["type"] == "agent_hook"] == [
            "running", "succeeded",
        ]
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_git_review_change_poll_publishes_structured_session_event(tmp_path, monkeypatch):
    from src.web.routers import realtime

    subprocess.run(["git", "-C", str(tmp_path), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "watch@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Watch Test"], check=True)
    (tmp_path / "watch.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "watch.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "base"], check=True)
    monkeypatch.setenv("VORTOCODE_CHANGE_SOURCE_STORE", str(tmp_path.parent / "watch-source.json"))
    ws = _FakeWS(sid="git-watch")
    key = _key(ws)
    realtime._SESSIONS[key] = {
        "agent": object(), "transcript": [], "activities": [], "last": 0.0,
        "repo_root": str(tmp_path), "persist_events": False,
    }
    realtime._SESSION_SUBSCRIBERS[key] = {ws}
    try:
        assert await realtime._poll_git_review_change(key, str(tmp_path)) is False
        (tmp_path / "watch.txt").write_text("after\n", encoding="utf-8")
        assert await realtime._poll_git_review_change(key, str(tmp_path)) is True
        event = next(item for item in ws.sent if item["type"] == "git_review_changed")
        assert event["files"] == 1 and event["paths"] == ["watch.txt"]
        assert len(event["baseline"]) == 64 and event["seq"] > 0
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_want_reasoning_streams_reasoning_events(monkeypatch):
    """入站 agent 带 want_reasoning=True → 回合发 agent_reasoning 事件（且回带 rid）。"""
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS(sid="s-think")
    _inject_session(ws, _ThinkingAgent())
    try:
        await realtime.handle_agent_message(
            ws, {"type": "agent", "text": "hi", "rid": "r-t", "want_reasoning": True})
        await _drain(ws)
        think = [m for m in ws.sent if m["type"] == "agent_reasoning"]
        assert think and think[0]["text"] == "想一想" and think[0]["rid"] == "r-t"
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_no_want_reasoning_sends_none(monkeypatch):
    """不带 want_reasoning：不发 agent_reasoning（web 前端不用不订阅，省流量）。"""
    from src.web.routers import realtime
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    ws = _FakeWS(sid="s-nothink")
    _inject_session(ws, _ThinkingAgent())
    try:
        await realtime.handle_agent_message(ws, {"type": "agent", "text": "hi"})
        await _drain(ws)
        assert not any(m["type"] == "agent_reasoning" for m in ws.sent)
    finally:
        _cleanup(ws)


@pytest.mark.asyncio
async def test_unregistered_inbound_silently_ignored():
    """未登记类型/缺必填的入站消息：不抛、不回——与从前"未知类型掉落"同效。"""
    from src.web.routers import realtime
    ws = _FakeWS(sid="s-junk")
    await realtime.handle_websocket_message(ws, {"type": "made_up_type", "x": 1})
    await realtime.handle_websocket_message(ws, {"type": "agent_confirm_response"})   # 缺 id
    await realtime.handle_websocket_message(ws, {"no": "type"})
    assert ws.sent == []


@pytest.mark.asyncio
async def test_ping_pong_and_init_version():
    from src.gateway import protocol as P
    from src.web.routers import realtime
    ws = _FakeWS(sid="s-ping")
    await realtime.handle_websocket_message(ws, {"type": "ping"})
    assert ws.sent == [{"type": "pong"}]
    evt = P.make_event(P.INIT, v=P.PROTOCOL_VERSION, data={})   # endpoint 的 init 构造式
    assert evt["v"] == P.PROTOCOL_VERSION


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
