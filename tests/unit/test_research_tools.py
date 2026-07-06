"""只读子 agent 委派工具（build_research_tools：task / research_parallel）—— UI 无关，给 Web/CLI。

主 agent 把大型只读调查甩给隔离的只读子 agent（独立上下文、不污染主对话、不会嵌套/改文件）。
用注入的假 LLM 驱动子 agent，确定性、不触网。
"""

import pytest

from src.agents.main_agent import build_research_tools


class EchoLLM:
    """无脑直接给结论（不调工具）：把最近 user 文本回显，证明子 agent 确实收到了该子任务。"""

    async def chat(self, messages, **kwargs):
        from src.llm.content import content_to_text
        last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        return {"content": f"结论<{content_to_text(last_user)}>"}


def _by_name(repo, **kw):
    return {t.name: t for t in build_research_tools(repo, **kw)}


def test_exposes_task_and_parallel_readonly(tmp_path):
    by = _by_name(str(tmp_path))
    assert set(by) == {"task", "research_parallel"}
    assert by["task"].read_only and by["research_parallel"].read_only   # 只读 → plan 模式也能用


@pytest.mark.asyncio
async def test_task_delegates_and_returns_conclusion(tmp_path):
    by = _by_name(str(tmp_path), llm=EchoLLM())
    out = await by["task"].handler({"description": "查认证模块怎么校验 token"})
    assert "查认证模块怎么校验 token" in out                # 子 agent 收到该子任务并返回了结论
    # 缺 description → 友好报错，不崩
    assert "需要 description" in await by["task"].handler({})


@pytest.mark.asyncio
async def test_task_subagent_actually_uses_read_tools(tmp_path):
    """端到端证明子 agent 真的带 read_file 且能读到文件内容（不是空壳）。"""
    (tmp_path / "a.py").write_text("# MAGIC_XYZ\ndef go(): pass\n", encoding="utf-8")

    class ReadThenConclude:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:                              # 先读文件
                return {"content": '{"tool":"read_file","args":{"path":"a.py"}}'}
            joined = " ".join(str(m.get("content")) for m in messages)
            return {"content": "FOUND" if "MAGIC_XYZ" in joined else "MISSING"}

    by = _by_name(str(tmp_path), llm=ReadThenConclude())
    out = await by["task"].handler({"description": "a.py 里有什么标记"})
    assert out == "FOUND"                                    # 读到了文件内容 → 子 agent 工具链通


@pytest.mark.asyncio
async def test_research_parallel_runs_all_and_labels(tmp_path):
    by = _by_name(str(tmp_path), llm=EchoLLM())
    out = await by["research_parallel"].handler({"tasks": ["问题甲", "问题乙"]})
    assert "【问题甲】" in out and "【问题乙】" in out          # 各路结论带标题
    assert "结论<问题甲>" in out and "结论<问题乙>" in out
    # 空列表 → 友好报错
    assert "需要 tasks" in await by["research_parallel"].handler({"tasks": []})


@pytest.mark.asyncio
async def test_research_parallel_coerces_str_and_caps(tmp_path):
    by = _by_name(str(tmp_path), llm=EchoLLM(), max_parallel=3, default_parallel=3)
    # 传字符串 → 当单元素
    one = await by["research_parallel"].handler({"tasks": "只一个问题"})
    assert "【只一个问题】" in one
    # 超过上限 → 截断到 max_parallel
    many = await by["research_parallel"].handler({"tasks": [f"q{i}" for i in range(10)]})
    assert "【q0】" in many and "【q2】" in many and "【q3】" not in many


@pytest.mark.asyncio
async def test_research_parallel_defaults_light_but_expands_with_reason(tmp_path):
    by = _by_name(str(tmp_path), llm=EchoLLM(), max_parallel=5, default_parallel=2)
    tasks = [f"q{i}" for i in range(5)]

    light = await by["research_parallel"].handler({"tasks": tasks})
    assert "【q0】" in light and "【q1】" in light and "【q2】" not in light

    no_reason = await by["research_parallel"].handler({"tasks": tasks, "max_parallel": 5})
    assert "【q0】" in no_reason and "【q1】" in no_reason and "【q2】" not in no_reason

    expanded = await by["research_parallel"].handler({
        "tasks": tasks,
        "max_parallel": 5,
        "reason": "用户明确要求从多个模块全面审查",
    })
    assert "【q0】" in expanded and "【q4】" in expanded


@pytest.mark.asyncio
async def test_subagent_error_is_caught(tmp_path):
    class BoomLLM:
        async def chat(self, messages, **kwargs):
            raise RuntimeError("relay down")

    by = _by_name(str(tmp_path), llm=BoomLLM())
    out = await by["task"].handler({"description": "随便查点东西"})
    # 子 agent 内部 LLM 报错被 MainAgent 兜成"对话出错"文本回复 → task 仍返回字符串、不抛
    assert isinstance(out, str) and out
