"""LLM 流式 token 输出测试（离线，假流式客户端）。"""

import json

import pytest

from src.agents.roles import DeveloperAgent


class FakeStreamLLM:
    """同时支持 stream()（逐块）与 chat()（整体）的假客户端。"""

    def __init__(self, full_text):
        self.full = full_text
        self.chunks = [full_text[i:i + 5] for i in range(0, len(full_text), 5)]

    async def stream(self, messages, model=None, temperature=None):
        for c in self.chunks:
            yield c

    async def chat(self, messages, model=None, temperature=None, **kw):
        return {"content": self.full}


class OnlyChatLLM:
    """只有 chat、没有 stream 的客户端，用于验证优雅降级。"""

    def __init__(self, full_text):
        self.full = full_text

    async def chat(self, messages, model=None, temperature=None, **kw):
        return {"content": self.full}


@pytest.mark.asyncio
async def test_developer_streams_tokens(tmp_path):
    payload = json.dumps({"files": [{"path": "a.py", "content": "x = 1\n"}]})
    dev = DeveloperAgent(llm_client=FakeStreamLLM(payload))
    tokens = []

    async def on_token(t):
        tokens.append(t)

    res = await dev.execute("写代码", context={"workspace": str(tmp_path), "on_token": on_token})

    assert res.success is True
    assert tokens                                  # 确实逐块回调了
    assert "".join(tokens) == payload              # 流式拼接 == 完整响应
    assert (tmp_path / "a.py").exists()            # 仍正确解析并写盘


@pytest.mark.asyncio
async def test_streaming_falls_back_when_unsupported(tmp_path):
    payload = json.dumps({"files": [{"path": "b.py", "content": "y = 2\n"}]})
    dev = DeveloperAgent(llm_client=OnlyChatLLM(payload))
    called = []

    res = await dev.execute("x", context={"workspace": str(tmp_path),
                                          "on_token": lambda t: called.append(t)})

    assert res.success is True
    assert (tmp_path / "b.py").exists()
    assert called == []                            # 无 stream 方法 → 不回调，退回 chat
