"""手机 App 作为主人终端的写入：默认关闭、每个写请求都要设备签名、批准只认生物识别钥匙且绑定动作摘要。"""

import base64
import json
import secrets
import time
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from src.gateway import mobile_signing
from src.scheduling import codex_watch
from src.workflow.workbench.personal_goals import PersonalGoals
from tests.unit.test_mobile_browser import native_handoff, setup  # noqa: F401 —— 复用原生会话夹具


class Key:
    def __init__(self):
        self.private = ec.generate_private_key(ec.SECP256R1())
        raw = self.private.public_key().public_bytes(serialization.Encoding.X962,
                                                     serialization.PublicFormat.UncompressedPoint)
        self.public = base64.b64encode(raw).decode()            # 与 iOS SecKeyCopyExternalRepresentation 相同


@pytest.fixture
def owner(setup, monkeypatch):  # noqa: F811
    client, sessions, now, calls, root, _ = setup
    monkeypatch.setenv("VORTOONE_MOBILE_WRITES", "1")
    token = client.post("/api/mobile/native/exchange", json=native_handoff(client)).json()["token"]
    headers = {"Authorization": "Bearer " + token}
    device_id = client.get("/api/mobile/native/bootstrap", headers=headers).json()["device"]["id"]
    return client, headers, device_id, root


def signed(client, headers, device_id, path, payload, key, *, timestamp=None, nonce=None, tamper=None):
    body = json.dumps(payload).encode()
    stamp = str(int(time.time()) if timestamp is None else timestamp)
    nonce = nonce or secrets.token_urlsafe(16)
    message = mobile_signing.message(method="POST", path=path, body=body, timestamp=stamp, nonce=nonce,
                                     device_id=device_id)
    signature = base64.b64encode(key.private.sign(message, ec.ECDSA(hashes.SHA256()))).decode()
    return client.post(path, content=tamper or body, headers={
        **headers, "Content-Type": "application/json", "X-VC-Timestamp": stamp, "X-VC-Nonce": nonce,
        "X-VC-Signature": signature})


def bind(client, headers, device=None, approval=None):
    device, approval = device or Key(), approval or Key()
    response = client.post("/api/mobile/native/keys", headers=headers,
                           json={"device_key": device.public, "approval_key": approval.public})
    return response, device, approval


def test_writes_are_closed_unless_the_server_opts_in(owner, monkeypatch):
    client, headers, device_id, _ = owner
    monkeypatch.delenv("VORTOONE_MOBILE_WRITES")
    response, *_ = bind(client, headers)
    assert response.status_code == 403
    assert client.get("/api/mobile/native/bootstrap", headers=headers).json()["capabilities"]["writes"] == []


def test_keys_bind_once_and_unlock_write_capabilities(owner):
    client, headers, device_id, _ = owner
    assert client.get("/api/mobile/native/bootstrap", headers=headers).json()["capabilities"]["writes"] == []
    response, device, approval = bind(client, headers)
    assert response.status_code == 200
    assert bind(client, headers, device, approval)[0].status_code == 200          # 同一对钥匙重试
    assert bind(client, headers)[0].status_code == 409                            # 换钥匙要先撤销设备
    assert client.get("/api/mobile/native/bootstrap", headers=headers).json()["capabilities"]["writes"] == \
        ["capture", "decide", "undo"]
    same = client.post("/api/mobile/native/keys", headers=headers,
                       json={"device_key": device.public, "approval_key": device.public})
    assert same.status_code == 409
    junk = client.post("/api/mobile/native/keys", headers={"Authorization": "Bearer nope"},
                       json={"device_key": device.public, "approval_key": approval.public})
    assert junk.status_code in (401, 403)


def test_points_off_the_curve_are_rejected():
    bogus = base64.b64encode(b"\x04" + b"\x01" * 64).decode()
    with pytest.raises(mobile_signing.SignatureRejected):
        mobile_signing.normalize_public_key(bogus)


def test_capture_records_an_owner_goal_once_and_can_be_undone(owner):
    client, headers, device_id, root = owner
    _, device, _ = bind(client, headers)
    payload = {"request_id": "cap-0001-abcd", "text": "周五前交周报"}
    first = signed(client, headers, device_id, "/api/mobile/native/capture", payload, device)
    assert first.status_code == 200
    again = signed(client, headers, device_id, "/api/mobile/native/capture", payload, device)   # 网络重试
    assert again.json()["goal_id"] == first.json()["goal_id"]
    [goal] = PersonalGoals(root).list()
    assert goal["objective"] == "周五前交周报" and goal["origins"]["objective"] == "owner"
    [op] = client.get("/api/mobile/native/changes", headers=headers).json()["ops"]
    assert op["source"] == "mobile" and "周五前交周报" in op["summary"]

    undone = signed(client, headers, device_id, "/api/mobile/native/undo", {"op_id": op["id"]}, device)
    assert undone.status_code == 200 and undone.json()["ok"] is True
    assert PersonalGoals(root).list() == []


