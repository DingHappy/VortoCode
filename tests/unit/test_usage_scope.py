"""阶段级 token 计量（模型分层的地基）。

"规划用旗舰、执行用中档"这类分层，得先知道钱花在哪个阶段。进程级/会话级累计答不了，
要的是 decompose / execute 各自的占比。**先量后动**——2026-08-03 门禁提速那轮刚验证过：
不量就动手会把力气使在错的地方（当时以为要上并行，实测发现一条测试占了全套 20%）。
"""

import asyncio

import pytest

from src.llm.client import add_usage, bind_usage, get_usage, new_usage, usage_scope


@pytest.fixture(autouse=True)
def _isolated():
    """每条用例自带作用域，别写进进程级全局。"""
    bind_usage(new_usage())


def test_scope_captures_only_its_own_span():
    add_usage(10, 5, model="outer")
    with usage_scope() as inner:
        add_usage(100, 50, model="inner")
    add_usage(1, 1, model="outer")

    assert inner["total_tokens"] == 150
    assert set(inner["by_model"]) == {"inner"}, "作用域外的调用漏进来了"


def test_outer_binding_is_restored_and_keeps_its_own_total():
    add_usage(10, 5, model="a")
    with usage_scope():
        add_usage(999, 999, model="b")
    add_usage(2, 3, model="a")

    outer = get_usage()
    assert outer["total_tokens"] == 20, "作用域内的用量算到外层头上了"
    assert "b" not in outer["by_model"]


def test_restores_even_when_body_raises():
    """阶段里抛异常（dev 流水线里很常见）也必须还原绑定，否则后续计量全乱。"""
    with pytest.raises(RuntimeError):
        with usage_scope():
            add_usage(50, 50, model="x")
            raise RuntimeError("阶段失败")
    add_usage(1, 1, model="y")
    assert get_usage()["total_tokens"] == 2


def test_nested_scopes_do_not_leak():
    with usage_scope() as outer:
        add_usage(10, 0, model="o")
        with usage_scope() as inner:
            add_usage(100, 0, model="i")
        add_usage(5, 0, model="o")
    assert inner["total_tokens"] == 100
    assert outer["total_tokens"] == 15


def test_stage_log_reports_real_per_model_numbers(caplog):
    """日志里的分模型数字必须是真的。

    第一版直接读 `by_model[m]["total_tokens"]`——那个键**根本不存在**（桶里只有
    prompt/completion），于是每个模型都打成 0。冒烟时把日志真打出来看了一眼才发现：
    **日志能产出 ≠ 日志是对的**。
    """
    import logging

    from src.agents.main_agent import _log_stage_usage

    with usage_scope() as u:
        add_usage(1200, 300, model="flagship")
        add_usage(400, 900, model="cheap")
    with caplog.at_level(logging.INFO, logger="vortocode.dev.usage"):
        _log_stage_usage("decompose", u)
    line = caplog.text
    assert "2800 tokens" in line and "2 次调用" in line
    assert "flagship=1500" in line and "cheap=1300" in line, f"分模型数字不对: {line}"


def test_stage_log_stays_quiet_on_zero_usage(caplog):
    """没花 token 的阶段不刷日志——噪音也是成本。"""
    import logging

    from src.agents.main_agent import _log_stage_usage

    with caplog.at_level(logging.INFO, logger="vortocode.dev.usage"):
        _log_stage_usage("execute", new_usage())
    assert caplog.text.strip() == ""


@pytest.mark.asyncio
async def test_parallel_children_are_counted():
    """**这条是能不能用的关键**：dev 流水线并行实现多个块，子任务的用量必须计进来。

    成立的原因是绑定的是**可变 dict**：create_task 拷贝 context 时子任务拿到同一个
    dict 引用，add_usage 原地累加 → 父作用域看得见。（"子任务里 set() 父任务看不见"
    那条 contextvars 陷阱在这里不成立，因为我们从不在子任务里 set。）
    """
    async def worker(n):
        await asyncio.sleep(0)
        add_usage(n, 0, model="child")

    with usage_scope() as scope:
        await asyncio.gather(*(worker(i) for i in (10, 20, 30)))

    assert scope["total_tokens"] == 60, "并行子任务的用量没被计进阶段——分层就无从谈起"
    assert scope["calls"] == 3
