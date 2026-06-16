"""可观测/指标埋点测试（离线）。"""

import json

import pytest

from src.core import metrics
from src.agents.roles import DeveloperAgent
from src.agents.base import Agent, AgentConfig, AgentResult
from src.orchestrator.dev_loop import IterativeDevLoop


class FakeLLM:
    def __init__(self, content):
        self.content = content

    async def chat(self, messages, model=None, temperature=None, **kw):
        return {"content": self.content, "usage": {"total_tokens": 7}}


class _Dev(Agent):
    def __init__(self):
        super().__init__(AgentConfig(role="developer"))

    async def execute(self, t, **kw):
        return AgentResult(success=True, output={"files_created": []}, files_created=[])


class _Tester(Agent):
    def __init__(self):
        super().__init__(AgentConfig(role="tester"))

    async def execute(self, t, **kw):
        return AgentResult(success=True, output={"passed": True})


class _Rev(Agent):
    def __init__(self):
        super().__init__(AgentConfig(role="reviewer"))

    async def execute(self, t, **kw):
        return AgentResult(success=True, output={"verdict": "approve"})


@pytest.mark.asyncio
async def test_records_llm_calls_and_tokens(tmp_path):
    metrics.reset()
    payload = json.dumps({"files": [{"path": "a.py", "content": "x = 1\n"}]})
    dev = DeveloperAgent(llm_client=FakeLLM(payload))
    await dev.execute("x", context={"workspace": str(tmp_path)})

    assert metrics.get_counter("llm.calls", {"role": "developer"}) >= 1
    assert metrics.get_counter("llm.tokens") >= 7


@pytest.mark.asyncio
async def test_records_devloop_iterations(tmp_path):
    metrics.reset()
    await IterativeDevLoop(_Dev(), _Tester(), _Rev(), max_iterations=2).run(
        "x", workspace=str(tmp_path))
    assert metrics.get_counter("devloop.runs") >= 1
    assert metrics.get_counter("devloop.iterations") >= 1


@pytest.mark.asyncio
async def test_metrics_endpoint_reflects_activity(tmp_path):
    from fastapi.testclient import TestClient
    from src.web.server import app

    metrics.reset()
    await IterativeDevLoop(_Dev(), _Tester(), _Rev(), max_iterations=1).run(
        "x", workspace=str(tmp_path))

    r = TestClient(app).get("/api/monitoring/metrics")
    assert r.status_code == 200
    data = r.json()
    assert "metrics" in data
    assert any("devloop.iterations" in k for k in data["metrics"].get("counters", {}))


def test_timer_percentiles():
    metrics.reset()
    for v in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        metrics.record_time("t", v)
    s = metrics.get_timer_stats("t")
    assert s["count"] == 10
    assert 0.4 <= s["p50"] <= 0.6
    assert s["p95"] >= 0.9
    assert s["p99"] == 1.0
