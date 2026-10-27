import json
import secrets
import uuid

import pytest
from fastapi.testclient import TestClient

from src.gateway.account_access import AccountIdentity
from src.gateway.account_registration import AccountOwnerMismatch, AccountRegistration
from src.gateway.account_service import AccountRejected, AccountSession, AccountUnavailable
from src.gateway.mobile_devices import MobileDevices, MobileStoreError


def session(user="12", device=1):
    return AccountSession(AccountIdentity("https://account.test", user), device)


def credential():
    return "vcm_" + secrets.token_urlsafe(32)


def test_owner_is_persistent_and_registration_requires_matching_revision(tmp_path):
    store = AccountRegistration(str(tmp_path), clock=lambda: 100)
    assert store.read()["revision"] == 0 and not list(tmp_path.iterdir())
    first = store.register(session(), "常驻助手", 0)
    assert AccountRegistration(str(tmp_path)).read() == first
    assert store.register(session(), "常驻助手", 1) == first
    with pytest.raises(ValueError):
        store.register(session(), "新名字", 0)
    with pytest.raises(AccountOwnerMismatch):
        store.register(session("99"), "常驻助手", 1)
    assert store.read() == first


def test_optional_instance_guard_prevents_stale_registration_and_unbinding(tmp_path):
    store = AccountRegistration(str(tmp_path))
    first = store.register(session(), "助手", 0, None)
    before = store.path.read_bytes()
    for method, args in [
        (store.register, (session(), "助手", 1, "a" * 32)),
        (store.unregister, (1, "a" * 32)),
    ]:
        with pytest.raises(ValueError):
            method(*args)
        assert store.path.read_bytes() == before
    assert store.unregister(1, first["instance_id"])["registration"] is None


def test_same_account_another_device_gets_short_read_lease_without_stored_tokens(tmp_path):
    store = AccountRegistration(str(tmp_path), clock=lambda: 100)
    registered = store.register(session(), "常驻助手", 0)
    token, request = credential(), uuid.uuid4().hex
    grant = store.read_lease(session(device=2), "手机", 1, request, token)
    assert grant["instance_id"] == registered["instance_id"]
    assert grant["device"]["expires_at"] == 400
    assert grant["device"]["scopes"] == ["assistant:read"]
    assert MobileDevices(str(tmp_path), clock=lambda: 101).authenticate(token)
    assert token not in store.path.read_text()
    assert "access_token" not in store.path.read_text()
    with pytest.raises(AccountOwnerMismatch):
        store.read_lease(session("99"), "手机", 1, uuid.uuid4().hex, credential())


def test_lease_retries_renewal_and_day_long_use_keep_one_device_record(tmp_path):
    now = [100]
    store = AccountRegistration(str(tmp_path), clock=lambda: now[0])
    store.register(session(), "助手", 0)
    token, rid = credential(), uuid.uuid4().hex
    first = store.read_lease(session(device=2), "手机", 1, rid, token)
    assert store.read_lease(session(device=2), "手机", 1, rid, token) == first
    now[0] = 400
    assert store.authenticate(token) is None
    previous = first["device"]["expires_at"]
    for _ in range(300):
        grant = store.read_lease(session(device=2), "手机", 1, rid, token, previous)
        assert store.read_lease(session(device=2), "手机", 1, rid, token, previous) == grant
        previous = grant["device"]["expires_at"]
        now[0] = previous
    assert len(store.list()["devices"]) == 1
    assert now[0] > 86400


def test_revoke_owner_change_and_wrong_receipts_never_resurrect_lease(tmp_path):
    store = AccountRegistration(str(tmp_path), clock=lambda: 100)
    store.register(session(), "助手", 0)
    token, rid = credential(), uuid.uuid4().hex
    grant = store.read_lease(session(device=2), "手机", 1, rid, token)
    with pytest.raises(ValueError):
        store.read_lease(session(device=2), "手机", 1, rid, token, 13)
    with pytest.raises(AccountOwnerMismatch):
        store.read_lease(session(device=3), "手机", 1, rid, token)
    store.revoke(rid)
    with pytest.raises(AccountOwnerMismatch):
        store.read_lease(session(device=2), "手机", 1, rid, token, grant["device"]["expires_at"])
    assert store.authenticate(token) is None
    advanced = store.issue("显式高级连接")
    store.unregister(1)
    assert store.read()["revision"] == 2
    assert store.authenticate(advanced["token"])  # Separate admin-granted policy is preserved.
    store.register(session("99"), "另一个账号的助手", 2)
    with pytest.raises(ValueError):
        store.read_lease(session(), "手机", 1, rid, token)


