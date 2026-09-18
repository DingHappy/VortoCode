"""打包版解释器解析（src/utils/python_exe.py）—— 回归：Desktop 上隔离流水线跑不了 pytest。

真机现象（Desktop dev 流水线，2026-09-17）：子 agent 写出了改动，但自测一律失败，输出是
`vortocode-runtime: error: argument command: invalid choice: 'pytest' (choose from server)`
——PyInstaller 单文件里 `sys.executable` 是 runtime 自己，不是 Python 解释器。
"""

import sys

import pytest

from src.agents.test_detect import detect_test_cmd
from src.utils.python_exe import pytest_argv, python_executable


def test_source_run_uses_current_interpreter(monkeypatch):
    monkeypatch.delenv("VORTOCODE_PYTHON", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert python_executable() == sys.executable


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("VORTOCODE_PYTHON", "/opt/py/bin/python3.12")
    assert python_executable() == "/opt/py/bin/python3.12"


def test_frozen_never_returns_the_bundle(monkeypatch):
    """打包版：绝不能回 sys.executable，否则 pytest 参数会被 runtime 的 argparse 接住。"""
    monkeypatch.delenv("VORTOCODE_PYTHON", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/VortoCode.app/vortocode-runtime",
                        raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/python3" if name == "python3" else None)
    assert python_executable() == "/usr/bin/python3"


def test_frozen_without_any_python_falls_back_to_a_readable_failure(monkeypatch):
    monkeypatch.delenv("VORTOCODE_PYTHON", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/VortoCode.app/vortocode-runtime",
                        raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert python_executable() == "python3"


@pytest.mark.parametrize("marker", ["pyproject.toml", "conftest.py"])
def test_detected_pytest_cmd_is_not_the_bundle_when_frozen(tmp_path, monkeypatch, marker):
    """探测出的测试命令在打包版里必须指向真解释器（这条在修复前会红）。"""
    (tmp_path / marker).write_text("", encoding="utf-8")
    bundle = "/Applications/VortoCode.app/Contents/MacOS/vortocode-runtime"
    monkeypatch.delenv("VORTOCODE_PYTHON", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", bundle, raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/python3" if name == "python3" else None)
    cmd = detect_test_cmd(str(tmp_path))
    assert cmd[0] == "/usr/bin/python3"
    assert bundle not in cmd
    assert cmd[1:3] == ["-m", "pytest"]


def test_pytest_argv_appends_args(monkeypatch):
    monkeypatch.setenv("VORTOCODE_PYTHON", "/usr/bin/python3")
    assert pytest_argv("-q", "tests/x.py") == ["/usr/bin/python3", "-m", "pytest", "-q", "tests/x.py"]
