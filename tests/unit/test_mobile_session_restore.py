"""手机网关重启后 App 不用重新登录（ADR-012，v4 审查 V6）：原生会话加密落盘，按需恢复。"""

import pytest

from src.gateway.mobile_browser import MobileBrowserSessions
from src.gateway.mobile_session_store import NativeSessionStore
from tests.unit.test_mobile_browser import native_handoff, setup  # noqa: F401 —— 复用原生会话夹具

pytestmark = pytest.mark.skipif(not NativeSessionStore.available(), reason="需要 cryptography（mobile 依赖）")


def _login(client):
    token = client.post("/api/mobile/native/exchange", json=native_handoff(client)).json()["token"]
    return {"Authorization": "Bearer " + token}


def _restart(monkeypatch, now):
    from src.web.server import app
    fresh = MobileBrowserSessions(clock=lambda: now[0])
    monkeypatch.setattr(app.state, "mobile_browser", fresh, raising=False)
    return fresh


def test_native_session_survives_a_gateway_restart(setup, monkeypatch):  # noqa: F811
    client, _sessions, now, _calls, root, _ = setup
    headers = _login(client)
    device = client.get("/api/mobile/native/bootstrap", headers=headers).json()["device"]["id"]
    _restart(monkeypatch, now)
    after = client.get("/api/mobile/native/bootstrap", headers=headers)
    assert after.status_code == 200 and after.json()["device"]["id"] == device


def test_record_is_encrypted_and_holds_no_session_credential(setup, monkeypatch):  # noqa: F811
    client, _sessions, _now, _calls, root, _ = setup
    headers = _login(client)
    client.get("/api/mobile/native/bootstrap", headers=headers)
    store = NativeSessionStore(str(root))
    blob = store.path.read_bytes()
    token = headers["Authorization"][len("Bearer "):]
    for secret in (token, token[4:], "a" * 43, "b" * 43, "c" * 43):     # 会话凭据、账号令牌都不以明文出现
        assert secret.encode() not in blob
    assert oct(store.key_path.stat().st_mode & 0o777) == "0o600"


def test_logout_removes_the_record_so_restart_cannot_revive_it(setup, monkeypatch):  # noqa: F811
    client, _sessions, now, _calls, root, _ = setup
    headers = _login(client)
    client.get("/api/mobile/native/bootstrap", headers=headers)
    assert client.post("/api/mobile/native/logout", headers=headers).status_code == 200
    _restart(monkeypatch, now)
    assert client.get("/api/mobile/native/bootstrap", headers=headers).status_code == 401


def test_changed_ownership_or_expiry_is_not_restored(setup, monkeypatch):  # noqa: F811
    client, _sessions, now, _calls, root, _ = setup
    headers = _login(client)
    client.get("/api/mobile/native/bootstrap", headers=headers)
    monkeypatch.setenv("VORTOCODE_MOBILE_PUBLIC_ORIGIN", "https://other.test")   # 入口换了
    _restart(monkeypatch, now)
    assert client.get("/api/mobile/native/bootstrap", headers=headers).status_code in (401, 503)
    assert NativeSessionStore(str(root))._read() == {}                        # 不认就删


def test_unreadable_record_means_log_in_again(setup, monkeypatch):  # noqa: F811
    client, _sessions, now, _calls, root, _ = setup
    headers = _login(client)
    client.get("/api/mobile/native/bootstrap", headers=headers)
    NativeSessionStore(str(root)).path.write_bytes(b"garbage" * 10)
    _restart(monkeypatch, now)
    assert client.get("/api/mobile/native/bootstrap", headers=headers).status_code == 401
