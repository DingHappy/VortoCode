"""手机上的聊天进助理记录：IM 桥存完整对话与摘要，Desktop 经只读接口查看，动态里也有。"""
from __future__ import annotations

import asyncio

from src.gateway.session_store import load_session, save_session
from src.gateway.turn_bindings import TurnBindings
from src.gateway.turn_digest import recent_turns
from src.im.bridge import IMBridge


def _bridge(tmp_path, reply="宁波今天晴。"):
    bridge = IMBridge.__new__(IMBridge)
    bridge.repo_root = str(tmp_path)
    bridge._sid = "sid-telegram-42"
    bridge.workspace_scope = "general"
    bridge.mode = "plan"
    bridge._edits = False
    bridge._bindings = TurnBindings(session="im")

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


def test_liveness_reports_inbound_gate_without_ids(tmp_path):
    """「机器人装死」时能一眼看出是白名单挡人（真机：交互角色继承了 .env 里 '' → 全拒）。不暴露 id。"""
    bridge = IMBridge.__new__(IMBridge)
    bridge.repo_root, bridge._sid, bridge._turn_task, bridge._pending_msgs = str(tmp_path), "sid-telegram-42", None, []
    bridge.adapter = type("A", (), {"liveness": lambda self: {"connected": True}})()
    bridge.owner_id = "42"
    cases = [(False, set(), "blocked_all"), (False, {"7"}, "owner_missing"), (False, {"42"}, "ok"), (True, set(), "ok")]
    for notification_only, allow_from, expected in cases:
        bridge.notification_only, bridge.allow_from = notification_only, allow_from
        bridge._ignored, bridge._ignored_no_mention = 3, 0
        live = bridge.liveness()
        assert live["inbound"] == expected and live["ignored"] == 3
        assert "42" not in str({k: v for k, v in live.items() if k != "channel"})


def test_transcript_route_reads_one_local_session_read_only(tmp_path, monkeypatch):
    """常驻电脑连不上时在本机聊的：连回去后 Desktop 只读取出，由用户决定要不要带过去。"""
    from fastapi.testclient import TestClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    save_session(str(tmp_path), "sid-desk-1", [{"role": "user", "text": "帮我记下周三开会", "rid": "r1", "secret": 1},
                                              {"role": "assistant", "text": "记下了"}],
                 [{"role": "user", "content": "帮我记下周三开会"}], None, title="周三开会")
    save_session(str(tmp_path), "sid-telegram-42", [{"role": "user", "text": "在吗"}], [], None)
    from src.web.server import app

    client = TestClient(app)
    before = (tmp_path / ".vortocode").stat().st_mtime_ns
    body = client.get("/api/assistant/transcript/desk-1").json()
    assert body["title"] == "周三开会"
    assert body["transcript"] == [{"role": "user", "text": "帮我记下周三开会", "rid": "r1", "at": None},
                                  {"role": "assistant", "text": "记下了", "rid": None, "at": None}]
    assert client.get("/api/assistant/transcript/telegram-42").status_code == 404     # 手机聊天走自己的只读入口
    assert client.get("/api/assistant/transcript/nope").status_code == 404
    assert client.get("/api/assistant/transcript/..%2Fx").status_code == 404
    assert (tmp_path / ".vortocode").stat().st_mtime_ns == before
