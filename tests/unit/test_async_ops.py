"""Cancellation must retain thread, Git lock, worktree and task ownership."""
import asyncio
import shutil
import threading
from types import SimpleNamespace

import pytest

from src.agents import worktree
from src.agents.tools.files import build_test_tool
from src.gateway.tasks import TaskRunner
from src.utils.async_ops import await_thread, cancel_requested, request_cancel, run_with_timeout


async def wait_started(event):
    async def poll():
        while not event.is_set():
            await asyncio.sleep(.001)
    await run_with_timeout(poll(), 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_thread_result_and_failure_propagate(fail):
    def operation(value):
        if fail:
            raise ValueError("thread failed")
        return value

    if fail:
        with pytest.raises(ValueError, match="thread failed"):
            await await_thread(operation, 42)
    else:
        assert await await_thread(operation, 42) == 42


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_cancel", [False, True])
@pytest.mark.parametrize("fail", [False, True])
async def test_cancellation_waits_for_thread_and_late_failure(repeat_cancel, fail):
    started, release, stopped = threading.Event(), threading.Event(), threading.Event()
    cleaned = []

    def operation():
        started.set()
        try:
            assert release.wait(3)
            if fail:
                raise ValueError("late failure")
            return "unused result"
        finally:
            stopped.set()

    async def caller():
        try:
            return await await_thread(operation)
        finally:
            cleaned.append(stopped.is_set())

    task = asyncio.create_task(caller())
    try:
        await wait_started(started)
        task.cancel()
        await asyncio.sleep(.01)
        if repeat_cancel:
            task.cancel()
            await asyncio.sleep(.01)
        assert not task.done() and cleaned == [] and not stopped.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cleaned == [True]


@pytest.mark.asyncio
async def test_cancelled_git_operation_retains_lock(monkeypatch):
    lock = asyncio.Lock()
    monkeypatch.setattr(worktree, "_GIT_LOCK", lock)
    started, release, next_started = threading.Event(), threading.Event(), threading.Event()

    def first_operation():
        started.set()
        assert release.wait(3)

    first = asyncio.create_task(worktree._git_op(first_operation))
    second = None
    try:
        await wait_started(started)
        first.cancel()
        second = asyncio.create_task(worktree._git_op(next_started.set))
        await asyncio.sleep(.02)
        assert lock.locked() and not next_started.is_set() and not first.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        if second is not None:
            await second
    assert next_started.is_set() and not lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["in_worktree", "isolated", "dependent"])
async def test_cancel_during_worktree_creation_cleans_after_creation(tmp_path, monkeypatch, entry):
    monkeypatch.setattr(worktree, "_GIT_LOCK", asyncio.Lock())
    path = worktree._worktrees_dir(tmp_path) / "wt-cancel-add"
    started, release, stopped = threading.Event(), threading.Event(), threading.Event()
    removed = []

    def create(*args, **kwargs):
        path.mkdir(parents=True)
        started.set()
        assert release.wait(3)
        stopped.set()
        return path if entry != "dependent" else SimpleNamespace(returncode=0)

    def remove(*args):
        assert stopped.is_set(), "worktree was removed while its creator was running"
        shutil.rmtree(path)
        removed.append(path)

    async def must_not_run(_path):
        raise AssertionError("cancelled creation cannot start implementation")

    def must_not_build(_path):
        raise AssertionError("cancelled creation cannot build an Agent")

    monkeypatch.setattr(worktree, "add_worktree", create)
    monkeypatch.setattr(worktree, "_git", create)
    monkeypatch.setattr(worktree, "remove_worktree", remove)
    operation = (worktree.in_worktree(tmp_path, path.name, must_not_run) if entry == "in_worktree"
                 else worktree.run_isolated_task(tmp_path, path.name, "fixture", must_not_build) if entry == "isolated"
                 else worktree.run_dependent_on_branch(tmp_path, path.name, "vorto/test", "fixture", must_not_build, "test"))
    task = asyncio.create_task(operation)
    try:
        await wait_started(started)
        task.cancel()
        await asyncio.sleep(.01)
        assert path.exists() and removed == [] and not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert removed == [path] and not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["isolated", "dependent", "tool"])
