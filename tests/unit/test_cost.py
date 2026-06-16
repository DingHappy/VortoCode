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
