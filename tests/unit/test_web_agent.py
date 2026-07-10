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

    async def run_turn(self, text, mode, say, emit, stream_cb, images=None, audio=None):
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
        turn_events = [m for m in ws.sent if m["type"].startswith("agent_")]
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
    assert evt["v"] == 1


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
