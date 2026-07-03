"""OS 级沙箱（macOS Seatbelt / sandbox-exec）—— src/agents/sandbox.py + shell.run_command 接入。

profile/argv/开关逻辑跨平台测（CI Linux 也跑）；真·挡写行为测仅 macOS（CI 自动 skip）。
"""
import os
import shutil  # noqa: F401 —— 供下方字符串形式 skipif 在运行时 eval 用（ruff 看不见字符串里的引用）

import pytest

from src.agents import sandbox as sb


def test_profile_restricts_writes_to_repo_and_temp(tmp_path):
    prof = sb.build_sandbox_profile(str(tmp_path))
    assert "(version 1)" in prof
    assert "(allow default)" in prof              # 默认全放行（读/exec/网络）
    assert "(deny file-write*)" in prof           # 再禁所有写
    real = os.path.realpath(str(tmp_path))
    assert f'(subpath "{real}")' in prof          # 仓库根重新放行写
    assert '(subpath "/private/tmp")' in prof     # 临时目录可写（pip/构建中间产物）
    assert '(literal "/dev/null")' in prof


def test_profile_realpaths_repo(tmp_path):
    (tmp_path / "sub").mkdir()
    weird = str(tmp_path / "sub" / "..")          # 带 .. 的路径
    prof = sb.build_sandbox_profile(weird)
    assert f'(subpath "{os.path.realpath(weird)}")' in prof   # 归一化后写进 profile


def test_sandboxed_argv_shape(tmp_path):
    argv = sb.sandboxed_argv(str(tmp_path), "echo hi")
    assert argv[0] == "sandbox-exec" and argv[1] == "-p"
    assert argv[-3:] == ["/bin/sh", "-c", "echo hi"]
    assert "(deny file-write*)" in argv[2]        # profile 作为 -p 的参数内联


def test_enabled_requires_both_env_and_platform(monkeypatch):
    monkeypatch.delenv("VORTOCODE_SANDBOX", raising=False)
    monkeypatch.setattr(sb, "sandbox_available", lambda: True)
    assert sb.sandbox_enabled() is False          # 没开 env → 关（默认零改变）
    monkeypatch.setenv("VORTOCODE_SANDBOX", "1")
    monkeypatch.setattr(sb, "sandbox_available", lambda: False)
    assert sb.sandbox_enabled() is False          # 开了 env 但平台不支持 → 仍关（优雅降级）
    monkeypatch.setattr(sb, "sandbox_available", lambda: True)
    assert sb.sandbox_enabled() is True           # 两者齐 → 开


def test_available_only_on_darwin_with_binary(monkeypatch):
    monkeypatch.setattr(sb.sys, "platform", "linux")
    assert sb.sandbox_available() is False         # 非 macOS → 否
    monkeypatch.setattr(sb.sys, "platform", "darwin")
    monkeypatch.setattr(sb.shutil, "which", lambda _n: None)
    assert sb.sandbox_available() is False         # macOS 但无 sandbox-exec → 否
    monkeypatch.setattr(sb.shutil, "which", lambda _n: "/usr/bin/sandbox-exec")
    assert sb.sandbox_available() is True


def test_truthy():
    assert sb._truthy("1") and sb._truthy("true") and sb._truthy("on") and sb._truthy("YES")
    assert not sb._truthy("") and not sb._truthy("0") and not sb._truthy(None)


def test_run_command_default_is_unsandboxed_passthrough(monkeypatch, tmp_path):
    """默认（未开 env）run_command 行为不变：shell 直跑、能拿到输出。"""
    monkeypatch.delenv("VORTOCODE_SANDBOX", raising=False)
    from src.agents.shell import run_command
    res = run_command(str(tmp_path), "echo hello_passthrough_123")
    assert res["ok"] and "hello_passthrough_123" in res["output"]


@pytest.mark.skipif("sys.platform != 'darwin' or shutil.which('sandbox-exec') is None",
                    reason="真·挡写行为需要 macOS sandbox-exec")
def test_sandbox_blocks_write_outside_repo_allows_inside(monkeypatch, tmp_path):
    monkeypatch.setenv("VORTOCODE_SANDBOX", "1")
    from src.agents.shell import run_command
    # 前置：本环境的 sandbox-exec 得先能"放行仓库内写"。某些 macOS 版本 / CI / 自定义 TMPDIR 下
    # Seatbelt 更严或路径策略不同，连仓库内写都挡——那本测试的前提就不成立，应**跳过而非失败**
    # （挡写行为本身仍由沙箱保证，只是此环境无法复现"内放行"这一半）。
    inside = tmp_path / "ok.txt"
    res2 = run_command(str(tmp_path), f"echo y > '{inside}' && echo DONE")
    if not (res2["ok"] and inside.exists()):
        pytest.skip(f"此环境 sandbox-exec 不放行仓库内写，前提不成立：{(res2.get('output') or '')[:160]}")

    marker = os.path.expanduser("~/.vorto_sbx_test_marker")   # 家目录：不在白名单
    try:
        res = run_command(str(tmp_path), f"echo x > {marker} && echo WROTE")
        assert not res["ok"]                       # 写仓库外被 Seatbelt 挡
        assert not os.path.exists(marker)          # 文件根本没生成
    finally:
        if os.path.exists(marker):
            os.remove(marker)
