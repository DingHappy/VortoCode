"""协议客户端事件泵（gateway/client.py）+ CLI attach 模式单测。

用**真 aiohttp WS 服务器**（127.0.0.1 随机端口、脚本化回放协议事件）驱动客户端——
不触网（仅回环）、不跑模型。覆盖：完整回合渲染、rid 串台过滤、confirm 往返、
取消、错误收尾、连不上时 CLI 回退进程内。
"""

import asyncio
import json

import pytest

pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402

from src.gateway import protocol as P  # noqa: E402
from src.gateway.client import ProtocolClient, TurnOutcome, _ws_url  # noqa: E402


# ------------------------------------------------------------ 脚手架：脚本化 WS 服务器
class FakeServe:
    """收 agent 消息后按脚本回放事件（rid 自动回带）；记录收到的一切入站消息。"""

    def __init__(self, script=None, init_version=P.PROTOCOL_VERSION):
        self.script = script or []          # [(type, fields_without_rid, with_rid: bool)]
        self.init_version = init_version
        self.received: list = []
        self.url = None
        self._runner = None
        self._cancel_evt = asyncio.Event()

    async def _replay(self, ws, rid):
        for etype, fields, with_rid in self.script:
            if etype == "WAIT_CANCEL":                  # 特殊步骤：等 agent_cancel 再继续
                await self._cancel_evt.wait()
                continue
            out = {"type": etype, **fields}
            if with_rid and rid:
                out["rid"] = rid
            await ws.send_json(out)

    async def _handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": P.INIT, "v": self.init_version, "data": {}})
        replay = None
        async for msg in ws:                            # 接收循环不被回放阻塞（回放开并发任务，
            if msg.type != web.WSMsgType.TEXT:          # 否则 WAIT_CANCEL 等事件时没人收 cancel）
                continue
            evt = json.loads(msg.data)
            self.received.append(evt)
            if evt.get("type") == P.AGENT:
                replay = asyncio.ensure_future(self._replay(ws, evt.get("rid")))
            elif evt.get("type") == P.AGENT_CANCEL:
                self._cancel_evt.set()
        if replay is not None:
            replay.cancel()
        return ws

    async def _delete_handler(self, request):
        self.deleted.append(request.match_info["sid"])
        return web.json_response({"deleted": True})

    async def __aenter__(self):
        self.deleted: list = []
        app = web.Application()
        app.router.add_get("/ws", self._handler)
        app.router.add_delete("/api/agent/sessions/{sid}", self._delete_handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        return self

    async def __aexit__(self, *exc):
        await self._runner.cleanup()


# ------------------------------------------------------------ 泵：完整回合 + rid 过滤
@pytest.mark.asyncio
async def test_full_turn_renders_and_filters_foreign_events():
    script = [
        (P.AGENT_SAY, {"text": "🔧 read_file"}, True),
        (P.NOTICE, {"data": {"text": "广播——别串进回合"}}, False),      # 无 rid：必须被跳过
        (P.AGENT_STREAM, {"text": "你"}, True),
        (P.AGENT_STREAM, {"text": "你好"}, True),
        (P.AGENT_EMIT, {"text": "你好"}, True),
        (P.AGENT_DONE, {}, True),
    ]
    says, streams, emits = [], [], []
    async with FakeServe(script) as srv:
        async with ProtocolClient(srv.url, sid="t1") as pc:
            assert pc.server_version == P.PROTOCOL_VERSION      # init 的 v 被收下
            out = await pc.run_turn("hi", on_say=says.append,
                                    on_stream=streams.append, on_emit=emits.append)
    assert out.ok and out.status == "done" and out.reply == "你好"
    assert says == ["🔧 read_file"] and streams == ["你", "你好"] and emits == ["你好"]
    sent = srv.received[0]
    assert sent["type"] == P.AGENT and sent["text"] == "hi" and sent.get("rid")   # rid 随消息发出


@pytest.mark.asyncio
async def test_agent_diff_dispatches_to_on_diff():
    """agent_diff（确认前的结构化 diff 推送）→ on_diff(title, diff)；不接 on_diff 的老端安全跳过。"""
    script = [
        (P.AGENT_DIFF, {"diff": "+++ b/f.txt\n+x", "title": "待开 PR 的改动"}, True),
        (P.AGENT_EMIT, {"text": "ok"}, True),
        (P.AGENT_DONE, {}, True),
    ]
    diffs = []
    async with FakeServe(script) as srv:
        async with ProtocolClient(srv.url, sid="t-diff") as pc:
            out = await pc.run_turn("hi", on_diff=lambda t, d: diffs.append((t, d)))
    assert out.ok
    assert diffs == [("待开 PR 的改动", "+++ b/f.txt\n+x")]
    async with FakeServe(script) as srv:                    # 不传 on_diff：事件被忽略、回合照常收尾
        async with ProtocolClient(srv.url, sid="t-diff2") as pc:
            assert (await pc.run_turn("hi")).ok


@pytest.mark.asyncio
async def test_confirm_round_trip_approves_and_denies():
    for approve in (True, False):
        script = [
            (P.AGENT_CONFIRM, {"id": "c1", "text": "跑命令？"}, True),
            (P.AGENT_DONE, {}, True),
        ]

        async def _confirm(text, _ok=approve):
            assert "跑命令" in text
            return _ok

        async with FakeServe(script) as srv:
            async with ProtocolClient(srv.url) as pc:
                out = await pc.run_turn("x", confirm=_confirm)
        assert out.ok
        resp = [m for m in srv.received if m.get("type") == P.AGENT_CONFIRM_RESPONSE]
        assert resp and resp[0]["id"] == "c1" and resp[0]["ok"] is approve


@pytest.mark.asyncio
async def test_confirm_defaults_to_deny_without_callback():
    script = [(P.AGENT_CONFIRM, {"id": "c2", "text": "危险操作？"}, True),
              (P.AGENT_DONE, {}, True)]
    async with FakeServe(script) as srv:
        async with ProtocolClient(srv.url) as pc:
            out = await pc.run_turn("x")                        # 不给 confirm → 一律拒绝（安全优先）
    assert out.ok
    resp = [m for m in srv.received if m.get("type") == P.AGENT_CONFIRM_RESPONSE]
    assert resp and resp[0]["ok"] is False


@pytest.mark.asyncio
async def test_error_outcome():
    script = [(P.AGENT_EMIT, {"text": "半截"}, True),
              (P.AGENT_ERROR, {"text": "boom"}, True)]
    async with FakeServe(script) as srv:
        async with ProtocolClient(srv.url) as pc:
            out = await pc.run_turn("x")
    assert not out.ok and out.status == "error" and out.error == "boom" and out.reply == "半截"


@pytest.mark.asyncio
async def test_cancel_round_trip():
    script = [
        (P.AGENT_SAY, {"text": "跑着呢"}, True),
        ("WAIT_CANCEL", {}, False),                             # 等客户端发 agent_cancel
        (P.AGENT_CANCELLED, {"text": "已中断"}, True),
    ]
    says: list = []
    async with FakeServe(script) as srv:
        async with ProtocolClient(srv.url) as pc:
            turn = asyncio.ensure_future(pc.run_turn("x", on_say=says.append))
            while not says:                                     # 等回合真的开跑
                await asyncio.sleep(0.01)
            await pc.cancel()
            out = await asyncio.wait_for(turn, 5)
    assert out.status == "cancelled"
    assert any(m.get("type") == P.AGENT_CANCEL for m in srv.received)


@pytest.mark.asyncio
async def test_connect_refused_raises():
    with pytest.raises(Exception):  # noqa: B017 —— 连接类异常（拒连/超时），类型因平台而异
        async with ProtocolClient("http://127.0.0.1:9", connect_timeout=1.0):
            pass


def test_ws_url_normalization():
    assert _ws_url("http://h:1", "s") == "ws://h:1/ws?sid=s"
    assert _ws_url("https://h", None) == "wss://h/ws"
    assert _ws_url("h:8080/", "x") == "ws://h:8080/ws?sid=x"


def test_turn_outcome_ok():
    assert TurnOutcome("done").ok and not TurnOutcome("error").ok


# ------------------------------------------------------------ CLI attach：走 serve / 回退进程内
@pytest.mark.asyncio
async def test_cli_attach_runs_over_protocol_not_in_process(monkeypatch, capsys):
    """--attach 且 serve 在：回合走协议客户端，绝不装配进程内 agent。"""
    import src.cli as cli
    script = [(P.AGENT_EMIT, {"text": "来自 serve 的回复"}, True), (P.AGENT_DONE, {}, True)]

    def _boom(*a, **k):
        raise AssertionError("attach 成功时不该装配进程内 agent")

    monkeypatch.setattr(cli, "_build_headless_agent", _boom)
    async with FakeServe(script) as srv:
        reply = await cli.run_agent_headless("问点啥", attach=srv.url, quiet=True)
    assert reply == "来自 serve 的回复"
    assert "来自 serve 的回复" in capsys.readouterr().out       # 输出契约：回复进 stdout


@pytest.mark.asyncio
async def test_cli_attach_falls_back_when_serve_unreachable(monkeypatch, capsys):
    """--attach 但 serve 不在：提示一行、回退进程内照跑（连接阶段失败才回退）。"""
    import src.llm.client as llmmod
    import src.cli as cli

    class FakeLLM:
        async def chat(self, messages, **k):
            return {"content": "进程内兜底回复。"}

    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    reply = await cli.run_agent_headless("hi", attach="http://127.0.0.1:9", quiet=False)
    assert "进程内兜底回复" in reply
    err = capsys.readouterr().err
    assert "未连上 serve" in err                                 # 回退有提示，不静默


@pytest.mark.asyncio
async def test_cli_attach_version_mismatch_warns(capsys):
    script = [(P.AGENT_DONE, {}, True)]
    async with FakeServe(script, init_version=99) as srv:
        import src.cli as cli
        await cli.run_agent_headless("hi", attach=srv.url, quiet=False)
    assert "协议版本" in capsys.readouterr().err                 # v 不齐要警示


@pytest.mark.asyncio
async def test_cli_attach_fresh_start_resets_serve_session_but_continue_keeps(monkeypatch):
    """语义对齐进程内（每轮都存、-c 才续）：attach 固定用 serve 侧 cli 会话——
    无 -c 先删旧会话（全新开始），有 -c 不删（续上）。"""
    import src.cli as cli
    script = [(P.AGENT_DONE, {}, True)]
    async with FakeServe(script) as srv:
        await cli.run_agent_headless("hi", attach=srv.url, quiet=True)
        assert srv.deleted == ["cli"]                            # 无 -c：先重置 serve 侧会话
        await cli.run_agent_headless("hi", attach=srv.url, quiet=True, continue_session=True)
        assert srv.deleted == ["cli"]                            # -c：不再删（续上）
        sids = [m for m in srv.received if m.get("type") == P.AGENT]
        assert len(sids) == 2


def test_server_startup_loads_dotenv_via_llm_client(monkeypatch):
    """start_server 必须先导入 llm.client（触发 .env 加载）——否则 shell 没 export key 时，
    /agent 首回合的 key 早检查永远走"未配置"降级（llm.client 只会在会话创建后才被导入，
    而会话创建在检查之后）。真机 attach 验证抓到的坑。"""
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "src" / "web" / "server.py").read_text("utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "start_server")
    imports = [a.name for n in ast.walk(fn) if isinstance(n, ast.Import) for a in n.names]
    assert "src.llm.client" in imports, "start_server 里必须 import src.llm.client（.env 加载）"


@pytest.mark.asyncio
async def test_cli_attach_error_exits_nonzero():
    import src.cli as cli
    script = [(P.AGENT_ERROR, {"text": "server 炸了"}, True)]
    async with FakeServe(script) as srv:
        with pytest.raises(SystemExit) as ei:
            await cli.run_agent_headless("hi", attach=srv.url, quiet=True)
    assert ei.value.code == 1


# ------------------------------------------------------------ --attach 参数消歧（dogfood 首日抓的）
def test_disambiguate_attach_swallowed_prompt():
    """argparse 的 --attach [URL] 会把紧跟的任务文本吞成 URL——不像 URL 就换回 prompt。"""
    from src.cli import _disambiguate_attach
    assert _disambiguate_attach(None, "修个 bug") == ("修个 bug", "")     # 被吞的任务文本 → 换回
    assert _disambiguate_attach(None, "http://127.0.0.1:9090") == (None, "http://127.0.0.1:9090")
    assert _disambiguate_attach(None, "localhost:8080") == (None, "localhost:8080")
    assert _disambiguate_attach("任务", "") == ("任务", "")               # 裸 --attach + 正常 prompt
    assert _disambiguate_attach("任务", "http://h:1") == ("任务", "http://h:1")
    assert _disambiguate_attach(None, "") == (None, "")                   # 裸 --attach、stdin 读 prompt
