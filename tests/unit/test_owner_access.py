"""Owner access is a bounded read projection, not another approval or permission channel."""
import json

from fastapi.testclient import TestClient

from src.gateway import audit
from src.gateway.owner_access import snapshot
from src.gateway.trust_setting import set_trust_level
from tests.unit.test_mobile_browser import setup, native_handoff  # noqa: F401
from tests.unit.test_mobile_owner import owner, bind  # noqa: F401


def test_projection_clamps_policy_and_omits_private_operation_and_tool_payload(tmp_path):
    set_trust_level(str(tmp_path), "full")
    audit.record_decision_audit(str(tmp_path), session="app-test", mode="build", operation="私人收件人和正文",
        decision=True, tainted=False, approval={"operation_kind": "own_data", "approval_scope": "once",
        "decision_source": "user", "approval_outcome": "approved"})
    audit.record_tool_audit(str(tmp_path), session="s", mode="build", name="shell", args={"command": "private-path"}, result="secret")
    before = (tmp_path / ".vortocode/audit.log").read_bytes()
    value = snapshot(str(tmp_path))
    permissions = value["permissions"]
    assert permissions["stored_level"] == "full" and permissions["effective_level"] == "reads"
    assert permissions["scopes"] == ["once"] and permissions["taint_revokes_preauthorization"]
    categories = {r["operation_kind"]: r for r in permissions["categories"]}
    assert categories["own_data"]["preauthorized"] and not categories["deliver"]["preauthorized"]
    assert not categories["interact"]["preauthorized"] and not categories["irreversible"]["preauthorized"]
    [row] = value["history"]["entries"]
    assert row["origin"] == "app" and row["record_type"] == "decision"
    assert row["approval_outcome"] == "approved"
    assert all(secret not in json.dumps(value, ensure_ascii=False) for secret in ["私人收件人", "private-path", "secret"])
    assert before == (tmp_path / ".vortocode/audit.log").read_bytes()


def test_projection_keeps_card_events_and_legacy_decisions_distinct(tmp_path):
    for op, metadata in [("old", None), ("App 审批卡 card-1", {"approval_outcome": "expired", "decision_source": "timeout"})]:
        audit.record_decision_audit(str(tmp_path), session="app-test", mode="build", operation=op,
                                    decision=False, tainted=True, approval=metadata)
    card, legacy = snapshot(str(tmp_path))["history"]["entries"]
    assert card["record_type"] == "card" and card["approval_outcome"] == "expired"
    assert legacy["record_type"] == "decision" and "operation_kind" not in legacy


def test_unreadable_or_malformed_history_never_becomes_empty_success(tmp_path, monkeypatch):
    assert snapshot(str(tmp_path))["history"]["status"] == "current"
    folder = tmp_path / ".vortocode"
    folder.mkdir()
    path = folder / "audit.log"
    path.write_text("not-json\n")
    assert snapshot(str(tmp_path))["history"]["status"] == "partial"

    def unreadable(_path):
        raise OSError("blocked")

    monkeypatch.setattr(audit, "_tail_lines", unreadable)
    history = snapshot(str(tmp_path))["history"]
    assert history["entries"] == [] and history["status"] == "unavailable" and history["bounded"]
    assert history["sources"]["audit"] == "unavailable"


def test_projection_is_bounded_and_ids_do_not_expose_legacy_values(tmp_path):
    folder = tmp_path / ".vortocode"
    folder.mkdir()
    rows = [{"category": "decision", "id": f"private-path-{n}", "operation": "private", "decision": "denied"} for n in range(600)]
    (folder / "audit.log").write_text("\n".join(json.dumps(r) for r in rows))
    value = snapshot(str(tmp_path))
    assert len(value["history"]["entries"]) == 100 and value["history"]["bounded"]
    assert "private" not in json.dumps(value)


