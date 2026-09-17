"""任务预算必须覆盖嵌套阶段、流式与子任务，并隔离外部并发用量。"""
import asyncio

import pytest

from src.llm.budget import BudgetedLLM, TokenBudgetExceeded
from src.llm.client import add_usage, reset_usage, usage_scope


class MeteredLLM:
    def __init__(self):
        self.calls = 0

    async def chat(self, *args, **kwargs):
        self.calls += 1
        await asyncio.sleep(0)
        add_usage(6, 4, model="budget-fixture")
        return {"content": "ok"}

    async def stream(self, *args, **kwargs):
        self.calls += 1
        yield {"content": "ok"}
        add_usage(6, 4, model="budget-fixture")


async def test_unrelated_concurrent_usage_cannot_spend_task_budget():
    guard = BudgetedLLM(MeteredLLM(), budget_tokens=20)
    ready, release = asyncio.Event(), asyncio.Event()

    async def task():
        with guard.scope():
            ready.set()
            await release.wait()
            await guard.chat([])

    pending = asyncio.create_task(task())
    await ready.wait()
    add_usage(10000, 10000, model="other-task")
    release.set()
    await pending
    assert guard.spent() == 10 and not guard.tripped


async def test_nested_scopes_and_parallel_children_count_without_double_billing():
    from src.models import cost_tracker
    guard = BudgetedLLM(MeteredLLM(), budget_tokens=20)
    before = len(cost_tracker.entries)

    async def child():
        with usage_scope():
            await guard.chat([])
            reset_usage()  # 会话/阶段清零不能重置预算

    with guard.scope():
        await asyncio.gather(child(), child())
    assert guard.spent() == 20
    assert len(cost_tracker.entries) - before == 2
    with pytest.raises(TokenBudgetExceeded):
        await guard.chat([])
    assert guard._inner.calls == 2


async def test_same_budget_concurrent_requests_cannot_both_pass_preflight():
    guard = BudgetedLLM(MeteredLLM(), budget_tokens=10)
    results = await asyncio.gather(guard.chat([]), guard.chat([]), return_exceptions=True)
    assert sum(isinstance(r, TokenBudgetExceeded) for r in results) == 1
    assert guard._inner.calls == 1 and guard.spent() == 10


async def test_stream_is_metered_at_consumption_and_shares_chat_budget():
    guard = BudgetedLLM(MeteredLLM(), budget_tokens=10)
    stream = guard.stream([])
    assert guard.spent() == 0
    assert [chunk async for chunk in stream] == [{"content": "ok"}]
    assert guard.spent() == 10
    with pytest.raises(TokenBudgetExceeded):
        await guard.chat([])


async def test_cancelled_stream_keeps_final_usage_and_releases_execution_lock():
    class StreamLLM(MeteredLLM):
        async def stream(self, *args, **kwargs):
            try:
                yield "partial"
            finally:
                add_usage(3, 2)

    guard = BudgetedLLM(StreamLLM(), budget_tokens=20)
    stream = guard.stream([])
    assert await anext(stream) == "partial"
    await stream.aclose()
    assert guard.spent() == 5
    await asyncio.wait_for(guard.chat([]), 1)
    assert guard.spent() == 15


async def test_single_call_overshoot_is_visible_even_without_another_call():
    guard = BudgetedLLM(MeteredLLM(), budget_tokens=5)
    await guard.chat([])
    assert guard.spent() == 10 and guard.tripped


async def test_stream_can_be_closed_from_another_task_without_context_token_error():
    guard = BudgetedLLM(MeteredLLM(), budget_tokens=20)
    stream = guard.stream([])
    await anext(stream)
    await asyncio.create_task(stream.aclose())
    await asyncio.wait_for(guard.chat([]), 1)
    assert guard.spent() == 10


async def test_cron_job_scope_counts_nested_work_and_isolates_other_jobs(tmp_path):
    from src.gateway.cron import CronJob, parse_schedule, run_job
    started, release = asyncio.Event(), asyncio.Event()
    guards = {}

    async def session(repo_root, prompt, *, mode, model, llm=None):
        guards[prompt] = llm
        if prompt == "A":
            started.set()
            await release.wait()
        with usage_scope():
            add_usage(6 if prompt == "A" else 60, 4 if prompt == "A" else 40)
        return "ok"

    first = asyncio.create_task(run_job(str(tmp_path), CronJob(
        name="A", schedule=parse_schedule("every 1h"), prompt="A", budget=20), run_session=session))
    await started.wait()
    await run_job(str(tmp_path), CronJob(
        name="B", schedule=parse_schedule("every 1h"), prompt="B", budget=200), run_session=session)
    release.set()
    await first
    assert (guards["A"].spent(), guards["B"].spent()) == (10, 100)
