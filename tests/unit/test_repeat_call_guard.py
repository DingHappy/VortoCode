"""只读工具被原样重复调用时的提醒/省略（src/agents/agent_loop.py）。

真机（2026-09-19 与 09-20 两轮打包版冒烟）：一个 339 字节的 todo.py 被 read_file 连读 4 次、
7 次，每次结果**逐字节相同**，中间夹着完整的模型往返——154 秒的任务约 78 秒耗在这上面。
结果喂回本身没问题（native 下 assistant.tool_calls + role=tool，转换无误），是模型原地打转；
但历史里连着几条一模一样的内容，最容易让它接着照抄下一次。

这里钉住：第 2 次起在结果后点一句，第 3 次起不再重放正文（正文一字未丢，就在上面几条里）；
写/执行类工具和"结果变了"一律不受影响——那两种重复是正当的。
"""

import pytest

from src.agents.agent_loop import MainAgent
from src.agents.tool import Tool


def _agent(tools):
    return MainAgent(tools, max_steps=8)


def _read_tool(box):
    async def handler(_args):
        return box["text"]
    return Tool("read_file", "读文件", {"type": "object", "properties": {}},
                handler, read_only=True)


def _write_tool(box):
    async def handler(_args):
        return box["text"]
    return Tool("edit_file", "改文件", {"type": "object", "properties": {}},
                handler, read_only=False)


async def _call(agent, name, args):
    return await agent._run_tool(name, args, "build", lambda _s: None)


@pytest.mark.asyncio
async def test_identical_read_is_noted_then_elided():
    box = {"text": "class TodoList: ..."}
    agent = _agent([_read_tool(box)])

    first = await _call(agent, "read_file", {"path": "todo.py"})
    assert first == box["text"], "第一次必须原样给出"

    second = await _call(agent, "read_file", {"path": "todo.py"})
    assert second.startswith(box["text"]), "第二次正文仍在，只是后面多一句提醒"
    assert "已经用同样的参数调过 2 次" in second

    third = await _call(agent, "read_file", {"path": "todo.py"})
    assert box["text"] not in third, f"第三次不该再重放正文：{third}"
    assert "第 3 次" in third and "不再重复贴出" in third

    fourth = await _call(agent, "read_file", {"path": "todo.py"})
    assert "第 4 次" in fourth


@pytest.mark.asyncio
async def test_changed_result_is_not_treated_as_repeat():
    """内容变了就是新信息——哪怕参数一样，也必须原样给出。"""
    box = {"text": "v1"}
    agent = _agent([_read_tool(box)])
    await _call(agent, "read_file", {"path": "todo.py"})
    box["text"] = "v2"
    again = await _call(agent, "read_file", {"path": "todo.py"})
    assert again == "v2"
    box["text"] = "v1"
    back = await _call(agent, "read_file", {"path": "todo.py"})
    assert back.startswith("v1") and "已经用同样的参数调过 2 次" in back


@pytest.mark.asyncio
async def test_different_args_are_counted_separately():
    box = {"text": "same bytes"}
    agent = _agent([_read_tool(box)])
    await _call(agent, "read_file", {"path": "a.py"})
    other = await _call(agent, "read_file", {"path": "b.py"})
    assert other == "same bytes", "换了参数就是另一次调用，不该算重复"


@pytest.mark.asyncio
async def test_write_tools_are_never_elided():
    """写/执行类重复调用可能是真的要再做一次，正文一律不动。"""
    box = {"text": "已写入"}
    agent = _agent([_write_tool(box)])
    for _ in range(4):
        assert await _call(agent, "edit_file", {"path": "todo.py"}) == "已写入"


@pytest.mark.asyncio
async def test_counter_is_turn_scoped(monkeypatch):
    """新回合重读同一个文件是正当的——计数必须清零。"""
    box = {"text": "class TodoList: ..."}
    agent = _agent([_read_tool(box)])
    await _call(agent, "read_file", {"path": "todo.py"})
    await _call(agent, "read_file", {"path": "todo.py"})
    assert agent._repeat_calls

    async def fake_body(*_a, **_kw):
        return "ok"

    monkeypatch.setattr(MainAgent, "_run_turn_body", fake_body)
    await agent.run_turn("再看看那个文件", mode="build")
    assert agent._repeat_calls == {}, "回合开始必须清零"
    again = await _call(agent, "read_file", {"path": "todo.py"})
    assert again == box["text"]