@pytest.mark.parametrize("attack", ["wrong_key", "tampered", "stale", "replay", "unsigned"])
def test_capture_rejects_bad_signatures(owner, attack):
    client, headers, device_id, root = owner
    _, device, approval = bind(client, headers)
    payload = {"request_id": "cap-0002-abcd", "text": "买牛奶"}
    path = "/api/mobile/native/capture"
    if attack == "wrong_key":
        response = signed(client, headers, device_id, path, payload, approval)       # 批准钥匙不能代替日常钥匙
    elif attack == "tampered":
        response = signed(client, headers, device_id, path, payload, device,
                          tamper=json.dumps({**payload, "text": "转账给陌生人"}).encode())
    elif attack == "stale":
        response = signed(client, headers, device_id, path, payload, device, timestamp=int(time.time()) - 3600)
    elif attack == "replay":
        assert signed(client, headers, device_id, path, payload, device, nonce="n" * 20).status_code == 200
        PersonalGoals(root).withdraw(PersonalGoals(root).list()[0]["id"], 1)
        response = signed(client, headers, device_id, path, {**payload, "request_id": "cap-0003-abcd"}, device,
                          nonce="n" * 20)
    else:
        response = client.post(path, headers=headers, json=payload)
    assert response.status_code == 401
    assert PersonalGoals(root).list() == []


def _proposed_action(root):
    now = datetime.now(timezone.utc).isoformat()
    thread = {"id": "thread-1", "name": "写计算器", "cwd": "/tmp/calc", "status": "idle",
              "turn": {"id": "turn-1", "status": "completed", "request": "写 add"}}
    codex_watch._save_state(root, {"received_at": now, "threads": [thread]})
    codex_watch.hand_over(root, thread)                    # 只有交接过的会话才能拟动作（ADR-010）
    return codex_watch.propose_action(root, thread="thread-1", kind="message", text="继续实现 sub")


def test_approval_needs_the_biometric_key_and_the_current_action_digest(owner):
    client, headers, device_id, root = owner
    _, device, approval = bind(client, headers)
    action = _proposed_action(root)
    [listed] = client.get("/api/mobile/native/decisions", headers=headers).json()["decisions"]
    assert listed["id"] == action["id"] and listed["action_digest"] == mobile_signing.action_digest(action)

    path = "/api/mobile/native/decide"
    good = {"action_id": action["id"], "approve": True, "action_digest": listed["action_digest"]}
    assert signed(client, headers, device_id, path, good, device).status_code == 401      # 日常钥匙批不了
    stale = {**good, "action_digest": mobile_signing.action_digest({**action, "text": "删库"})}
    assert signed(client, headers, device_id, path, stale, approval).status_code == 409   # 批的不是现在这条
    assert codex_watch.load_actions(root)[0]["status"] == "proposed"

    approved = signed(client, headers, device_id, path, good, approval)
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert signed(client, headers, device_id, path, good, approval).status_code == 409    # 不能批两次


def test_rejecting_uses_the_everyday_key(owner):
    client, headers, device_id, root = owner
    _, device, _ = bind(client, headers)
    action = _proposed_action(root)
    response = signed(client, headers, device_id, "/api/mobile/native/decide",
                      {"action_id": action["id"], "approve": False}, device)
    assert response.status_code == 200 and response.json()["status"] == "cancelled"


def test_push_registration_is_signed_and_validated(owner):
    from src.gateway.mobile_devices import MobileDevices
    client, headers, device_id, root = owner
    _, device, approval = bind(client, headers)
    push_key = Key()
    good = {"apns_token": "ab" * 32, "push_key": push_key.public, "environment": "sandbox"}
    assert client.post("/api/mobile/native/push", headers=headers, json=good).status_code == 401      # 没签名
    assert signed(client, headers, device_id, "/api/mobile/native/push", good, approval).status_code == 401
    assert signed(client, headers, device_id, "/api/mobile/native/push", {**good, "environment": "x"},
                  device).status_code == 400
    assert signed(client, headers, device_id, "/api/mobile/native/push", good, device).status_code == 200
    [(target, registration)] = MobileDevices(root).push_targets()
    assert target == device_id and registration["token"] == "ab" * 32 and registration["key"] == push_key.public
