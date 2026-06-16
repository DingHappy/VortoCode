"""生成器 API 端点测试（离线、确定性）。

回归点：/api/docs/generate(python) 此前调用不存在的 analyze_python_file_content
会 500——本测试守住修复。
"""

import pytest
from fastapi.testclient import TestClient

from src.web.server import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("AUTODEV_API_TOKEN", raising=False)
    return TestClient(app, raise_server_exceptions=False)


def test_docs_generate_python(client):
    code = '"""模块。"""\n\n\ndef f(x: int) -> int:\n    """doc"""\n    return x\n'
    r = client.post("/api/docs/generate", json={"code": code, "language": "python"})
    assert r.status_code == 200
    j = r.json()
    assert j["success"] is True
    assert j["docs"].startswith("#")        # 产出了 Markdown（此前会 500）
    assert "f" in j["docs"]


def test_docs_generate_non_python_passthrough(client):
    r = client.post("/api/docs/generate", json={"code": "hello", "language": "go"})
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_testing_generate_python(client):
    r = client.post("/api/testing/generate", json={
        "code": "def add(a, b):\n    return a + b\n",
        "language": "python",
        "test_type": "unit",
    })
    assert r.status_code == 200
    j = r.json()
    assert j["success"] is True
    assert "def test_add" in j["tests"]
