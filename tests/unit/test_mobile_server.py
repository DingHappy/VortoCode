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
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "private-mobile-administrator-token")
    monkeypatch.setenv("VORTOCODE_MOBILE_PUBLIC_ORIGIN", "https://assistant.example.com:18443")
    monkeypatch.setenv("VORTOCODE_MOBILE_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("VORTOCODE_MOBILE_RELEASE", "mobile-test.123")
    return tmp_path


def test_gateway_refuses_missing_admin(mobile_env, monkeypatch):
    monkeypatch.delenv("VORTOCODE_API_TOKEN")
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        create_app()


def test_gateway_refuses_wrong_state_root(mobile_env, monkeypatch):
    monkeypatch.setenv("VORTOCODE_MOBILE_STATE_ROOT", str(mobile_env / "other"))
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
