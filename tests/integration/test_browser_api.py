"""浏览器端点安全测试：默认禁用闸 + 导航 URL 防 SSRF/file://（离线）。

被拦的请求都在「启动真实浏览器之前」返回，故无需 Playwright。
"""

import pytest
from fastapi.testclient import TestClient

from src.web.server import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    monkeypatch.delenv("VORTOCODE_ENABLE_BROWSER", raising=False)
    return TestClient(app, raise_server_exceptions=False)


def test_browser_endpoints_disabled_by_default(client):
    assert client.post("/api/browser/navigate",
                       json={"url": "https://example.com"}).status_code == 403
    assert client.post("/api/browser/screenshot").status_code == 403
    assert client.post("/api/browser/click", params={"selector": "#x"}).status_code == 403


def test_navigate_rejects_non_http_scheme(client, monkeypatch):
    monkeypatch.setenv("VORTOCODE_ENABLE_BROWSER", "1")
    r = client.post("/api/browser/navigate", json={"url": "file:///etc/passwd"})
    assert r.status_code == 400
    assert "URL 被拒绝" in r.json().get("detail", "")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x",                       # 环回
    "http://169.254.169.254/latest/meta-data",  # 云元数据 SSRF 经典
    "http://10.0.0.1/",                          # 私有网段
    "http://localhost:8080/",                    # 解析到环回
])
def test_navigate_blocks_ssrf_targets(client, monkeypatch, url):
    monkeypatch.setenv("VORTOCODE_ENABLE_BROWSER", "1")
    r = client.post("/api/browser/navigate", json={"url": url})
    assert r.status_code == 400
