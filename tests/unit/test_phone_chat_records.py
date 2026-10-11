"""手机上的聊天进助理记录：IM 桥存完整对话与摘要，Desktop 经只读接口查看，动态里也有。"""
from __future__ import annotations

import asyncio

from src.gateway.session_store import load_session, save_session
from src.gateway.turn_digest import recent_turns
from src.im.bridge import IMBridge


def _bridge(tmp_path, reply="宁波今天晴。"):
    bridge = IMBridge.__new__(IMBridge)
    bridge.repo_root = str(tmp_path)
    bridge._sid = "sid-telegram-42"
    bridge.workspace_scope = "general"
    bridge.mode = "plan"
    bridge._edits = False
    bridge._confirm_holder, bridge._progress_holder = {"fn": None}, {"fn": None}

    class _Agent:
        history: list = []
        plan = None

        async def run_turn(self, prompt, **_kwargs):
            return reply

        def _client(self):
            class _LLM:
                async def chat(self, *_a, **_k):
                    return {"content": '{"title": "查询宁波天气", "outcome": "今天晴"}'}
            return _LLM()

    bridge.agent = _Agent()
    sent = []

    class _Adapter:
        async def send_text(self, text):
            sent.append(text)
            return "1"
    bridge.adapter = _Adapter()

    async def _safe_send(text):
        sent.append(text)
    bridge._safe_send = _safe_send
    bridge._make_confirm = lambda q: (lambda message: asyncio.sleep(0, False))
    return bridge, sent


async def test_turn_is_recorded_with_digest_and_listed(tmp_path, monkeypatch):
    monkeypatch.setattr("src.gateway.agent_session.session_tool_names", lambda agent: [], raising=False)
    monkeypatch.setattr("src.gateway.agent_session.session_behavior_fp", lambda agent: "", raising=False)
    bridge, sent = _bridge(tmp_path)
    await bridge._run_turn("宁波天气")
    for _ in range(50):
        await asyncio.sleep(0.01)
        saved = load_session(str(tmp_path), "sid-telegram-42") or {}
        if any(item.get("digest") for item in saved.get("transcript") or []):
            break
    transcript = saved["transcript"]
    assert [item["role"] for item in transcript] == ["user", "assistant"]
    assert transcript[0]["text"] == "宁波天气" and transcript[1]["digest"]["title"] == "查询宁波天气"
    assert sent == ["宁波今天晴。"]
    turn = recent_turns(str(tmp_path))[0]
    assert turn["sid"] == "telegram-42" and turn["title"] == "查询宁波天气"


def test_phone_chats_route_is_read_only_listing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    save_session(str(tmp_path), "sid-telegram-42", [{"role": "user", "text": "在吗", "rid": "r1", "at": "x", "secret": 1}],
                 [{"role": "user", "content": "在吗"}], None, title="📱 Telegram")
    save_session(str(tmp_path), "sid-desk", [{"role": "user", "text": "桌面", "rid": "r1"}],
                 [{"role": "user", "content": "桌面"}], None)
    from src.web.server import app

    chats = TestClient(app).get("/api/assistant/phone-chats").json()["chats"]
    assert [chat["sid"] for chat in chats] == ["telegram-42"]
    assert chats[0]["title"] == "📱 Telegram" and chats[0]["transcript"] == [{"role": "user", "text": "在吗", "rid": "r1", "at": "x"}]
