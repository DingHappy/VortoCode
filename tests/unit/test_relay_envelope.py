"""常驻助理再验一遍手机签名（纵深防御）：手机网关被攻破也伪造不了主人的请求。

网关只能转发手机真正签过的原始请求；助理验签、核对设备没撤销、时间新鲜、nonce 没用过，
操作由签进去的路径决定，批准类操作要批准钥匙。"""

import base64
import json
import secrets
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from src.gateway import mobile_signing
from src.gateway.mobile_devices import MobileDevices
from src.gateway.relay_envelope import EnvelopeRejected, open_envelope
from tests.unit.test_mobile_owner import Key


def phone(root):
    """一台已登记、钥匙已信任的手机：返回 (设备 id, 日常钥匙, 批准钥匙)。"""
    store = MobileDevices(str(root))
    device_id = store.issue("手机", 30)["device"]["id"]
    device, approval = Key(), Key()
    store.bind_keys(device_id, device.public, approval.public)
    return device_id, device, approval


def envelope(device_id, key, path, payload, *, key_kind="device", timestamp=None, nonce=None, body=None):
    raw = json.dumps(payload).encode() if body is None else body
    stamp = str(int(time.time()) if timestamp is None else timestamp)
    nonce = nonce or secrets.token_urlsafe(16)
    message = mobile_signing.message(method="POST", path=path, body=raw, timestamp=stamp, nonce=nonce,
                                     device_id=device_id)
    signature = base64.b64encode(key.private.sign(message, ec.ECDSA(hashes.SHA256()))).decode()
    return {"method": "POST", "path": path, "body": base64.b64encode(raw).decode(), "timestamp": stamp,
            "nonce": nonce, "signature": signature, "device_id": device_id, "key_kind": key_kind}


def test_a_genuine_request_opens_with_the_op_taken_from_the_signed_path(tmp_path):
    device_id, device, _ = phone(tmp_path)
    env = envelope(device_id, device, "/api/mobile/native/capture", {"text": "买牛奶", "request_id": "cap-0001-abcd"})
    op, data, who, kind = open_envelope(str(tmp_path), env)
    assert (op, data["text"], who, kind) == ("capture", "买牛奶", device_id, "device")


@pytest.mark.parametrize("attack", ["no_envelope", "tampered_body", "wrong_key", "stale", "unknown_path",
                                    "replay", "revoked", "device_key_for_dispatch_approval", "card_key_mismatch"])
def test_forgeries_are_rejected(tmp_path, attack):
    root = str(tmp_path)
    device_id, device, approval = phone(tmp_path)
    path, payload, key, kind = "/api/mobile/native/capture", {"text": "x", "request_id": "cap-0002-abcd"}, device, "device"
    if attack == "no_envelope":
        with pytest.raises(EnvelopeRejected):
            open_envelope(root, {"op": "dispatch", "args": {"dispatch_id": "dsp-0123456789", "approve": True}})
        return
    env = envelope(device_id, key, path, payload, key_kind=kind)
    if attack == "tampered_body":
        env["body"] = base64.b64encode(json.dumps({"text": "下发全部", "request_id": "cap-0002-abcd"}).encode()).decode()
    elif attack == "wrong_key":
        env = envelope(device_id, Key(), path, payload)
    elif attack == "stale":
        env = envelope(device_id, device, path, payload, timestamp=int(time.time()) - 3600)
    elif attack == "unknown_path":
        env = envelope(device_id, device, "/api/mobile/native/keys", payload)
    elif attack == "replay":
        open_envelope(root, env)
    elif attack == "revoked":
        MobileDevices(root).revoke(device_id)
    elif attack == "device_key_for_dispatch_approval":
        env = envelope(device_id, device, "/api/mobile/native/dispatch",
                       {"dispatch_id": "dsp-0123456789", "approve": True, "digest": "d" * 64})
    elif attack == "card_key_mismatch":
        env = envelope(device_id, device, "/api/mobile/native/chat/card",
                       {"rid": "app-" + "a" * 16, "card_id": "card-1", "approve": True, "digest": "d" * 64,
                        "key_kind": "approval"})
    with pytest.raises(EnvelopeRejected):
        open_envelope(root, env)


def test_the_gateway_cannot_choose_the_operation(tmp_path, monkeypatch):
    """网关拿着令牌、也拿着一个真签名的「随手记」请求，想送到下发任务单的接口：拒。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOONE_API_TOKEN", "admin-token")
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    monkeypatch.setenv("VORTOONE_APP_RELAY_TOKEN", "relay-token")
    device_id, device, _ = phone(tmp_path)
    from src.web.server import app
    local = TestClient(app, client=("127.0.0.1", 50000))
    auth = {"Authorization": "Bearer relay-token"}
    capture = envelope(device_id, device, "/api/mobile/native/capture", {"text": "x", "request_id": "cap-0003-abcd"})
    assert local.post("/api/assistant/app-relay/turn", headers=auth, json={"envelope": capture}).status_code == 401
    forged = local.post("/api/assistant/app-relay/op", headers=auth,
                        json={"op": "dispatch", "args": {"dispatch_id": "dsp-0123456789", "approve": True}})
    assert forged.status_code == 401
