"""Mobile read grants must never become administrator or write authority."""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from src.gateway.mobile_devices import MobileDevices, MobileStoreError


def test_grant_survives_restart_without_persisting_plaintext(tmp_path):
    store = MobileDevices(str(tmp_path), clock=lambda: 100)
    grant = store.issue("我的手机")
    saved = store.path.read_text()
    assert grant["token"] not in saved
    assert "digest" in saved
    assert MobileDevices(str(tmp_path), clock=lambda: 101).authenticate(grant["token"]) == {
        "instance_id": grant["instance_id"],
        "device": grant["device"],
    }
    assert "digest" not in json.dumps(store.list())
    assert "token" not in json.dumps(store.list())
    if os.name != "nt":
        assert store.path.stat().st_mode & 0o777 == 0o600
        assert store.directory.stat().st_mode & 0o777 == 0o700


def test_expiry_and_revocation_are_independent_per_device(tmp_path):
    now = [100]
    store = MobileDevices(str(tmp_path), clock=lambda: now[0])
    first, second = store.issue("A", 1), store.issue("B", 2)
    store.revoke(first["device"]["id"])
    assert store.authenticate(first["token"]) is None
    assert store.authenticate(second["token"])
    assert store.revoke(first["device"]["id"])["revoked_at"] == 100
    now[0] += 2 * 86400
    assert store.authenticate(second["token"]) is None


def test_reads_do_not_create_state_and_corruption_never_reinitializes(tmp_path):
    store = MobileDevices(str(tmp_path))
    assert store.list() == {"instance_id": None, "devices": []}
    assert store.authenticate("vcm_" + "x" * 43) is None
    assert list(tmp_path.iterdir()) == []
    grant = store.issue("A")
    store.path.write_text("bad")
    for operation in (
        lambda: store.authenticate(grant["token"]),
        store.list,
        lambda: store.issue("B"),
    ):
        with pytest.raises(MobileStoreError):
            operation()
    assert store.path.read_text() == "bad"


def test_device_store_refuses_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "mobile-devices").symlink_to(outside, target_is_directory=True)
    with pytest.raises(MobileStoreError):
        MobileDevices(str(tmp_path)).issue("A")
    assert not list(outside.iterdir())


@pytest.mark.parametrize("token", ["", "秘密", "Bearer x", "vcm_" + "x" * 42, "vcm_" + "x" * 44])
def test_invalid_token_rejected_without_reading_storage(tmp_path, token):
    assert MobileDevices(str(tmp_path)).authenticate(token) is None
    assert not list(tmp_path.iterdir())


def test_concurrent_issuance_never_loses_existing_grants(tmp_path):
    store = MobileDevices(str(tmp_path))
    store.issue("initial")

    def issue(number):
        try:
            return MobileDevices(str(tmp_path)).issue(f"phone-{number}")
        except MobileStoreError:
            return None  # A busy lock is explicit; no write is reported as accepted.

    with ThreadPoolExecutor(max_workers=6) as executor:
        accepted = [grant for grant in executor.map(issue, range(18)) if grant]
    assert accepted
    assert len(store.list()["devices"]) == len(accepted) + 1
    assert all(store.authenticate(grant["token"]) for grant in accepted)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "admin-test-only")
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    from src.web.server import app

    client = TestClient(app, base_url="https://gateway.test")
    try:
        yield client, tmp_path
    finally:
        client.close()


ADMIN = {"Authorization": "Bearer admin-test-only"}


def _grant(client):
    response = client.post("/api/mobile/devices", headers=ADMIN, json={"name": "手机"})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_device_routes_require_device_grant_and_admin_routes_do_not_accept_it(api):
    client, root = api
    grant = _grant(client)
    device = {"Authorization": f"Bearer {grant['token']}"}
    assert client.get("/api/mobile/devices", headers=device).status_code == 401
    assert (
        client.post("/api/mobile/devices", headers=device, json={"name": "escalate"}).status_code
        == 401
    )
    assert client.get("/api/assistant/profile", headers=device).status_code == 401
    assert (
        client.put("/api/assistant/profile", headers=device, json={"name": "changed"}).status_code
        == 401
    )
    assert client.get("/api/mobile/v1/bootstrap", headers=ADMIN).status_code == 401
    assert (
        client.get("/api/mobile/v1/bootstrap", params={"token": grant["token"]}).status_code == 401
    )
    assert (
        client.get("/api/mobile/v1/bootstrap", headers={"X-API-Token": grant["token"]}).status_code
        == 401
    )
    assert (
        client.get(
            "/api/mobile/v1/bootstrap", cookies={"vortocode_session": grant["token"]}
        ).status_code
        == 401
    )
    response = client.get("/api/mobile/v1/bootstrap", headers=device)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["capabilities"] == {
        "reads": ["profile", "main_chat", "goals"],
        "writes": [],
    }
    assert response.json()["instance_id"] == grant["instance_id"]
    assert client.post("/api/mobile/v1/snapshot", headers=device, json={}).status_code == 401
    assert (
        client.get("/api/mobile/v1/unknown", headers=device).status_code == 409
    )  # General workspace fence
    assert not (root / ".vortocode" / "assistant.json").exists()


