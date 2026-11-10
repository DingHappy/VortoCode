"""The independently deployed mobile gateway must never start the execution host."""

import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from src.web.mobile_server import create_app


@pytest.fixture
def mobile_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOONE_API_TOKEN", "private-mobile-administrator-token")
    monkeypatch.setenv("VORTOONE_MOBILE_PUBLIC_ORIGIN", "https://assistant.example.com:18443")
    monkeypatch.setenv("VORTOONE_MOBILE_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("VORTOONE_MOBILE_RELEASE", "mobile-test.123")
    return tmp_path


def test_gateway_refuses_missing_admin(mobile_env, monkeypatch):
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        create_app()


def test_gateway_refuses_wrong_state_root(mobile_env, monkeypatch):
    monkeypatch.setenv("VORTOONE_MOBILE_STATE_ROOT", str(mobile_env / "other"))
    with pytest.raises(RuntimeError):
        create_app()


def test_gateway_has_no_execution_or_general_api_routes(mobile_env):
    app = create_app()
    with TestClient(app) as client:
        health = client.get("/api/health/quick").json()
        assert health["release"] == "mobile-test.123" and health["execution_runtime"] is False
        assert client.get("/api/mobile/account/registration").status_code == 401
        admin = {"Authorization": "Bearer private-mobile-administrator-token"}
        for method, path in (("GET", "/api/config"), ("POST", "/api/tasks"),
                             ("GET", "/api/runs"), ("POST", "/api/cron"), ("GET", "/docs")):
            assert client.request(method, path, headers=admin).status_code == 404


def test_gateway_import_does_not_load_agent_lifespan(mobile_env):
    root = str(Path(__file__).resolve().parents[2])
    code = """import sys
from src.web.mobile_server import create_app
create_app()
assert 'src.web.server' not in sys.modules
assert 'src.web.runtime' not in sys.modules
assert 'src.gateway.runtime' not in sys.modules
assert 'src.llm.client' not in sys.modules
"""
    env = {**os.environ, "PYTHONPATH": root, "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run([sys.executable, "-c", code], env=env, check=True, capture_output=True)


@pytest.fixture
def limited_gateway(mobile_env, monkeypatch):
    from src.web import mobile_server

    now = [1000.0]
    monkeypatch.setattr(mobile_server, "monotonic", lambda: now[0], raising=False)
    return create_app(), now


def test_gateway_limits_requests_per_source(limited_gateway):
    app, _ = limited_gateway
    with TestClient(app) as client:
        for _ in range(120):
            assert client.get("/api/health/quick").status_code == 200
        response = client.get("/api/health/quick")
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "60"


def test_gateway_backs_off_after_authentication_failures(limited_gateway):
    app, _ = limited_gateway
    with TestClient(app) as client:
        for _ in range(5):
            assert client.get("/api/mobile/account/registration").status_code == 401
        response = client.get("/api/health/quick")
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "30"


def test_gateway_rate_window_recovers_and_ignores_untrusted_forwarding_headers(limited_gateway):
    app, now = limited_gateway
    with TestClient(app, client=("source-a", 1234)) as client:
        for _ in range(120):
            assert client.get("/api/health/quick").status_code == 200
        now[0] += 59
        response = client.get("/api/health/quick", headers={"X-Forwarded-For": "other-source",
                                                           "Forwarded": "for=other-source"})
        assert response.status_code == 429 and response.headers["Retry-After"] == "1"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Pragma"] == "no-cache"
        with TestClient(app, client=("source-b", 5678)) as other:
            assert other.get("/api/health/quick").status_code == 200
        now[0] += 1
        assert client.get("/api/health/quick").status_code == 200


def test_gateway_authentication_backoff_grows_caps_and_recovers(limited_gateway):
    app, now = limited_gateway
    with TestClient(app, client=("source-a", 1234)) as client:
        for seconds in (30, 60, 120, 240, 480, 900, 900):
            for _ in range(5):
                assert client.get("/api/mobile/account/registration").status_code == 401
            response = client.get("/api/health/quick")
            assert response.status_code == 429
            assert response.headers["Retry-After"] == str(seconds)
            with TestClient(app, client=("source-b", 5678)) as other:
                assert other.get("/api/health/quick").status_code == 200
            now[0] += seconds - 1
            assert client.get("/api/health/quick").status_code == 429
            now[0] += 1
            assert client.get("/api/health/quick").status_code == 200


def test_gateway_failure_window_expires_and_success_does_not_erase_recent_failures(limited_gateway):
    app, now = limited_gateway
    with TestClient(app) as client:
        for _ in range(4):
            assert client.get("/api/mobile/account/registration").status_code == 401
        now[0] += 60
        for _ in range(4):
            assert client.get("/api/mobile/account/registration").status_code == 401
            assert client.get("/api/health/quick").status_code == 200
        assert client.get("/api/mobile/account/registration").status_code == 401
        assert client.get("/api/health/quick").status_code == 429


def test_gateway_counts_authentication_failures_inside_self_authenticated_routes(limited_gateway):
    app, _ = limited_gateway
    with TestClient(app, base_url="https://assistant.example.com") as client:
        for _ in range(5):
            assert client.get("/api/mobile/native/snapshot").status_code == 401
        assert client.get("/api/health/quick").status_code == 429


def test_gateway_apps_have_independent_limits(limited_gateway):
    app, _ = limited_gateway
    with TestClient(app) as client:
        for _ in range(120):
            assert client.get("/api/health/quick").status_code == 200
        assert client.get("/api/health/quick").status_code == 429
    with TestClient(create_app()) as other:
        assert other.get("/api/health/quick").status_code == 200


def test_normal_polling_and_authenticated_refreshes_remain_available(limited_gateway):
    app, now = limited_gateway
    admin = {"Authorization": "Bearer private-mobile-administrator-token"}
    with TestClient(app, base_url="https://assistant.example.com") as client:
        # 覆盖连续读回合、定期刷新与小组查询的混合节奏，跨越多个窗口。
        for second in range(180):
            assert client.get("/api/health/quick").status_code == 200
            if second % 5 == 0:
                assert client.get("/api/health/quick").status_code == 200
            if second % 30 == 0:
                for _ in range(6):
                    assert client.get("/api/mobile/account/registration", headers=admin).status_code == 200
            now[0] += 1