def test_tampered_owner_binding_and_corruption_fail_closed(tmp_path):
    store = AccountRegistration(str(tmp_path), clock=lambda: 100)
    store.register(session(), "助手", 0)
    token = credential()
    store.read_lease(session(device=2), "手机", 1, uuid.uuid4().hex, token)
    raw = json.loads(store.path.read_text())
    raw["account_registration"]["owner"]["subject"] = "99"
    store.path.write_text(json.dumps(raw))
    assert MobileDevices(str(tmp_path), clock=lambda: 100).authenticate(token) is None
    raw["account_revision"] = "bad"
    store.path.write_text(json.dumps(raw))
    before = store.path.read_bytes()
    with pytest.raises(MobileStoreError):
        store.register(session(), "助手", 1)
    assert store.path.read_bytes() == before


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "root-account-bridge-test")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    from src.web.server import app
    from src.web.routers import account_access

    calls = []

    async def verify(token, issuer):
        assert issuer == "https://account.test"
        calls.append(token)
        if token == "u" * 43:
            raise AccountUnavailable()
        if token == "r" * 43:
            raise AccountRejected()
        return session("99" if token == "b" * 43 else "12", device=2)

    monkeypatch.setattr(account_access, "verify_account_access", verify)
    client = TestClient(app, base_url="https://gateway.test")
    yield client, tmp_path, calls
    client.close()


ROOT = {"Authorization": "Bearer root-account-bridge-test"}


def test_desktop_setup_metadata_and_atomic_instance_check(api, monkeypatch):
    client, root, calls = api
    monkeypatch.setenv("VORTOCODE_ACCOUNT_URL", "https://account.test")
    monkeypatch.setenv("VORTOCODE_MOBILE_PUBLIC_ORIGIN", "https://phone.test")
    path = "/api/mobile/account/registration"
    assert client.get(path).status_code == 401
    current = client.get(path, headers=ROOT).json()
    assert current["account_issuer"] == "https://account.test"
    assert current["mobile_entry_url"] == "https://phone.test/mobile"
    body = {
        "access_token": "a" * 43,
        "issuer": "https://account.test",
        "name": "助手",
        "revision": 0,
        "instance_id": None,
    }
    first = client.put(path, json=body, headers=ROOT).json()
    assert first["revision"] == 1
    assert (
        client.request(
            "DELETE", path, headers=ROOT, json={"revision": 1, "instance_id": "a" * 32}
        ).status_code
        == 409
    )
    assert client.get(path, headers=ROOT).json()["registration"]
    assert (
        client.request(
            "DELETE", path, headers=ROOT, json={"revision": 1, "instance_id": first["instance_id"]}
        ).status_code
        == 200
    )


def test_internal_bridge_requires_admin_and_live_account_identity(api):
    client, root, calls = api
    registration = {
        "access_token": "a" * 43,
        "issuer": "https://account.test",
        "name": "助手",
        "revision": 0,
    }
    path = "/api/mobile/account/registration"
    assert client.put(path, json=registration).status_code == 401
    assert (
        client.put(
            path, headers={"Authorization": "Bearer " + "a" * 43}, json=registration
        ).status_code
        == 401
    )
    assert not calls
    assert client.put(path, headers=ROOT, json={**registration, "user_id": 12}).status_code == 422
    registered = client.put(path, headers=ROOT, json=registration)
    assert registered.status_code == 200
    assert registered.headers["cache-control"] == "no-store"
    assert registered.json()["registration"]["owner"]["subject"] == "12"
    proof = credential()
    request = {
        "access_token": "a" * 43,
        "issuer": "https://account.test",
        "registration_revision": 1,
        "request_id": uuid.uuid4().hex,
        "device_token": proof,
        "name": "手机",
    }
    assert client.post("/api/mobile/account/connect", json=request).status_code == 401
    response = client.post("/api/mobile/account/connect", headers=ROOT, json=request)
    assert response.status_code == 200
    assert "access_token" not in response.text
    assert response.json()["device"]["scopes"] == ["assistant:read"]
    device = {"Authorization": "Bearer " + proof}
    assert client.get("/api/mobile/v1/snapshot", headers=device).status_code == 200
    assert client.get("/api/assistant/profile", headers=device).status_code == 401
    assert (
        client.post(
            "/api/mobile/account/connect", headers=ROOT, json={**request, "access_token": "b" * 43}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/mobile/account/connect", headers=ROOT, json={**request, "access_token": "u" * 43}
        ).status_code
        == 503
    )
    assert (
        client.post(
            "/api/mobile/account/connect", headers=ROOT, json={**request, "access_token": "r" * 43}
        ).status_code
        == 401
    )
    saved = (root / ".vortocode/mobile-devices/grants.json").read_text()
    assert "a" * 43 not in saved and proof not in saved
    assert client.request("DELETE", path, headers=ROOT, json={"revision": 1}).status_code == 200
    assert client.get("/api/mobile/v1/bootstrap", headers=device).status_code == 401
