"""等人的时间不算干活的时间。

真机冒烟（2026-09-18）：时间线顶上写「已工作 14 秒」，工具行写「运行 xxx · 2 分 13 秒」，
而那两分钟 agent 一直停在确认框前等人点头。耗时一旦把等待算进去，它就不再能回答任何问题
（是模型慢、命令慢，还是我慢？）。
"""

import asyncio

import pytest

from src.agents import wait_clock as wc


def test_without_a_turn_it_reads_zero_and_does_not_blow_up():
    """没开回合也要能用：只是不累计，绝不抛。"""
    wc._CLOCK.set(None)
    assert wc.waited() == 0.0
    wc.add_wait(5)                        # 无声丢弃
    assert wc.waited() == 0.0


def test_accumulates_within_a_turn():
    wc.start_turn()
    wc.add_wait(1.5)
    wc.add_wait(2.0)
    assert wc.waited() == pytest.approx(3.5)
    wc.start_turn()                        # 新回合归零
    assert wc.waited() == 0.0


def test_negative_waits_are_ignored():
    wc.start_turn()
    wc.add_wait(-10)
    assert wc.waited() == 0.0


async def test_waiting_block_counts_even_when_it_is_cancelled():
    """用户点「停止」之前的那段等待也是等待，异常照常往外传。"""
    wc.start_turn()
    with pytest.raises(RuntimeError):
        async with wc.waiting():
            await asyncio.sleep(0.02)
            raise RuntimeError("停止")
    assert wc.waited() >= 0.02


def test_minus_wait_never_goes_negative():
    wc.start_turn()
    wc.add_wait(100)
    assert wc.minus_wait(3.0, waited_before=0.0) == 0.0
    assert wc.minus_wait(120.0, waited_before=0.0) == pytest.approx(20.0)


def test_only_the_wait_inside_this_span_is_subtracted():
    """扣的是"这段耗时之内"发生的等待，之前那次确认不该算到这个工具头上。"""
    wc.start_turn()
    wc.add_wait(30)                        # 上一个工具等过 30 秒
    before = wc.waited()
    wc.add_wait(5)                         # 本工具等了 5 秒
    assert wc.minus_wait(8.0, before) == pytest.approx(3.0)


async def test_nested_task_wait_reaches_the_parent():
    """ContextVar 在派生任务里是拷贝——计数器必须装在可变对象里才回得来。"""
    wc.start_turn()

    async def child():
        wc.add_wait(2.0)

    await asyncio.gather(asyncio.create_task(child()), asyncio.create_task(child()))
    assert wc.waited() == pytest.approx(4.0)


# ---------------------------------------------------------------- 端到端
async def test_gate_records_the_time_it_spends_asking(tmp_path):
    """确认门是全仓唯一一处"真的在等人"的 await，等待就该记在那里。"""
    from src.agents.gate import make_confirm_gate

    async def slow_human(_message):
        await asyncio.sleep(0.05)
        return True

    wc.start_turn()
    gate = make_confirm_gate(slow_human, can_ask_human=True)
    assert await gate("跑命令？") is True
    assert wc.waited() >= 0.05


async def test_tool_duration_excludes_the_confirmation_wait(monkeypatch, tmp_path):
    """真正要的结论：工具耗时里不含用户犹豫的那段。"""
    monkeypatch.chdir(tmp_path)
    from src.agents.gate import make_confirm_gate
    from src.agents.tool import Tool
    from src.web.routers.realtime import _new_agent

    async def slow_human(_message, _kind=None):
        await asyncio.sleep(0.12)          # 用户犹豫
        return True

    agent = _new_agent()
    gate = make_confirm_gate(slow_human, can_ask_human=True)
    events = []
    agent._emit_tool_event = lambda stage, payload: events.append((stage, payload))

    async def handler(_args):
        from src.agents.gate import request
        await request(gate, "要不要？", "write")
        return "好了"

    wc.start_turn()
    await agent._run_bound_tool(Tool("probe", "", {}, handler, read_only=False), {}, "build",
                                lambda _m: None)
    finish = [p for stage, p in events if stage == "finish"][0]
    assert finish["duration_ms"] < 100, f"等待被算进了耗时：{finish['duration_ms']}ms"
