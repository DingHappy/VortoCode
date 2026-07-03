"""评测 harness 的 live 冒烟（gated，默认 skip）——证明 harness 能真机驱动模型并产出评分。

只跑最便宜的 no_gitignore 一个场景（单次 dev_isolated）。真机非确定，故只断言"端到端跑通、
产出结构完整的 Score"，不对具体通过与否下断言（那属于基线分析，不进 CI）。

开跑：`VORTOCODE_LIVE_TESTS=1 OPENAI_API_KEY=... python -m pytest tests/live/test_live_evals.py`
"""

import os

import pytest

_LIVE = os.getenv("VORTOCODE_LIVE_TESTS") == "1" and bool(os.getenv("OPENAI_API_KEY"))

pytestmark = pytest.mark.skipif(not _LIVE, reason="live 评测冒烟需 VORTOCODE_LIVE_TESTS=1 + OPENAI_API_KEY")


@pytest.mark.asyncio
async def test_live_eval_no_gitignore_smoke(tmp_path):
    from evals.runner import run_scenario
    from evals.scenarios import BY_NAME
    from evals.scoring import Score

    s, note = await run_scenario(BY_NAME["no_gitignore"], tmp_path)
    assert s is not None, f"评测被跳过而非执行：{note}"
    assert isinstance(s, Score)
    for field in (s.landed, s.honest, s.clean):     # 三项指标都被算出（bool）
        assert isinstance(field, bool)
    assert s.message_excerpt                        # 工具确实返回了非空消息
