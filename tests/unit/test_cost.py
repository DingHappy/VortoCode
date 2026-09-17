"""成本追踪 tokens→费用 测试（离线）。"""

import json

import pytest

from src.models import cost_for, track_usage, cost_tracker
from src.agents.roles import DeveloperAgent


def test_cost_for_known_and_unknown():
    # mimo-v2.5 定价：0.001/1k 输入，0.002/1k 输出
    assert abs(cost_for("mimo-v2.5", 1000, 1000) - 0.003) < 1e-9
    assert cost_for("nonexistent-model", 1000, 1000) is None


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


def test_add_usage_unknown_model_preserves_unpriced_tokens():
    from src.llm.client import add_usage
    cost_tracker.entries.clear()
    add_usage(1000, 1000, model="nonexistent-model")
    assert cost_tracker.entries[-1]["cost"] is None
    report = cost_tracker.get_report("all")
    assert report["total_cost"] is None and not report["pricing_complete"]
    assert report["unpriced_calls"] == 1 and report["unpriced_tokens"] == 2000
    assert report["total_input_tokens"] == report["total_output_tokens"] == 1000


def test_mixed_pricing_keeps_known_subtotal_without_claiming_complete_cost(monkeypatch):
    from src.models.router import CostTracker
    from src.models import cost as cost_mod
    tracker = CostTracker()
    monkeypatch.setattr(cost_mod, "cost_tracker", tracker)
    monkeypatch.setenv("VORTOCODE_COST_BUDGET", "0.001")
    tracker.set_budget("dev", 0.001)
    cost_mod.track_usage("unknown", 1000, 500, agent="dev")
    cost_mod.track_usage("mimo-v2.5", 1000, 1000, agent="dev")
    report = tracker.get_report("all")
    assert report["total_cost"] is None
    assert report["known_cost"] == pytest.approx(0.003)
    assert report["by_agent"]["dev"]["cost"] is None
    assert report["by_model"]["unknown"]["unpriced_tokens"] == 1500
    assert report["by_model"]["mimo-v2.5"]["cost"] == pytest.approx(0.003)
    assert {a["type"] for a in tracker.alerts} == {"budget_exceeded", "total_budget_exceeded"}
    tracker.get_optimization_suggestions()  # 未知费用不应让报告生成崩溃


def test_explicit_free_pricing_is_distinct_from_missing_pricing(monkeypatch):
    from src.models import cost as cost_mod
    from src.models.router import ModelConfig, ModelTier
    base = dict(id="local", name="local", provider="local", tier=ModelTier.ECONOMY)
    monkeypatch.setitem(cost_mod._router.models, "local", ModelConfig(**base))
    assert cost_for("local", 100, 50) is None
    monkeypatch.setitem(cost_mod._router.models, "local", ModelConfig(
        **base, cost_per_1k_input=0, cost_per_1k_output=0))
    assert cost_for("local", 100, 50) == 0


def test_tui_shows_unknown_total_and_preserves_known_subtotal():
    from types import SimpleNamespace
    from src.llm.client import usage_scope, add_usage
    from src.tui.commands import TUICommandsMixin
    output = []
    view = SimpleNamespace(_emit=output.append, _context_usage_label=lambda: "")
    with usage_scope():
        add_usage(1000, 1000, model="mimo-v2.5")
        add_usage(100, 100, model="unpriced")
        TUICommandsMixin._cmd_usage(view, "")
    assert "费用未知（未配置单价）" in output[0]
    assert "费用合计未知" in output[0] and "$0.0030" in output[0]


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
