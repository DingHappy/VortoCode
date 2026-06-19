"""创建的 agent 现在能真跑：ConfigAgent 执行 + /api/agents/{id}/run 端点（离线）。"""

import pytest
from fastapi.testclient import TestClient

from src.web.server import app

client = TestClient(app)


@pytest.mark.asyncio
async def test_config_agent_runs_with_system_prompt():
    from src.agents.config_agent import build_config_agent

    class FakeLLM:
        async def chat(self, messages, model=None, temperature=None, max_tokens=None, stream=False):
            assert any(m["role"] == "system" for m in messages)   # system_prompt 进了消息
            return {"content": "完成了"}

    agent = build_config_agent("我的agent", "custom", "你是助手", "inherit", llm_client=FakeLLM())
    res = await agent.execute("做点事")
    assert res.success is True and res.output == "完成了"


def test_run_unknown_agent_returns_error():
    r = client.post("/api/agents/nope-xyz/run", json={"task": "x"})
    assert r.json() == {"success": False, "error": "Agent not found"}


def test_run_known_agent_dispatches(monkeypatch):
    from src.web.routers import execution

    async def fake_run(agent_id, config, task):   # 避免后台真打 LLM
        return None

    monkeypatch.setattr(execution, "_run_single_agent", fake_run)

    aid = client.post("/api/agents/custom",
                      json={"name": "Z_run", "role": "custom"}).json()["agent"]["id"]
    try:
        r = client.post(f"/api/agents/{aid}/run", json={"task": "做事"}).json()
        assert r["success"] is True and r["agent_id"] == aid
    finally:
        client.delete(f"/api/agents/{aid}")
