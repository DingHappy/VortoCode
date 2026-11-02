"""手机 App 对话（ADR-012 阶段 A）：网关核对签名后经本机专用接口转给常驻助理跑回合；
回合中途的确认变待批准卡，App 签名批准后继续。"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.gateway import app_turns as turns_module
from src.gateway.app_turns import AppTurnError, AppTurns

# —— 回合与待批准卡 ——


class FakeAgent:
    """run_turn 时按脚本：可选地请求一次确认，再回复。"""

    def __init__(self, confirm, ask: str | None):
        self.confirm, self.ask, self.history = confirm, ask, []

    async def run_turn(self, text, mode="plan", **_kwargs):
        if self.ask is not None:
            ok = await self.confirm(self.ask)
            return "已记下" if ok else "没有记"
        return f"收到：{text}"


@pytest.fixture
def shared_chat(monkeypatch):
    """把主要聊天宿主换成直接跑：拿空历史跑 run，返回回复。"""
    seen = []

    async def run_external_turn(root, *, source, user_text, rid, run, activities=None):
        seen.append((source, user_text))
        _history, reply = await run([])
        return reply

    import src.gateway.main_conversation as main_conversation
    monkeypatch.setattr(main_conversation, "run_external_turn", run_external_turn)
    return seen


def _turns(tmp_path, ask=None, **kwargs):
    agents = []

    def build(confirm):
        agents.append(FakeAgent(confirm, ask))
        return agents[-1]
    return AppTurns(str(tmp_path), build, **kwargs), agents


async def _settle(turn, states=("done", "failed", "busy", "waiting")):
    for _ in range(200):
        if turn.state in states:
            return
        await asyncio.sleep(0.01)


async def test_turn_goes_into_the_main_chat_as_the_app_entry(tmp_path, shared_chat):
    turns, _ = _turns(tmp_path)
    turn = turns.start("req-00000001", "  明天早上提醒我交周报 ")
    assert turns.start("req-00000001", "重发") is turn                 # 网络重发只起一次
    await _settle(turn, ("done",))
    assert turn.state == "done" and turn.reply == "收到：明天早上提醒我交周报"
    assert shared_chat == [("app", "明天早上提醒我交周报")]


async def test_card_waits_for_a_signed_decision_then_the_turn_continues(tmp_path, shared_chat):
    turns, _ = _turns(tmp_path, ask="把「交周报」记成目标？")
    turn = turns.start("req-00000002", "记一下")
    await _settle(turn, ("waiting",))
    [card] = turn.public()["cards"]
    assert card["status"] == "pending" and card["risk"] == "normal"
    with pytest.raises(AppTurnError):
        turns.decide(turn.rid, card["id"], True, "0" * 64, "device")      # 摘要对不上：卡片内容变了
    turns.decide(turn.rid, card["id"], True, card["digest"], "device")
    await _settle(turn, ("done",))
    assert turn.reply == "已记下" and turn.public()["cards"][0]["status"] == "approved"
    with pytest.raises(AppTurnError):
        turns.decide(turn.rid, card["id"], False, card["digest"], "device")  # 已处理过


async def test_unanswered_card_expires_as_a_denial(tmp_path, shared_chat):
    turns, _ = _turns(tmp_path, ask="记吗？", card_timeout=0.05)
    turn = turns.start("req-00000003", "记一下")
    await _settle(turn, ("done",))
    assert turn.reply == "没有记" and turn.public()["cards"][0]["status"] == "expired"


async def test_high_risk_card_needs_the_face_id_key(tmp_path, shared_chat):
    turns, _ = _turns(tmp_path, ask="对外发送？")
    turn = turns.start("req-00000004", "发出去")
    await _settle(turn, ("waiting",))
    turn.cards[0].risk = "high"                   # 阶段 B 才会出现；规则先立住
    card = turn.cards[0].public(turn.rid)
    with pytest.raises(AppTurnError):
        turns.decide(turn.rid, card["id"], True, card["digest"], "device")
    turns.decide(turn.rid, card["id"], True, card["digest"], "approval")
    await _settle(turn, ("done",))


async def test_busy_main_chat_is_reported_not_swallowed(tmp_path, monkeypatch):
    class MainConversationBusy(RuntimeError):
        pass

    async def busy(*_args, **_kwargs):
        raise MainConversationBusy("主要聊天里还有回合没结束，稍后再发一次")

    import src.gateway.main_conversation as main_conversation
    monkeypatch.setattr(main_conversation, "run_external_turn", busy)
    turns, _ = _turns(tmp_path)
    turn = turns.start("req-00000005", "你好")
    await _settle(turn, ("busy",))
    assert "稍后" in turn.error


def test_bad_requests_are_refused(tmp_path):
    turns, _ = _turns(tmp_path)
    for request_id, text in (("short", "hi"), ("req-00000006", ""), ("req-00000006", "x" * 5000)):
        with pytest.raises(AppTurnError):
            turns.start(request_id, text)


# —— 常驻助理上的本机专用接口 ——


@pytest.fixture
def relay_app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "admin-token")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    monkeypatch.setenv("VORTOCODE_APP_RELAY_TOKEN", "relay-token")
    from src.web.routers import app_relay
    turns, _ = _turns(tmp_path)
    monkeypatch.setattr(app_relay, "_turns", turns)
    from src.web.server import app
    return app


def test_relay_routes_need_the_relay_token_and_a_loopback_peer(relay_app, shared_chat, tmp_path):
    from tests.unit.test_relay_envelope import envelope, phone
    device_id, device, _ = phone(tmp_path)
    local = TestClient(relay_app, client=("127.0.0.1", 50000))
    remote = TestClient(relay_app)

    def body(request_id):
        return {"envelope": envelope(device_id, device, "/api/mobile/native/chat/send",
                                     {"request_id": request_id, "text": "你好"})}
    assert remote.post("/api/assistant/app-relay/turn", json=body("req-00000007"),
                       headers={"Authorization": "Bearer relay-token"}).status_code == 404
    assert local.post("/api/assistant/app-relay/turn", json=body("req-00000008"),
                      headers={"Authorization": "Bearer admin-token"}).status_code == 404   # 主令牌进不来
    started = local.post("/api/assistant/app-relay/turn", json=body("req-00000009"),
                         headers={"Authorization": "Bearer relay-token"})
    assert started.status_code == 200 and started.json()["rid"].startswith("app-")
    # 只有令牌、没有手机签名：拒
    assert local.post("/api/assistant/app-relay/turn", json={"request_id": "req-00000010", "text": "x"},
                      headers={"Authorization": "Bearer relay-token"}).status_code == 401
    # 专用令牌也进不了别的接口
    assert local.get("/api/assistant/goals", headers={"Authorization": "Bearer relay-token"}).status_code == 401


def test_relay_target_must_be_loopback(monkeypatch):
    from src.gateway.app_relay_client import RelayUnavailable, _target
    monkeypatch.setenv("VORTOCODE_APP_RELAY_TOKEN", "t")
    for url in ("https://127.0.0.1:8081", "http://192.168.10.97:8081", "http://evil.test", ""):
        monkeypatch.setenv("VORTOCODE_APP_RELAY_URL", url)
        with pytest.raises(RelayUnavailable):
            _target()
    monkeypatch.setenv("VORTOCODE_APP_RELAY_URL", "http://127.0.0.1:8081")
    assert _target() == ("http://127.0.0.1:8081", "t")


# —— 手机网关上的对话路由 ——

from tests.unit.test_mobile_browser import native_handoff, setup  # noqa: E402,F401
from tests.unit.test_mobile_owner import Key, bind, owner, signed  # noqa: E402,F401


@pytest.fixture
def relayed(monkeypatch):
    calls = []

    async def relay(path, body, *, client=None):
        calls.append((path, body))
        if path.endswith("/turn"):
            return 200, {"rid": "app-" + "a" * 16, "state": "running", "cards": []}
        if path.endswith("/chat"):
            return 200, {"registered": True, "items": [{"role": "user", "text": "hi", "source": "telegram"}]}
        return 200, {"ok": True}

    import src.gateway.app_relay_client as client
    monkeypatch.setattr(client, "relay", relay)
    return calls


def test_send_requires_a_device_signature_and_is_forwarded(owner, relayed):  # noqa: F811
    client, headers, device_id, _root = owner
    _, device, _ = bind(client, headers)
    payload = {"request_id": "req-00000008", "text": "帮我看看今天的目标"}
    assert client.post("/api/mobile/native/chat/send", json=payload, headers=headers).status_code == 401
    response = signed(client, headers, device_id, "/api/mobile/native/chat/send", payload, device)
    assert response.status_code == 200 and response.json()["rid"] == "app-" + "a" * 16
    [(path, body)] = relayed
    env = body["envelope"]                                   # 转发的是签过名的原始请求，不是摘出来的参数
    assert path == "/api/assistant/app-relay/turn" and env["path"] == "/api/mobile/native/chat/send"
    import base64, json as _json
    assert _json.loads(base64.b64decode(env["body"])) == payload and env["signature"]


def test_reading_the_chat_needs_a_trusted_key(owner, relayed):  # noqa: F811
    client, headers, _device_id, _root = owner
    assert client.get("/api/mobile/native/chat", headers=headers).status_code == 403   # 还没绑钥匙
    bind(client, headers)
    assert client.get("/api/mobile/native/chat", headers=headers).json()["items"][0]["source"] == "telegram"
    assert client.get("/api/mobile/native/chat/turn", params={"rid": "../x"}, headers=headers).status_code == 404


def test_card_decision_is_signed_with_the_declared_key(owner, relayed):  # noqa: F811
    client, headers, device_id, _root = owner
    _, device, approval = bind(client, headers)
    body = {"rid": "app-" + "a" * 16, "card_id": "card-abc", "approve": True, "digest": "d" * 64, "key_kind": "approval"}
    wrong = signed(client, headers, device_id, "/api/mobile/native/chat/card", body, device)
    assert wrong.status_code == 401                         # 声明用批准钥匙却用日常钥匙签：拒
    right = signed(client, headers, device_id, "/api/mobile/native/chat/card", body, approval)
    assert right.status_code == 200 and relayed[-1][1]["envelope"]["key_kind"] == "approval"


def test_chat_is_unavailable_until_the_relay_is_configured(owner, monkeypatch):  # noqa: F811
    client, headers, device_id, _root = owner
    _, device, _ = bind(client, headers)
    monkeypatch.delenv("VORTOCODE_APP_RELAY_URL", raising=False)
    response = signed(client, headers, device_id, "/api/mobile/native/chat/send",
                      {"request_id": "req-00000009", "text": "hi"}, device)
    assert response.status_code == 503 and "开通" in response.json()["detail"]


_ = turns_module   # 保持模块导入（被 monkeypatch 的对象）
