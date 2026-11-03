"""给主人 App 的推送：正文端到端加密、APNs 令牌签名、失效令牌清理、与 IM 双发互不拖累。"""

import base64
import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.x963kdf import X963KDF

from src.gateway import mobile_push
from src.gateway.mobile_devices import MobileDevices


def _key_pair():
    private = ec.generate_private_key(ec.SECP256R1())
    raw = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return private, base64.b64encode(raw).decode()


def apple_ecies_decrypt(private, blob_b64):
    """按 iOS .eciesEncryptionStandardX963SHA256AESGCM 的定义解密（App 的通知扩展做的事）。"""
    blob = base64.b64decode(blob_b64)
    ephemeral_raw, sealed = blob[:65], blob[65:]
    ephemeral = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ephemeral_raw)
    shared = private.exchange(ec.ECDH(), ephemeral)
    key = X963KDF(algorithm=hashes.SHA256(), length=16, sharedinfo=ephemeral_raw).derive(shared)
    return AESGCM(key).decrypt(b"\x00" * 16, sealed, None).decode()


@pytest.fixture
def apns(tmp_path, monkeypatch):
    signer = ec.generate_private_key(ec.SECP256R1())
    key_file = tmp_path / "AuthKey_TEST.p8"
    key_file.write_bytes(signer.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
    monkeypatch.setenv("VORTOCODE_APNS_KEY_FILE", str(key_file))
    monkeypatch.setenv("VORTOCODE_APNS_KEY_ID", "KEY1234567")
    monkeypatch.setenv("VORTOCODE_APNS_TEAM_ID", "TEAM123456")
    monkeypatch.setenv("VORTOCODE_APNS_TOPIC", "com.vorto.vortocode.mobile")
    mobile_push._jwt_cache.clear()
    yield signer
    mobile_push._jwt_cache.clear()


def test_payload_hides_the_text_and_only_the_device_key_can_read_it():
    private, public = _key_pair()
    body = mobile_push.payload({"key": public}, "Codex「写计算器」在等你批准")
    assert "写计算器" not in json.dumps(body, ensure_ascii=False)
    assert body["aps"]["alert"]["body"] == mobile_push.PLACEHOLDER and body["aps"]["mutable-content"] == 1
    assert apple_ecies_decrypt(private, body["enc"]) == "Codex「写计算器」在等你批准"
    other, _ = _key_pair()
    with pytest.raises(Exception):
        apple_ecies_decrypt(other, body["enc"])
    assert mobile_push.encrypt(public, "x") != mobile_push.encrypt(public, "x")       # 每条都换临时钥匙


def test_provider_token_is_es256_signed_and_cached(apns):
    token = mobile_push._jwt(time.time())
    head, body, signature = token.split(".")
    pad = lambda part: base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))      # noqa: E731
    assert json.loads(pad(head)) == {"alg": "ES256", "kid": "KEY1234567"}
    assert json.loads(pad(body))["iss"] == "TEAM123456"
    raw = pad(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    apns.public_key().verify(der, f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
    assert mobile_push._jwt(time.time() + 60) == token


@pytest.mark.parametrize("status,reason,expected", [
    (200, "", "ok"), (410, "Unregistered", "gone"), (400, "BadDeviceToken", "gone"),
    (400, "PayloadTooLarge", "failed"), (500, "InternalServerError", "failed")])
async def test_send_maps_apns_answers(apns, status, reason, expected):
    _, public = _key_pair()
    seen = []

    def answer(request):
        seen.append(request)
        return httpx.Response(status, json={"reason": reason} if reason else None)

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        result = await mobile_push.send({"token": "ab" * 32, "key": public, "environment": "sandbox"}, "正文", client=client)
    assert result == expected
    request = seen[0]
    assert request.url == "https://api.sandbox.push.apple.com/3/device/" + "ab" * 32
    assert request.headers["apns-topic"] == "com.vorto.vortocode.mobile" and request.headers["apns-push-type"] == "alert"
    assert "正文" not in request.content.decode()


async def test_network_errors_are_a_plain_failure(apns):
    _, public = _key_pair()

    def broken(request):
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as client:
        assert await mobile_push.send({"token": "ab" * 32, "key": public, "environment": "production"}, "x",
                                      client=client) == "failed"


def _device(root):
    store = MobileDevices(root)
    return store, store.issue("手机")["device"]["id"]


def test_push_targets_skip_revoked_and_stale_registrations(tmp_path):
    store, device_id = _device(str(tmp_path))
    _, public = _key_pair()
    registration = mobile_push.normalize_registration("CD" * 32, public, "production")
    store.register_push(device_id, registration)
    assert store.push_targets() == [(device_id, {**registration, "refreshed_at": store.push_targets()[0][1]["refreshed_at"]})]
    later = MobileDevices(str(tmp_path), clock=lambda: time.time() + 31 * 86400)
    assert later.push_targets() == []
    store.forget_push(device_id, "ee" * 32)                     # 别的令牌：不动
    assert store.push_targets()
    store.revoke(device_id)
    assert store.push_targets() == []


@pytest.mark.parametrize("token,env", [("not-hex", "production"), ("ab" * 32, "staging")])
def test_bad_registrations_are_rejected(token, env):
    _, public = _key_pair()
    with pytest.raises(ValueError):
        mobile_push.normalize_registration(token, public, env)


async def test_push_owner_clears_dead_tokens_and_does_nothing_without_config(tmp_path, apns, monkeypatch):
    root = str(tmp_path)
    store, device_id = _device(root)
    _, public = _key_pair()
    store.register_push(device_id, mobile_push.normalize_registration("ab" * 32, public, "production"))

    async def gone(registration, text, client=None):
        return "gone"

    monkeypatch.setattr(mobile_push, "send", gone)
    assert await mobile_push.push_owner(root, "x") is False
    assert store.push_targets() == []
    monkeypatch.delenv("VORTOCODE_APNS_TOPIC")
    assert await mobile_push.push_owner(root, "x") is False


async def test_notifier_dual_sends_and_an_app_failure_never_breaks_im(tmp_path, monkeypatch):
    from src.gateway import im_runtime
    from src.scheduling import notices

    sent, pushed = [], []

    async def im(text, **kwargs):
        sent.append(text)
        return True

    async def app(root, text):
        pushed.append(text)
        raise RuntimeError("APNs down")

    monkeypatch.setattr(im_runtime, "notify_owner", im)
    monkeypatch.setattr(mobile_push, "push_owner", app)
    monkeypatch.setattr(notices, "in_quiet_hours", lambda now=None: False)
    assert await notices.make_notifier(str(tmp_path), source="test")("Codex 出错了") is True
    assert sent and pushed == ["Codex 出错了"]


def test_same_phone_logging_in_again_is_pushed_once(tmp_path):
    """真机 2026-11-03：同一台手机重新登录三次，留下三条都挂着同一个令牌的设备记录，每条通知推三遍。"""
    root = str(tmp_path)
    store = MobileDevices(root)
    first = store.issue("手机")["device"]["id"]
    second = store.issue("手机")["device"]["id"]
    _, old_key = _key_pair()
    _, new_key = _key_pair()
    store.register_push(first, mobile_push.normalize_registration("ab" * 32, old_key, "production"))
    store.register_push(second, mobile_push.normalize_registration("ab" * 32, new_key, "production"))
    [(device_id, push)] = store.push_targets()
    assert device_id == second and push["key"] == new_key       # 手机现在只持有最新那台的解密钥匙
    store.register_push(first, mobile_push.normalize_registration("cd" * 32, old_key, "production"))
    assert sorted(d for d, _ in store.push_targets()) == sorted([first, second])  # 不同令牌各推各的


def test_duplicate_registrations_left_by_older_versions_are_pushed_once(tmp_path):
    root = str(tmp_path)
    store = MobileDevices(root)
    ids = [store.issue("手机")["device"]["id"] for _ in range(3)]
    _, key = _key_pair()
    registration = mobile_push.normalize_registration("ab" * 32, key, "production")
    data = store._load()
    for offset, item in enumerate(item for item in data["devices"] if item["id"] in ids):
        item["push"] = {**registration, "refreshed_at": int(time.time()) - 100 + offset}
    store._save(data)
    assert [d for d, _ in store.push_targets()] == [ids[-1]]


async def test_push_owner_sends_each_token_once(tmp_path, apns, monkeypatch):
    root = str(tmp_path)
    store = MobileDevices(root)
    _, key = _key_pair()
    for _ in range(3):
        device_id = store.issue("手机")["device"]["id"]
        store.register_push(device_id, mobile_push.normalize_registration("ab" * 32, key, "production"))
    sent = []

    async def ok(registration, text, client=None):
        sent.append(registration["token"])
        return "ok"

    monkeypatch.setattr(mobile_push, "send", ok)
    assert await mobile_push.push_owner(root, "x") is True
    assert sent == ["ab" * 32]
