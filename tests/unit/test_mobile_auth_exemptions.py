"""手机路由的鉴权豁免表必须与实际路由一致：不留失效条目，表外的手机路由一律要管理员令牌。"""

import re

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import pytest

from src.gateway.workspace_scope import http_workspace_requirement
from src.web.auth import MOBILE_SELF_AUTHENTICATED
from src.web.mobile_server import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "mobile-exemption-admin-token")
    monkeypatch.setenv("VORTOCODE_MOBILE_PUBLIC_ORIGIN", "https://assistant.example.com")
    monkeypatch.setenv("VORTOCODE_MOBILE_STATE_ROOT", str(tmp_path))
    return create_app()


def _mobile_api_routes(app):
    def walk(routes):
        for route in routes:
            nested = getattr(route, "original_router", None)  # fastapi 0.137+ 惰性 include
            if nested is not None:
                yield from walk(nested.routes)
            elif isinstance(route, APIRoute) and route.path.startswith("/api/mobile/"):
                for method in route.methods - {"HEAD", "OPTIONS"}:
                    yield method, route.path

    return set(walk(app.routes))


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "0" * 32, path)


def test_every_exemption_names_a_registered_route(app):
    registered = _mobile_api_routes(app)
    assert MOBILE_SELF_AUTHENTICATED <= registered, MOBILE_SELF_AUTHENTICATED - registered


def test_routes_outside_the_table_require_the_administrator(app):
    guarded = [(m, p) for m, p in sorted(_mobile_api_routes(app)) if (m, p) not in MOBILE_SELF_AUTHENTICATED]
    assert guarded, "设备管理、配对审批、账号登记等管理路由应当存在"
    with TestClient(app, base_url="https://assistant.example.com") as client:
        for method, path in guarded:
            response = client.request(method, _concrete(path))
            assert response.status_code == 401, (method, path, response.status_code)
            assert response.headers["cache-control"] == "no-store"


def test_every_mobile_route_is_reachable_from_the_general_scope(app):
    for _, path in _mobile_api_routes(app):
        assert http_workspace_requirement(_concrete(path), "general") is None, path
