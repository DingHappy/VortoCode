"""成本预算告警测试。"""

import pytest

from src.models import cost_tracker, set_budget, track_usage


def _reset():
    cost_tracker.entries.clear()
    cost_tracker.alerts.clear()
    cost_tracker.budgets.clear()


def test_per_agent_budget_alert():
    _reset()
    set_budget("developer", 0.001)
    track_usage("gpt-4o", 1000, 500, agent="developer")     # 0.0125 > 0.001
    assert any(a["type"] == "budget_exceeded" for a in cost_tracker.alerts)


def test_total_budget_alert(monkeypatch):
    _reset()
    monkeypatch.setenv("VORTOCODE_COST_BUDGET", "0.001")
    track_usage("gpt-4o", 1000, 500, agent="x")
    assert any(a["type"] == "total_budget_exceeded" for a in cost_tracker.alerts)


def test_no_alert_within_budget():
    _reset()
    set_budget("dev", 100.0)
    track_usage("gpt-4o", 1000, 500, agent="dev")           # 远低于预算
    assert cost_tracker.alerts == []


@pytest.mark.asyncio
async def test_cost_report_exposes_alerts():
    from fastapi.testclient import TestClient
    from src.web.server import app
    _reset()
    set_budget("dev", 0.0001)
    track_usage("gpt-4o", 1000, 500, agent="dev")
    r = TestClient(app).get("/api/cost/report?period=all").json()
    assert "alerts" in r
    assert len(r["alerts"]) >= 1
