"""CLI 参数解析测试（离线，不触发任何业务执行）。

只验证 argparse 层：无命令给总览、必填参数被强制、子命令能解析。
不调用 run_*（那些需 LLM/网络），所以这里只测“解析与分发前”的行为。
"""

import sys

import pytest

from src import cli


def test_no_command_prints_overview_and_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["auto-dev-crew"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "可用命令" in out
    assert "self-analyze" in out and "self-fix" in out      # 命令带说明
    assert "常用示例" in out                                  # 有示例


def test_self_fix_requires_paths(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["auto-dev-crew", "self-fix"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code != 0                                 # argparse 缺必填参数 -> 退出码 2
    err = capsys.readouterr().err
    assert "--paths" in err


def test_run_requires_task(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["auto-dev-crew", "run"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code != 0
    assert "--task" in capsys.readouterr().err


def test_help_is_per_command(monkeypatch, capsys):
    # 子命令 -h 只显示该命令自己的参数（不再是全局糊一脸）
    monkeypatch.setattr(sys, "argv", ["auto-dev-crew", "self-improve", "-h"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "--apply" in out and "--max-fixes" in out
    assert "--stage" not in out          # quant 的参数不该出现在 self-improve 帮助里