def test_revocation_applies_to_next_request(api):
    client, _ = api
    first, second = _grant(client), _grant(client)
    assert (
        client.delete(f"/api/mobile/devices/{first['device']['id']}", headers=ADMIN).status_code
        == 200
    )
    for grant, expected in ((first, 401), (second, 200)):
        assert (
            client.get(
                "/api/mobile/v1/snapshot", headers={"Authorization": f"Bearer {grant['token']}"}
            ).status_code
            == expected
        )
    listing = client.get("/api/mobile/devices", headers=ADMIN)
    assert first["token"] not in listing.text and "digest" not in listing.text


def test_no_admin_configuration_disables_mobile_even_when_grants_exist(api, monkeypatch):
    client, _ = api
    grant = _grant(client)
    monkeypatch.delenv("VORTOCODE_API_TOKEN")
    assert client.post("/api/mobile/devices", json={"name": "A"}).status_code == 403
    assert (
        client.get(
            "/api/mobile/v1/bootstrap", headers={"Authorization": f"Bearer {grant['token']}"}
        ).status_code
        == 401
    )


def test_cross_origin_cookie_cannot_issue_grant(api):
    client, _ = api
    response = client.post(
        "/api/mobile/devices", headers={**ADMIN, "Origin": "https://other.test"}, json={"name": "A"}
    )
    assert response.status_code == 403
    assert (
        client.post(
            "/api/mobile/devices", headers=ADMIN, json={"name": "A", "scope": "admin"}
        ).status_code
        == 422
    )


def test_remote_plain_http_is_not_a_device_provisioning_transport(api):
    client, _ = api
    response = client.post(
        "http://gateway.test/api/mobile/devices", headers=ADMIN, json={"name": "A"}
    )
    assert response.status_code == 403
    # A loopback Host header alone does not turn a remote peer into local transport.
    response = client.post("http://127.0.0.1/api/mobile/devices", headers=ADMIN, json={"name": "A"})
    assert response.status_code == 403


def test_snapshot_projects_existing_domains_without_mutating_or_exporting_private_profile(api):
    client, root = api
    from src.memory.assistant_profile import save_profile
    from src.gateway.main_conversation import claim_main_chat
    from src.workflow.workbench.personal_goals import PersonalGoals

    save_profile(str(root), name="助手", soul="private-soul", about_you="private-persona")
    claim_main_chat(str(root), "sid-main")
    goal = PersonalGoals(str(root)).create(
        {
            "request_id": "mobile-test",
            "objective": "继续推进",
            "next_action": "核对依据",
            "acceptance_criteria": ["用户确认"],
        }
    )
    grant = _grant(client)
    before = {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    response = client.get(
        "/api/mobile/v1/snapshot", headers={"Authorization": f"Bearer {grant['token']}"}
    )
    assert response.status_code == 200
    assert "private-" not in response.text
    sources = response.json()["sources"]
    assert sources["profile"]["data"] == {"name": "助手"}
    assert sources["main_chat"]["data"] == {"sid": "main"}
    assert sources["goals"]["data"]["items"][0]["id"] == goal["id"]
    assert all(item["status"] == "current" and item["checked_at"] for item in sources.values())
    after = {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    assert after == before


def test_source_failure_is_not_a_successful_empty_list(api, monkeypatch):
    client, root = api
    from src.web.routers import mobile

    def broken(_root):
        raise OSError("private/path/secret")

    monkeypatch.setattr(mobile, "_goals", broken)
    grant = _grant(client)
    (root / ".vortocode" / "main_chat.json").write_text("broken")
    response = client.get(
        "/api/mobile/v1/snapshot", headers={"Authorization": f"Bearer {grant['token']}"}
    )
    assert "private/path" not in response.text
    sources = response.json()["sources"]
    assert sources["profile"]["status"] == "current"
    assert (
        sources["main_chat"]
        == sources["goals"]
        == {"status": "unavailable", "checked_at": None, "data": None}
    )
