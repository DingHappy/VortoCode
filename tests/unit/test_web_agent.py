"""Web 控制台的主 agent WebSocket 处理（src/web/routers/realtime.py）单测。

用假 WebSocket + 假 LLM 直接驱动 handle_agent_message，不起真实服务、不触网。
"""

import pytest

pytest.importorskip("fastapi")


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


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
