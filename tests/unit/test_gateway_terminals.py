"""Interactive PTY behavior used by Desktop."""
import os
import subprocess
import time

import pytest

from src.gateway.terminals import TerminalManager


pytestmark = pytest.mark.skipif(os.name != "posix", reason="PTY is POSIX-only")


def test_terminal_accepts_input_streams_output_and_resizes(tmp_path):
    manager = TerminalManager(str(tmp_path))
    created = manager.create(cols=90, rows=24)
    terminal_id = created["id"]
    offset = created["offset"]

    manager.write(terminal_id, "printf '__VORTOCODE_PTY_OK__\\n'\n")
    output = ""
    for _ in range(100):
        snapshot = manager.read(terminal_id, offset)
        assert snapshot is not None
        output += snapshot["output"]
        offset = snapshot["offset"]
        if "__VORTOCODE_PTY_OK__" in output:
            break
        time.sleep(0.02)

    assert "__VORTOCODE_PTY_OK__" in output
    resized = manager.resize(terminal_id, 120, 40)
    assert resized is not None and resized["cols"] == 120 and resized["rows"] == 40

    stopped = manager.stop(terminal_id)
    assert stopped is not None and stopped["status"] == "exited"
    manager.shutdown()


def test_terminal_stop_reaps_process_ignoring_sigterm(tmp_path):
    """进程 trap 掉 SIGTERM，逼 stop() 走 SIGKILL 分支：SIGKILL 后必须 wait 回收，否则紧接的
    snapshot() poll() 竞态读到 None、status 误报 "running"（且留僵尸）。

    诚实注记：竞态窗口只在 Linux 容器够宽可复现（CI 上原用例正因此红）；macOS reap 极快、
    窗口几乎为零，**本地跑此钉恒绿=smoke，真复现与验证靠 99 的 Linux CI**。留它是为了：①
    在 CI 钉住 SIGKILL 后必 wait 的修复（删了 wait，CI 会红）；② 本地至少 smoke 掉 trap
    场景不崩。想在任何平台确定验证 wait 逻辑，得注入受控假进程复现 poll 滞后（另做）。"""
    manager = TerminalManager(str(tmp_path))
    created = manager.create(cols=80, rows=24)
    terminal_id = created["id"]
    offset = created["offset"]
    manager.write(terminal_id, "trap '' TERM; echo __TRAPPED__\n")
    got = ""
    for _ in range(100):
        snapshot = manager.read(terminal_id, offset)
        assert snapshot is not None
        got += snapshot["output"]
        offset = snapshot["offset"]
        if "__TRAPPED__" in got:
            break
        time.sleep(0.02)
    assert "__TRAPPED__" in got            # trap 已生效：SIGTERM 会被吞，逼出 SIGKILL 路径
    stopped = manager.stop(terminal_id)    # SIGTERM 超时 → SIGKILL + wait → 必已回收
    assert stopped is not None and stopped["status"] == "exited"
    manager.shutdown()


def test_terminal_stop_waits_after_sigkill_deterministic(tmp_path, monkeypatch):
    """任何平台确定验证 SIGKILL 后必 wait 回收（补上 trap 钉的本地 smoke 缺口）：注入受控假进程
    复现 Linux 的 poll 滞后——SIGTERM 的 wait 超时逼出 SIGKILL，假进程 poll() 在 wait() 前恒
    None、wait() 后才回收。删掉修复里的 SIGKILL-wait，本用例在任何机器都会红。"""
    manager = TerminalManager(str(tmp_path))
    terminal_id = manager.create(cols=80, rows=24)["id"]
    proc = manager.get(terminal_id)
    proc.stop()                       # 先真回收 create 起的 shell，避免泄漏，再植入受控假进程

    class _RacyProc:
        pid = 424242

        def __init__(self):
            self._waits = 0
            self._dead = False

        def poll(self):
            return -9 if self._dead else None

        def wait(self, timeout=None):
            self._waits += 1
            if self._waits == 1:
                raise subprocess.TimeoutExpired("sh", timeout)   # SIGTERM 超时 → 逼出 SIGKILL
            self._dead = True                                    # SIGKILL 后 wait 才真回收
            return -9

    proc.process = _RacyProc()
    proc._closed = False
    monkeypatch.setattr(os, "killpg", lambda *args: None)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(os, "close", lambda fd: None)

    stopped = manager.stop(terminal_id)
    assert stopped is not None and stopped["status"] == "exited"
    manager.shutdown()


def test_terminal_ids_and_limits_fail_closed(tmp_path):
    manager = TerminalManager(str(tmp_path))
    assert manager.get("../../bad") is None
    assert manager.read("term-missing") is None
    created = manager.create(cols=1, rows=999)
    assert created["cols"] == 20 and created["rows"] == 200
    manager.shutdown()
