import json
import secrets
import uuid

import pytest
from fastapi.testclient import TestClient

from src.gateway.mobile_devices import MobileDevices, MobileStoreError
from src.gateway.mobile_pairing import MobilePairing, PairingDenied


def proof():
    return "vcm_" + secrets.token_urlsafe(32)


def test_window_and_two_sided_confirmation_do_not_implicitly_grant_access(tmp_path):
    service = MobilePairing(str(tmp_path), clock=lambda: 100)
    rid, token = uuid.uuid4().hex, proof()
    with pytest.raises(ValueError):
        service.start(rid, "手机", token)
    service.window(True)
    pending = service.start(rid, "手机", token)
    assert service.authenticate(token) is None
    assert service.start(rid, "手机", token) == pending
    with pytest.raises(PairingDenied):
        service.start(rid, "手机", proof())
    with pytest.raises(PairingDenied):
        service.status(rid, proof())
    with pytest.raises(ValueError):
        service.decide(rid, "WRONG123", True)
    approved = service.decide(rid, pending["code"], True)
    assert approved["status"] == "approved"
    assert service.decide(rid, pending["code"], True, 90) == approved
    assert len(service.list()["devices"]) == 1
    assert service.status(rid, token) == approved
    assert MobileDevices(str(tmp_path), clock=lambda: 101).authenticate(token)
    assert token not in service.path.read_text()
    assert "digest" not in json.dumps(service.overview())


@pytest.mark.parametrize("change", ["close", "reopen", "expire", "reject", "cancel"])
def test_stale_or_cancelled_pairing_cannot_be_approved(tmp_path, change):
    now = [100]
    service = MobilePairing(str(tmp_path), clock=lambda: now[0])
    service.window(True)
    rid, token = uuid.uuid4().hex, proof()
    pending = service.start(rid, "手机", token)
    if change == "close":
        service.window(False)
    elif change == "reopen":
        service.window(True)
    elif change == "expire":
        now[0] = 400
    elif change == "reject":
        service.decide(rid, pending["code"], False)
    else:
        service.cancel(rid, token)
    with pytest.raises(ValueError):
        service.decide(rid, pending["code"], True)
    assert not service.list()["devices"]
    assert service.authenticate(token) is None


def test_cancellation_after_lost_approval_response_revokes_own_grant(tmp_path):
    service = MobilePairing(str(tmp_path), clock=lambda: 100)
    service.window(True)
    rid, token = uuid.uuid4().hex, proof()
    pending = service.start(rid, "手机", token)
    service.decide(rid, pending["code"], True)
    assert service.cancel(rid, token)["status"] == "revoked"
    assert service.authenticate(token) is None
    with pytest.raises(ValueError):
        service.decide(rid, pending["code"], True)
    assert len(service.list()["devices"]) == 1


def test_bounded_window_and_duplicate_credentials(tmp_path):
    service = MobilePairing(str(tmp_path), clock=lambda: 100)
    service.window(True)
    token = proof()
    first = service.start(uuid.uuid4().hex, "手机", token)
    with pytest.raises(ValueError):
        service.start(uuid.uuid4().hex, "重复凭证", token)
    for number in range(7):
        service.start(uuid.uuid4().hex, f"phone-{number}", proof())
    with pytest.raises(ValueError):
        service.start(uuid.uuid4().hex, "too-many", proof())
    assert service.status(first["id"], token)["status"] == "pending"


def test_corrupt_pairing_metadata_is_not_reset(tmp_path):
    service = MobilePairing(str(tmp_path), clock=lambda: 100)
    service.window(True)
    raw = json.loads(service.path.read_text())
    raw["pairings"] = [{"id": 12}]
    service.path.write_text(json.dumps(raw))
    before = service.path.read_bytes()
    with pytest.raises(MobileStoreError):
        service.window(True)
    assert service.path.read_bytes() == before


ADMIN = {"Authorization": "Bearer test-admin-pairing"}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "test-admin-pairing")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    from src.web.server import app

    client = TestClient(app, base_url="https://gateway.test")
    yield client
    client.close()


def test_http_pairing_proof_never_becomes_administrator_or_a_second_device(api):
    rid, token = uuid.uuid4().hex, proof()
    body = {"request_id": rid, "device_token": token, "name": "我的手机"}
    assert api.post("/api/mobile/pairing/start", json=body).status_code == 409
    assert api.post("/api/mobile/pairing-window", json={"enabled": True}).status_code == 401
    assert (
        api.post("/api/mobile/pairing-window", headers=ADMIN, json={"enabled": True}).status_code
        == 200
    )
    start = api.post("/api/mobile/pairing/start", json=body)
    assert start.status_code == 200
    pending = start.json()
    assert token not in start.text
    assert (
        api.get(
            "/api/mobile/v1/bootstrap", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 401
    )
    assert api.get("/api/mobile/pairings").status_code == 401
    assert api.get("/api/mobile/pairing/status").status_code == 401
    assert (
        api.post(
            "/api/mobile/pairing/status", json={"request_id": rid, "device_token": proof()}
        ).status_code
        == 401
    )
    decision = {"code": pending["code"], "approve": True}
    assert api.post(f"/api/mobile/pairings/{rid}/decision", json=decision).status_code == 401
    assert (
        api.post(f"/api/mobile/pairings/{rid}/decision", headers=ADMIN, json=decision).status_code
        == 200
    )
    assert (
        api.post(f"/api/mobile/pairings/{rid}/decision", headers=ADMIN, json=decision).status_code
        == 200
    )
    assert len(api.get("/api/mobile/devices", headers=ADMIN).json()["devices"]) == 1
    poll = api.post("/api/mobile/pairing/status", json={"request_id": rid, "device_token": token})
    assert poll.json()["status"] == "approved"
    device = {"Authorization": f"Bearer {token}"}
    assert api.get("/api/mobile/v1/snapshot", headers=device).status_code == 200
    assert api.get("/api/assistant/profile", headers=device).status_code == 401
    assert (
        api.post(
            "/api/mobile/pairing/cancel", json={"request_id": rid, "device_token": token}
        ).status_code
        == 200
    )
    assert api.get("/api/mobile/v1/bootstrap", headers=device).status_code == 401


def test_cross_site_pairing_and_missing_admin_configuration_are_rejected(api, monkeypatch):
    body = {"request_id": uuid.uuid4().hex, "device_token": proof(), "name": "手机"}
    api.post("/api/mobile/pairing-window", headers=ADMIN, json={"enabled": True})
    assert (
        api.post(
            "/api/mobile/pairing/start", headers={"Origin": "https://other.test"}, json=body
        ).status_code
        == 403
    )
    monkeypatch.delenv("VORTOCODE_API_TOKEN")
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    assert api.post("/api/mobile/pairing/start", json=body).status_code == 403


def test_mobile_shell_is_public_but_only_built_assets_can_be_read(api, tmp_path, monkeypatch):
    from src.web.routers import mobile_pages

    directory = tmp_path / "bundle"
    monkeypatch.setattr(mobile_pages, "_DIRECTORY", directory)
    assert api.get("/mobile").status_code == 503
    directory.mkdir()
    (directory / "index.html").write_text("<html>Static shell only</html>")
    response = api.get("/mobile")
    assert response.status_code == 200
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert api.get("/static/mobile/grants.json").status_code == 404
    assert api.get("/static/mobile/assets/..%2Fsecret.js").status_code == 404
