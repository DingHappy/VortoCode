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
    # 去掉 key：端点走确定性回退，断言稳定（本地 .env 有 key 时也不打真网络）
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return TestClient(app, raise_server_exceptions=False)


class _FakeLLM:
    """注入用：模拟 LLMClient.analyze（prompt→text），不触网。"""
    def __init__(self, out):
        self._out = out

    async def analyze(self, prompt, system_prompt=""):
        return self._out


@pytest.mark.asyncio
async def test_agenerate_tests_uses_llm_when_provided():
    from src.testing import TestGenerator
    out = await TestGenerator().agenerate_tests(
        "def f():\n    return 1\n", "python", "unit",
        llm_client=_FakeLLM("def test_f():\n    assert f() == 1\n"))
    assert out == "def test_f():\n    assert f() == 1\n"


@pytest.mark.asyncio
async def test_agenerate_strips_code_fence():
    from src.testing import TestGenerator
    out = await TestGenerator().agenerate_tests(
        "def f(): pass", llm_client=_FakeLLM("```python\nX = 1\n```"))
    assert out == "X = 1"


@pytest.mark.asyncio
async def test_agenerate_tests_falls_back_on_llm_error():
    from src.testing import TestGenerator

    class _Boom:
        async def analyze(self, *a, **k):
            raise RuntimeError("boom")

    out = await TestGenerator().agenerate_tests(
        "def add(a, b):\n    return a + b\n", llm_client=_Boom())
    assert "def test_add" in out  # 回退到确定性骨架


@pytest.mark.asyncio
async def test_agenerate_docs_uses_llm_when_provided():
    from src.documentation import DocGenerator
    out = await DocGenerator().agenerate_docs(
        "def f(): pass", "python", llm_client=_FakeLLM("# LLM 文档"))
    assert out == "# LLM 文档"


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


def test_testing_generate_javascript(client):
    # 回归：此前 _analyze_javascript 缺失，language=javascript 会 AttributeError/500
    r = client.post("/api/testing/generate", json={
        "code": "export function add(a, b) { return a + b }",
        "language": "javascript",
        "test_type": "unit",
    })
    assert r.status_code == 200
    j = r.json()
    assert j["success"] is True
    assert "describe('add'" in j["tests"]
