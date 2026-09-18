"""监护进程看门狗——Desktop 被强杀后 runtime 不该继续跑。

背景（2026-09-17 真机诊断）：正常退出走 Tauri 的 RunEvent::Exit → 终止 runtime 进程组，这条是通的；
但强杀/崩溃时那条回调根本不执行，留下的 runtime 继续占端口、持着能调模型的 agent，界面上再无入口。
"""

import asyncio
import os

import pytest

from src.gateway import supervisor_watchdog as wd


def test_no_env_means_no_watchdog(monkeypatch):
    """`vc server` 手工起 / systemd 托管 / CI：生命周期不归 Desktop 管，不启动看门狗。"""
    monkeypatch.delenv(wd.ENV_VAR, raising=False)
    assert wd.supervisor_pid() is None


@pytest.mark.parametrize("raw", ["", "  ", "abc", "0", "1", "-3", "12.5"])
def test_unusable_pid_is_ignored(monkeypatch, raw):
    monkeypatch.setenv(wd.ENV_VAR, raw)
    assert wd.supervisor_pid() is None


def test_own_pid_is_ignored(monkeypatch):
    """自己监护自己 = 永不触发，等于没装；宁可明确返回 None。"""
    monkeypatch.setenv(wd.ENV_VAR, str(os.getpid()))
    assert wd.supervisor_pid() is None


def test_reads_a_real_pid(monkeypatch):
    monkeypatch.setenv(wd.ENV_VAR, " 4242 ")
    assert wd.supervisor_pid() == 4242


def test_process_alive_on_self_and_missing():
    assert wd.process_alive(os.getpid()) is True
    assert wd.process_alive(2 ** 22) is False        # 几乎不可能存在的 pid


def test_permission_error_counts_as_alive(monkeypatch):
    """别的用户跑的进程：探不到不等于没了——误杀正在跑的活比多活一会儿更糟。"""
    monkeypatch.setattr(wd.os, "kill", lambda *_: (_ for _ in ()).throw(PermissionError()))
    assert wd.process_alive(4242) is True


async def test_watch_fires_once_the_supervisor_disappears():
    alive = {"value": True}
    gone = asyncio.Event()

    def _alive(_pid):
        return alive["value"]

    original = wd.process_alive
    wd.process_alive = _alive
    try:
        task = asyncio.create_task(
            wd.watch_supervisor(4242, interval=0.01, on_gone=gone.set))
        await asyncio.sleep(0.05)
        assert not gone.is_set()                     # 宿主还在 → 不动
        alive["value"] = False
        await asyncio.wait_for(gone.wait(), 1)
        await asyncio.wait_for(task, 1)
    finally:
        wd.process_alive = original


async def test_start_watchdog_is_a_noop_without_the_env(monkeypatch):
    monkeypatch.delenv(wd.ENV_VAR, raising=False)
    assert wd.start_watchdog() is None


async def test_start_watchdog_schedules_a_task(monkeypatch):
    monkeypatch.setenv(wd.ENV_VAR, str(os.getppid() or 1))
    monkeypatch.setattr(wd, "supervisor_pid", lambda env=None: 4242)
    task = wd.start_watchdog(interval=60)
    assert task is not None
    task.cancel()