async def test_cancel_during_tests_waits_before_worktree_cleanup(tmp_path, monkeypatch, entry):
    monkeypatch.setattr(worktree, "_GIT_LOCK", asyncio.Lock())
    path = worktree._worktrees_dir(tmp_path) / "wt-cancel-tests"
    started, release, stopped = threading.Event(), threading.Event(), threading.Event()
    removed, commits = [], []

    def add(*args, **kwargs):
        path.mkdir(parents=True)
        return path

    def git(_root, *args, **kwargs):
        if args[:2] == ("worktree", "add"):
            add()
        if args[0] == "commit":
            commits.append(args)
        return SimpleNamespace(returncode=0, stdout=" M app.py\n", stderr="")

    def run_tests(*args):
        started.set()
        assert release.wait(3)
        assert path.exists(), "tests lost their worktree while still running"
        stopped.set()
        return {"ok": True, "output": "passed", "cmd": "fixture"}

    def remove(*args):
        assert stopped.is_set()
        shutil.rmtree(path)
        removed.append(path)

    class Agent:
        async def run_turn(self, *args, **kwargs):
            return "fixture"

    monkeypatch.setattr(worktree, "add_worktree", add)
    monkeypatch.setattr(worktree, "_git", git)
    monkeypatch.setattr(worktree, "collect_diff", lambda _: "diff --git a/app.py b/app.py\n+VALUE=1\n")
    monkeypatch.setattr(worktree, "run_tests", run_tests)
    monkeypatch.setattr(worktree, "remove_worktree", remove)
    if entry == "tool":
        add()
        operation = build_test_tool(str(path), ["fixture"]).handler({})
    elif entry == "isolated":
        operation = worktree.run_isolated_task(tmp_path, path.name, "fixture", lambda _: Agent(), test_cmd=["fixture"])
    else:
        operation = worktree.run_dependent_on_branch(tmp_path, path.name, "vorto/test", "fixture", lambda _: Agent(), "test", ["fixture"])
    task = asyncio.create_task(operation)
    try:
        await wait_started(started)
        task.cancel()
        await asyncio.sleep(.01)
        assert path.exists() and removed == [] and not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert commits == [] and stopped.is_set()
    assert removed == ([] if entry == "tool" else [path])


@pytest.mark.asyncio
async def test_task_pause_keeps_slot_until_thread_has_stopped(tmp_path):
    started, release, stopped = threading.Event(), threading.Event(), threading.Event()
    updates, next_steps = [], []

    def operation():
        started.set()
        assert release.wait(3)
        stopped.set()

    async def worker(task, progress):
        await await_thread(operation)
        next_steps.append("must not execute after pause")

    runner = TaskRunner(str(tmp_path), worker, on_update=lambda task: updates.append(task.status))
    task = await runner.submit("fixture")
    paused = None
    try:
        await wait_started(started)
        paused = asyncio.create_task(runner.pause(task.id))
        await asyncio.sleep(.01)
        assert runner.active_count == 1 and runner.get(task.id).status == "running"
        assert not paused.done() and "paused" not in updates
    finally:
        release.set()
        if paused is not None:
            result = await paused
            assert result.status == "paused"
    assert stopped.is_set() and runner.active_count == 0 and next_steps == []


# ---- 3.10 兼容垫片：asyncio.timeout / Task.cancelling 是 3.11+，两个版本都得给出同一语义。
@pytest.mark.asyncio
async def test_run_with_timeout_returns_result_and_raises_builtin_timeout():
    async def value():
        return 7

    assert await run_with_timeout(value(), 1) == 7
    with pytest.raises(TimeoutError):   # 3.10 的 asyncio.TimeoutError 也要统一成内置类
        await run_with_timeout(asyncio.Event().wait(), 0.01)


@pytest.mark.asyncio
async def test_cancel_request_stays_visible_after_cancellation_is_swallowed():
    swallowed, release = asyncio.Event(), asyncio.Event()

    async def stubborn():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            swallowed.set()
        await release.wait()

    assert not cancel_requested(None)
    task = asyncio.create_task(stubborn())
    await asyncio.sleep(0)
    assert not cancel_requested(task)
    request_cancel(task)
    await swallowed.wait()
    assert cancel_requested(task)   # 被吞掉的取消仍算"已请求"，调用方据此不重复取消
    release.set()
    await task