def test_native_access_requires_native_session_enabled_writes_and_trusted_device(owner, monkeypatch):  # noqa: F811
    client, headers, _device, _root = owner
    path = "/api/mobile/native/access"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=headers).status_code == 403
    bind(client, headers)
    response = client.get(path, headers=headers)
    assert response.status_code == 200 and response.json()["permissions"]["profile"] == "owner_device"
    assert response.headers["cache-control"] == "no-store"
    assert client.put(path, headers=headers, json={"effective_level": "full"}).status_code == 401
    monkeypatch.setenv("VORTOCODE_MOBILE_WRITES", "0")
    assert client.get(path, headers=headers).status_code == 403


def test_native_access_uses_resident_projection_when_relay_is_configured(owner, monkeypatch):  # noqa: F811
    client, headers, _device, _root = owner
    bind(client, headers)
    import src.gateway.app_relay_client as relay
    calls = []

    async def read(path, body):
        calls.append((path, body))
        return 200, {"schema_version": 1, "history": {"status": "unavailable", "entries": []}}

    monkeypatch.setenv("VORTOCODE_APP_RELAY_URL", "http://127.0.0.1:8081")
    monkeypatch.setenv("VORTOCODE_APP_RELAY_TOKEN", "relay-test")
    monkeypatch.setattr(relay, "relay", read)
    response = client.get("/api/mobile/native/access", headers=headers)
    assert response.status_code == 200 and response.json()["history"]["status"] == "unavailable"
    assert calls == [("/api/assistant/app-relay/access", {})]


def test_admin_and_loopback_access_do_not_borrow_each_others_tokens(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "admin-token")
    monkeypatch.setenv("VORTOCODE_APP_RELAY_TOKEN", "relay-token")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    from src.web.server import app
    local = TestClient(app, client=("127.0.0.1", 5000))
    remote = TestClient(app)
    path = "/api/assistant/app-relay/access"
    assert local.post(path, headers={"Authorization": "Bearer relay-token"}).status_code == 200
    assert remote.post(path, headers={"Authorization": "Bearer relay-token"}).status_code == 404
    assert local.post(path, headers={"Authorization": "Bearer admin-token"}).status_code == 404
    public = "/api/assistant/access"
    assert local.get(public, headers={"Authorization": "Bearer relay-token"}).status_code == 401
    assert local.get(public, headers={"Authorization": "Bearer admin-token"}).status_code == 200
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    assert local.get(public, headers={"Authorization": "Bearer admin-token"}).status_code == 403


def test_revocation_during_resident_read_discards_the_result(owner, monkeypatch):  # noqa: F811
    client, headers, device, root = owner
    bind(client, headers)
    import src.gateway.app_relay_client as relay
    from src.gateway.mobile_devices import MobileDevices

    async def read(_path, _body):
        MobileDevices(root).revoke(device)
        return 200, {"private_history": "must-not-arrive"}

    monkeypatch.setenv("VORTOCODE_APP_RELAY_URL", "http://127.0.0.1:8081")
    monkeypatch.setenv("VORTOCODE_APP_RELAY_TOKEN", "relay-test")
    monkeypatch.setattr(relay, "relay", read)
    response = client.get("/api/mobile/native/access", headers=headers)
    assert response.status_code == 401 and "must-not-arrive" not in response.text


def test_lost_turn_closes_old_cards_without_claiming_previous_actions_failed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "admin-token")
    monkeypatch.setenv("VORTOCODE_APP_RELAY_TOKEN", "relay-token")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    from src.web.routers import app_relay
    from src.gateway.app_turns import AppTurns
    monkeypatch.setattr(app_relay, "_turns", AppTurns(str(tmp_path), lambda _confirm: None))
    from src.web.server import app
    client = TestClient(app, client=("127.0.0.1", 5000))
    response = client.post(app_relay.STATUS_PATH, json={"rid": "app-" + "a" * 16}, headers={"Authorization": "Bearer relay-token"})
    data = response.json()
    assert response.status_code == 200 and data["state"] == "failed" and data["outcome"] == "unknown"
    assert data["cards"] == [] and "核对之前的动作" in data["error"]
    assert not app_relay.turns().turns  # No recovery, retry or execution is started by this read.
