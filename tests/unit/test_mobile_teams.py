"""手机上的任务小组与旁聊（ADR-017 M3）：网关核对会话与签名后转给常驻助理；
在群里说话、验收 / 退回要批准钥匙（Face ID），停止用日常钥匙；旁聊只读，不含主要聊天和 IM 会话。"""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from src.gateway.relay_envelope import EnvelopeRejected, open_envelope
from src.scheduling import team_runner
from src.workflow.tasks.team import TeamService
from tests.unit.test_relay_envelope import envelope, phone
from tests.unit.test_team_routes import MEMBERS, FakeRunner

RELAY = {"Authorization": "Bearer relay-token"}


@pytest.fixture
def assistant(tmp_path, monkeypatch):
    """常驻助理进程：有小组调度器、有本机专用接口、有一台钥匙已信任的手机。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOONE_API_TOKEN", "admin-token")
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    monkeypatch.setenv("VORTOONE_APP_RELAY_TOKEN", "relay-token")
    audits = []
    monkeypatch.setattr("src.gateway.audit.record_decision_audit", lambda *a, **k: audits.append(k))
    runner = FakeRunner(str(tmp_path))
    team_runner.set_team_runner(runner)
    from src.web.server import app
    device_id, device, approval = phone(tmp_path)
    yield TestClient(app, client=("127.0.0.1", 50000)), runner, audits, (device_id, device, approval)
    team_runner.set_team_runner(None)


def _op(client, keys, path, payload, *, approval=True):
    device_id, device, approval_key = keys
    key, kind = (approval_key, "approval") if approval else (device, "device")
    return client.post("/api/assistant/app-relay/team-op", headers=RELAY,
                       json={"envelope": envelope(device_id, key, path, payload, key_kind=kind)})


def test_team_list_and_chat_are_readable_with_taint_marked(assistant):
    client, runner, _audits, _keys = assistant
    service: TeamService = runner.service
    root = service.create("sid-main", objective="比较方案", acceptance=["给推荐"], members=MEMBERS)
    child = service.assign(root.id, "研究员", "查价格", "价格表")
    work = service.collaboration(service.get(root.id))
    work.start(child.id, 1)
    work.finish(child.id, 1, "A 10 元，来源 https://a.example", tainted=True)
    listed = client.post("/api/assistant/app-relay/teams", headers=RELAY, json={}).json()["teams"]
    assert [item["id"] for item in listed] == [root.id]
    assert listed[0]["tainted"] is True and listed[0]["members"][0] == {"name": "研究员", "role": "web-researcher"}
    assert "limits" not in listed[0] and "children" not in listed[0]           # 只给列表要的字段
    chat = client.post("/api/assistant/app-relay/team", headers=RELAY, json={"id": root.id}).json()
    assert chat["team"]["objective"] == "比较方案" and chat["members"][0]["name"] == "研究员"
    result = next(row for row in chat["messages"] if row["kind"] == "result")
    assert result["tainted"] is True and result["text"].startswith("A 10 元")
    for bad in ("../x", "", "a" * 65):
        assert client.post("/api/assistant/app-relay/team", headers=RELAY, json={"id": bad}).status_code == 404
    # 专用令牌以外进不来
    assert client.post("/api/assistant/app-relay/teams", json={}).status_code == 404


def test_message_and_decisions_need_the_face_id_key_stop_does_not(assistant, tmp_path):
    client, runner, audits, keys = assistant
    service = runner.service
    root = service.create("sid-main", objective="比较方案", acceptance=["给推荐"], members=MEMBERS)
    service.ask_owner(root.id, "预算多少？")
    weak = _op(client, keys, "/api/mobile/native/team/message", {"team_id": root.id, "text": "500 以内"}, approval=False)
    assert weak.status_code == 401 and service.get(root.id).status == "waiting"    # 日常钥匙签的回话：拒
    ok = _op(client, keys, "/api/mobile/native/team/message", {"team_id": root.id, "text": "500 以内"})
    assert ok.status_code == 200 and ok.json()["team"]["status"] == "active"
    assert runner.woken[-1] == (root.id, "主人在群里说话了")
    service.submit(root.id, "推荐 A", [{"criterion": 0, "evidence": "A 最便宜"}])
    assert _op(client, keys, "/api/mobile/native/team/message", {"team_id": root.id, "text": "再改改"}).status_code == 409
    no_reason = _op(client, keys, "/api/mobile/native/team/decide", {"team_id": root.id, "accept": False, "note": " "})
    assert no_reason.status_code == 422
    weak_reject = _op(client, keys, "/api/mobile/native/team/decide",
                      {"team_id": root.id, "accept": False, "note": "再比较一家"}, approval=False)
    assert weak_reject.status_code == 401                                         # 退回也要 Face ID
    back = _op(client, keys, "/api/mobile/native/team/decide", {"team_id": root.id, "accept": False, "note": "再比较一家"})
    assert back.status_code == 200 and back.json()["team"]["status"] == "active"
    stopped = _op(client, keys, "/api/mobile/native/team/stop", {"team_id": root.id}, approval=False)
    assert stopped.status_code == 200 and stopped.json()["team"]["status"] == "cancelled"
    assert [audit["operation"][:6] for audit in audits] == ["在手机上在群", "在手机上退回", "在手机上停止"]


def test_accept_closes_the_team(assistant):
    client, runner, audits, keys = assistant
    service = runner.service
    root = service.create("sid-main", objective="比较方案", acceptance=["给推荐"], members=MEMBERS)
    service.submit(root.id, "推荐 A", [{"criterion": 0, "evidence": "A 最便宜"}])
    done = _op(client, keys, "/api/mobile/native/team/decide", {"team_id": root.id, "accept": True})
    assert done.status_code == 200 and done.json()["team"]["status"] == "done"
    assert audits[-1]["operation"].startswith("在手机上验收通过")


def test_envelope_requires_the_approval_key_for_team_writes(tmp_path):
    device_id, device, approval = phone(tmp_path)
    for path in ("/api/mobile/native/team/message", "/api/mobile/native/team/decide"):
        with pytest.raises(EnvelopeRejected):
            open_envelope(str(tmp_path), envelope(device_id, device, path, {"team_id": "t", "accept": False}))
        op, _data, _who, kind = open_envelope(str(tmp_path), envelope(device_id, approval, path, {"team_id": "t"},
                                                                      key_kind="approval"))
        assert kind == "approval" and op.startswith("team_")
    op, _data, _who, kind = open_envelope(str(tmp_path), envelope(device_id, device, "/api/mobile/native/team/stop",
                                                                  {"team_id": "t"}))
    assert (op, kind) == ("team_stop", "device")


def test_side_chats_exclude_the_main_chat_and_phone_sessions(assistant, tmp_path):
    client, _runner, _audits, _keys = assistant
    from src.gateway.main_conversation import claim_main_chat
    from src.gateway.session_store import save_session

    def chat(sid, text):
        save_session(str(tmp_path), f"sid-{sid}", [{"role": "user", "text": text, "at": "2026-11-07T10:00:00"},
                                                    {"role": "assistant", "text": "好的"}], [], None)
    chat("main-0001", "主要聊天里的话")
    chat("side-0001", "查一下机票")
    chat("telegram-42", "IM 里的话")
    claim_main_chat(str(tmp_path), "sid-main-0001")
    listed = client.post("/api/assistant/app-relay/side-chats", headers=RELAY, json={}).json()["chats"]
    assert [item["sid"] for item in listed] == ["side-0001"] and listed[0]["messages"] == 2
    read = client.post("/api/assistant/app-relay/side-chat", headers=RELAY, json={"sid": "side-0001"}).json()
    assert [item["text"] for item in read["items"]] == ["查一下机票", "好的"]
    for other in ("main-0001", "telegram-42", "../etc", "missing"):
        assert client.post("/api/assistant/app-relay/side-chat", headers=RELAY, json={"sid": other}).status_code == 404


# —— 手机网关上的路由 ——

from tests.unit.test_mobile_browser import native_handoff, setup  # noqa: E402,F401
from tests.unit.test_mobile_owner import Key, bind, owner, signed  # noqa: E402,F401


@pytest.fixture
def relayed(monkeypatch):
    calls = []

    async def relay(path, body, *, client=None):
        calls.append((path, body))
        return 200, {"teams": [], "chats": [], "team": {"status": "active"}}

    import src.gateway.app_relay_client as relay_client
    monkeypatch.setattr(relay_client, "relay", relay)
    return calls


def test_gateway_reads_need_a_trusted_key_and_valid_ids(owner, relayed):  # noqa: F811
    client, headers, _device_id, _root = owner
    assert client.get("/api/mobile/native/teams", headers=headers).status_code == 403     # 还没绑钥匙
    bind(client, headers)
    assert client.get("/api/mobile/native/teams", headers=headers).status_code == 200
    assert client.get("/api/mobile/native/side-chats", headers=headers).status_code == 200
    assert client.get("/api/mobile/native/team", params={"id": "task-0000000001"}, headers=headers).status_code == 200
    assert client.get("/api/mobile/native/team", params={"id": "../x"}, headers=headers).status_code == 404
    assert client.get("/api/mobile/native/side-chat", params={"sid": "a/b"}, headers=headers).status_code == 404
    assert [path for path, _ in relayed] == ["/api/assistant/app-relay/teams", "/api/assistant/app-relay/side-chats",
                                             "/api/assistant/app-relay/team"]
    assert relayed[-1][1] == {"id": "task-0000000001"}


def test_gateway_team_writes_are_signed_with_the_right_key(owner, relayed):  # noqa: F811
    client, headers, device_id, _root = owner
    _, device, approval = bind(client, headers)
    message = {"team_id": "task-0000000001", "text": "500 以内"}
    assert signed(client, headers, device_id, "/api/mobile/native/team/message", message, device).status_code == 401
    assert not relayed
    assert signed(client, headers, device_id, "/api/mobile/native/team/message", message, approval).status_code == 200
    env = relayed[-1][1]["envelope"]
    assert relayed[-1][0] == "/api/assistant/app-relay/team-op" and env["key_kind"] == "approval"
    assert json.loads(base64.b64decode(env["body"])) == message
    stop = {"team_id": "task-0000000001"}
    assert signed(client, headers, device_id, "/api/mobile/native/team/stop", stop, device).status_code == 200
    assert relayed[-1][1]["envelope"]["key_kind"] == "device"
