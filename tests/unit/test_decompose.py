"""自动分解（src/agents/decompose.decompose_for_parallel）—— 注入假分解器，纯离线。"""

import pytest

from src.agents.decompose import decompose_for_parallel
from src.orchestrator.task_analyzer import SubTask


class _FakeAnalyzer:
    async def analyze(self, task, context=None):
        return object()                      # decompose 不真正用它（假分解器忽略）


class _FakeDecomposer:
    def __init__(self, subs):
        self._subs = subs

    async def decompose(self, task, analysis):
        return self._subs


@pytest.mark.asyncio
async def test_splits_independent_and_deferred():
    subs = [
        SubTask(id="t1", title="补 content 测试", description="给 content.py 写单测"),
        SubTask(id="t2", title="补 shell 测试", description="给 shell.py 写单测"),
        SubTask(id="t3", title="集成", description="整合两者", dependencies=["t1", "t2"]),
    ]
    out = await decompose_for_parallel(
        "大任务", analyzer=_FakeAnalyzer(), decomposer=_FakeDecomposer(subs))
    assert out["total"] == 3
    assert len(out["independent"]) == 2 and len(out["deferred"]) == 1
    assert out["deferred"][0].id == "t3"
    # 描述串由 title：description 拼成
    assert any("补 content 测试：给 content.py 写单测" == d for d in out["descriptions"])


@pytest.mark.asyncio
async def test_acceptance_criteria_appended():
    subs = [SubTask(id="a", title="加端点", description="实现 /health",
                    acceptance_criteria=["返回 200", "带版本号"])]
    out = await decompose_for_parallel("x", analyzer=_FakeAnalyzer(), decomposer=_FakeDecomposer(subs))
    assert "验收标准：返回 200；带版本号" in out["descriptions"][0]


@pytest.mark.asyncio
async def test_caps_at_max_parallel():
    subs = [SubTask(id=f"t{i}", title=f"任务{i}", description="做点啥") for i in range(10)]
    out = await decompose_for_parallel(
        "x", analyzer=_FakeAnalyzer(), decomposer=_FakeDecomposer(subs), max_parallel=3)
    assert len(out["descriptions"]) == 3 and len(out["independent"]) == 10   # 描述截到 3，但 independent 全列


@pytest.mark.asyncio
async def test_all_dependent_yields_no_parallel():
    subs = [
        SubTask(id="a", title="A", description="先做 A"),
        SubTask(id="b", title="B", description="依赖 A", dependencies=["a"]),
    ]
    out = await decompose_for_parallel("x", analyzer=_FakeAnalyzer(), decomposer=_FakeDecomposer(subs))
    # A 无依赖 → 可并行；B 依赖 A → deferred
    assert [s.id for s in out["independent"]] == ["a"]
    assert [s.id for s in out["deferred"]] == ["b"]


def test_dev_auto_tool_registered(tmp_path):
    from src.agents.main_agent import build_dev_tools
    by = {t.name: t for t in build_dev_tools(str(tmp_path))}
    assert "dev_auto" in by and by["dev_auto"].read_only is False
