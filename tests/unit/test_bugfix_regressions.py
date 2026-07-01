"""核心 bug 修复的回归测试（B2/B3/B5/B6）。"""

import asyncio

import pytest


def test_b2_hook_registry_load_no_importerror(tmp_path):
    """B2: load_from_config 曾从 .hook 误导入而必崩；现应安全。"""
    from src.hooks.registry import HookRegistry
    # 配置不存在时直接返回，且导入路径正确，不抛 ImportError
    HookRegistry().load_from_config(str(tmp_path / "nope.yaml"))


@pytest.mark.asyncio
async def test_b3_decomposition_is_chained():
    """B3: 子任务依赖应为链式（只指向上一个），而非依赖之前全部。"""
    from src.orchestrator.task_analyzer import (
        TaskDecomposer, TaskAnalysis, TaskComplexity,
    )
    dec = TaskDecomposer(use_llm=False)   # 验证规则版链式分解
    analysis = TaskAnalysis(
        complexity=TaskComplexity.COMPLEX,
        required_capabilities=["architecture", "code_generation", "testing", "code_review"],
    )
    subs = await dec.decompose("build a thing", analysis)
    assert len(subs) == 4
    assert subs[0].dependencies == []
    for prev, cur in zip(subs, subs[1:]):
        assert cur.dependencies == [prev.id]   # 每个只依赖紧邻的上一个


@pytest.mark.asyncio
async def test_b5_taskqueue_stop_does_not_hang():
    """B5: 队列非空时 stop() 不应死锁（先 join 再停）。"""
    from src.core.task_queue import TaskQueue, Task
    q = TaskQueue(max_workers=2)
    done = []
    q.register_handler("noop", lambda: done.append(1))
    await q.start()
    for _ in range(5):
        await q.submit(Task(name="t", func="noop"))
    # 修复前这里会永久阻塞；要求 5 秒内完成并处理完所有任务
    await asyncio.wait_for(q.stop(), timeout=5)
    assert len(done) == 5
