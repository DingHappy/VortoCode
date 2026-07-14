"""成本追踪 tokens→费用 测试（离线）。"""

import json

import pytest

from src.models import cost_for, track_usage, cost_tracker
from src.agents.roles import DeveloperAgent


def test_cost_for_known_and_unknown():
    # mimo-v2.5 定价：0.001/1k 输入，0.002/1k 输出
    assert abs(cost_for("mimo-v2.5", 1000, 1000) - 0.003) < 1e-9
    assert cost_for("nonexistent-model", 1000, 1000) == 0.0


def test_track_usage_accumulates_cost():
    cost_tracker.entries.clear()
    c = track_usage("gpt-4o", 1000, 500, agent="developer")   # 0.005 + 0.0075
    assert abs(c - 0.0125) < 1e-9
    assert cost_tracker.entries[-1]["model"] == "gpt-4o"
    assert cost_tracker.entries[-1]["agent"] == "developer"


@pytest.mark.asyncio
async def test_complete_feeds_cost_tracker(tmp_path):
    cost_tracker.entries.clear()

    class FakeLLM:
        async def chat(self, messages, model=None, temperature=None, **kw):
            return {
                "content": json.dumps({"files": [{"path": "a.py", "content": "x = 1\n"}]}),
                "model": "mimo-v2.5",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            }

    dev = DeveloperAgent(llm_client=FakeLLM())
    await dev.execute("x", context={"workspace": str(tmp_path)})

    assert any(e["agent"] == "developer" and e["model"] == "mimo-v2.5"
               for e in cost_tracker.entries)


def test_add_usage_feeds_cost_tracker():
    """主线计量口 add_usage 必须给 cost_tracker 记账——主 agent 走 LLMClient 直连、
    不经 BaseAgent，此前它的花费从不进成本系统（/api/cost/report 恒空、预算告警是死的）。"""
    from src.llm.client import add_usage
    cost_tracker.entries.clear()
    add_usage(1000, 1000, model="mimo-v2.5")          # 0.001 + 0.002
    assert cost_tracker.entries[-1]["model"] == "mimo-v2.5"
    assert abs(cost_tracker.entries[-1]["cost"] - 0.003) < 1e-9


def test_add_usage_unknown_model_tracks_zero_cost():
    from src.llm.client import add_usage
    cost_tracker.entries.clear()
    add_usage(1000, 1000, model="nonexistent-model")   # 未登记定价 → 记账但费用 0
    assert cost_tracker.entries[-1]["cost"] == 0.0


def test_add_usage_without_model_skips_tracker():
    from src.llm.client import add_usage
    cost_tracker.entries.clear()
    add_usage(100, 100)                                # 无模型名 → 不进成本系统（无从计价）
    assert cost_tracker.entries == []


def test_add_usage_total_budget_alert(monkeypatch):
    """经 add_usage 进账也要触发总预算告警（VORTOCODE_COST_BUDGET）。"""
    from src.models.router import CostTracker
    from src.models import cost as cost_mod
    from src.llm.client import add_usage
    monkeypatch.setattr(cost_mod, "cost_tracker", CostTracker())
    monkeypatch.setenv("VORTOCODE_COST_BUDGET", "0.0001")
    add_usage(1000, 1000, model="mimo-v2.5")          # 0.003 > 0.0001
    assert any(a.get("type") == "total_budget_exceeded" for a in cost_mod.cost_tracker.alerts)
