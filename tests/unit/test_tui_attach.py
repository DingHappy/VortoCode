"""TUI attach 模式（b3 PR-4）单测——协议事件 → UI 面映射、confirm 往返、回退语义。

复用 test_gateway_client 的 FakeServe（真 aiohttp WS 服务器，回环、脚本化回放），
用 textual run_test/pilot 驱动真 TUI。不触网、不跑模型。
"""

import pytest

pytest.importorskip("textual")
pytest.importorskip("aiohttp")

from textual.widgets import Input  # noqa: E402

from src.gateway import protocol as P  # noqa: E402
from src.tui.app import VortoCodeTUI  # noqa: E402
from tests.unit.test_gateway_client import FakeServe  # noqa: E402


async def _submit(app, pilot, text):
    inp = app.query_one("#prompt", Input)
    inp.focus()
    inp.value = text
    await pilot.press("enter")
    await pilot.pause()


async def _wait_for(app, pilot, needle, tries=100):
    for _ in range(tries):
        if any(needle in t for t in app.transcript):
            return True
        await pilot.pause(0.05)
    return False


@pytest.mark.asyncio
async def test_attach_turn_maps_protocol_events_to_ui(monkeypatch):
    """attach 回合：say→_chrome、plan→计划面板、emit→对话 log；全程不装配本地 agent。"""
    script = [
        (P.AGENT_SAY, {"text": "🔧 read_file src/x.py"}, True),
        (P.AGENT_PLAN, {"items": [{"step": "读代码", "status": "in_progress"}]}, True),
        (P.AGENT_REASONING, {"text": "想一想…"}, True),
        (P.AGENT_STREAM, {"text": "serve 端回复"}, True),
        (P.AGENT_EMIT, {"text": "serve 端回复"}, True),
        (P.AGENT_DONE, {}, True),
    ]
    async with FakeServe(script) as srv:
        app = VortoCodeTUI(repo_root=".", attach=srv.url)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "看看代码")
            assert await _wait_for(app, pilot, "serve 端回复"), app.transcript[-5:]
            joined = "\n".join(app.transcript)
            assert "read_file" in joined                 # say → _chrome
            assert "读代码" in joined and "计划" in joined  # plan → 计划面板
            assert "✓ 完成（serve）" in joined            # 收尾标记（serve 路径）
            assert app.agent is None                     # 关键：没装配本地 agent（serve 是唯一所有者）
    sent = [m for m in srv.received if m.get("type") == P.AGENT]
    assert sent and sent[0].get("want_reasoning") is True    # TUI 显示思维链 → 订阅 reasoning
    assert sent[0].get("rid")


@pytest.mark.asyncio
async def test_attach_confirm_round_trip_via_confirm_screen():
    """serve 端确认经协议回 TUI 弹窗：按 y → 应答 ok=True 回传。"""
    script = [
        (P.AGENT_CONFIRM, {"id": "c1", "text": "要跑 pytest -q，允许吗？"}, True),
        (P.AGENT_EMIT, {"text": "跑完了"}, True),
        (P.AGENT_DONE, {}, True),
    ]
    async with FakeServe(script) as srv:
        app = VortoCodeTUI(repo_root=".", attach=srv.url)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "跑下测试")
            for _ in range(100):                          # 等确认弹窗出现
                if len(app.screen_stack) > 1:
                    break
                await pilot.pause(0.05)
            assert len(app.screen_stack) > 1, "ConfirmScreen 没弹出来"
            await pilot.press("y")
            assert await _wait_for(app, pilot, "跑完了")
    resp = [m for m in srv.received if m.get("type") == P.AGENT_CONFIRM_RESPONSE]
    assert resp and resp[0]["id"] == "c1" and resp[0]["ok"] is True


@pytest.mark.asyncio
async def test_attach_serve_error_rendered_not_retried_locally():
    """serve 侧回合出错：如实渲染，**不回退**进程内重跑（防重复执行）。"""
    script = [(P.AGENT_ERROR, {"text": "server 炸了"}, True)]
    async with FakeServe(script) as srv:
        app = VortoCodeTUI(repo_root=".", attach=srv.url)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "hi")
            assert await _wait_for(app, pilot, "serve 回合出错")
            assert app.agent is None                     # 没有本地重跑


@pytest.mark.asyncio
async def test_attach_falls_back_in_process_when_serve_unreachable(monkeypatch):
    """serve 够不着（连接阶段失败）：提示一行、本回合回退进程内照跑。"""
    import src.llm.client as llmmod

    class FakeLLM:
        async def chat(self, messages, **k):
            return {"content": "本地兜底回复。"}

    monkeypatch.setattr(llmmod, "LLMClient", FakeLLM)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    app = VortoCodeTUI(repo_root=".", attach="http://127.0.0.1:9")
    async with app.run_test() as pilot:
        await _submit(app, pilot, "你好")
        assert await _wait_for(app, pilot, "本地兜底回复")
        joined = "\n".join(app.transcript)
        assert "未连上 serve" in joined                   # 回退有提示，不静默
