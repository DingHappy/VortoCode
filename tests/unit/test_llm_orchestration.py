"""TaskAnalyzer / TaskDecomposer 的 LLM 路径测试（离线，注入 FakeLLM，不打网络）。

覆盖：LLM 分析中文任务、健壮解析（围栏/垃圾降级）、LLM 分解的依赖重映射与防环、分解降级。
"""

import json

import pytest

from src.orchestrator.task_analyzer import (
    TaskAnalyzer, TaskDecomposer, TaskComplexity, TaskAnalysis,
)


class FakeLLM:
    """模拟 LLMClient.analyze(prompt, system)。"""

    def __init__(self, response):
        self._response = response
        self.calls = []

    async def analyze(self, prompt, system_prompt=""):
        self.calls.append((prompt, system_prompt))
        return self._response


# --------------------------------------------------------------- TaskAnalyzer

@pytest.mark.asyncio
async def test_analyzer_llm_path_parses_chinese():
    resp = json.dumps({
        "complexity": "complex",
        "capabilities": ["code_generation", "testing"],
        "parallelizable": True, "risk_level": "high", "effort": "high",
        "agents": ["developer", "tester"],
    })
    a = TaskAnalyzer(use_llm=True, llm_client=FakeLLM(resp))
    res = await a.analyze("实现用户认证功能并编写测试")  # 规则版会误判成 TRIVIAL
    assert res.complexity == TaskComplexity.COMPLEX
    assert "code_generation" in res.required_capabilities
    assert res.risk_level == "high"


@pytest.mark.asyncio
async def test_analyzer_fenced_json():
    a = TaskAnalyzer(use_llm=True, llm_client=FakeLLM('```json\n{"complexity":"epic"}\n```'))
    res = await a.analyze("build a platform")
    assert res.complexity == TaskComplexity.EPIC
    # 缺 capabilities 时用规则兜底，不为空
    assert res.required_capabilities


@pytest.mark.asyncio
async def test_analyzer_garbage_falls_back_to_rules():
    a = TaskAnalyzer(use_llm=True, llm_client=FakeLLM("这不是 JSON"))
    res = await a.analyze("fix typo in readme")   # 降级规则 → TRIVIAL
    assert res.complexity == TaskComplexity.TRIVIAL


# -------------------------------------------------------------- TaskDecomposer

@pytest.mark.asyncio
async def test_decomposer_llm_remaps_deps():
    resp = json.dumps([
        {"id": "A", "title": "设计", "description": "设计接口",
         "required_capabilities": ["architecture"], "dependencies": []},
        {"id": "B", "title": "实现", "description": "写代码",
         "required_capabilities": ["code_generation"], "dependencies": ["A"]},
        {"id": "C", "title": "测试", "description": "写测试",
         "required_capabilities": ["testing"], "dependencies": ["B"]},
    ])
    analysis = TaskAnalysis(complexity=TaskComplexity.COMPLEX,
                            required_capabilities=["code_generation"])
    d = TaskDecomposer(use_llm=True, llm_client=FakeLLM(resp))
    subs = await d.decompose("做个东西", analysis)
    assert [s.id for s in subs] == ["subtask-1", "subtask-2", "subtask-3"]
    assert subs[0].dependencies == []
    assert subs[1].dependencies == ["subtask-1"]   # A 重映射为 subtask-1
    assert subs[2].dependencies == ["subtask-2"]


@pytest.mark.asyncio
async def test_decomposer_drops_self_and_forward_deps_no_cycle():
    resp = json.dumps([
        {"id": "A", "title": "a", "description": "a", "dependencies": ["A"]},        # 自依赖
        {"id": "B", "title": "b", "description": "b", "dependencies": ["C"]},        # 指向更晚
        {"id": "C", "title": "c", "description": "c", "dependencies": ["A", "B"]},
    ])
    analysis = TaskAnalysis(complexity=TaskComplexity.COMPLEX,
                            required_capabilities=["code_generation"])
    d = TaskDecomposer(use_llm=True, llm_client=FakeLLM(resp))
    subs = await d.decompose("x", analysis)
    by_id = {s.id: s for s in subs}
    assert by_id["subtask-1"].dependencies == []                       # 自依赖丢弃
    assert by_id["subtask-2"].dependencies == []                       # 前向依赖丢弃
    assert set(by_id["subtask-3"].dependencies) == {"subtask-1", "subtask-2"}
    # 无环：每个依赖序号都严格小于自身
    for s in subs:
        idx = int(s.id.split("-")[1])
        assert all(int(dep.split("-")[1]) < idx for dep in s.dependencies)


@pytest.mark.asyncio
async def test_decomposer_garbage_falls_back_to_rules():
    analysis = TaskAnalysis(complexity=TaskComplexity.COMPLEX,
                            required_capabilities=["architecture", "code_generation"])
    d = TaskDecomposer(use_llm=True, llm_client=FakeLLM("不是 JSON 数组"))
    subs = await d.decompose("x", analysis)
    assert len(subs) == 2                       # 规则版：architecture + code_generation
    assert subs[0].id == "subtask-1"
    assert subs[1].dependencies == ["subtask-1"]
