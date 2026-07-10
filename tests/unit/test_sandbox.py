"""OS sandbox policy, Seatbelt/bubblewrap argv, and fail-closed execution tests."""
import os
import shutil  # noqa: F401 - referenced by the string-form skipif below

import pytest

from src.agents import sandbox as sb


def test_profile_restricts_writes_to_repo_and_temp(tmp_path):
    prof = sb.build_sandbox_profile(str(tmp_path))
    assert "(version 1)" in prof
    assert "(allow default)" in prof
    assert "(deny file-write*)" in prof
    real = os.path.realpath(str(tmp_path))
    assert f'(subpath "{real}")' in prof
    assert '(subpath "/private/tmp")' in prof
    assert '(literal "/dev/null")' in prof


def test_profile_realpaths_repo(tmp_path):
    (tmp_path / "sub").mkdir()
    weird = str(tmp_path / "sub" / "..")
    prof = sb.build_sandbox_profile(weird)
    assert f'(subpath "{os.path.realpath(weird)}")' in prof


def test_seatbelt_argv_shape(tmp_path):
    argv = sb.sandboxed_argv(str(tmp_path), "echo hi", backend="seatbelt")
    assert argv[0] == "sandbox-exec" and argv[1] == "-p"
    assert argv[-3:] == ["/bin/sh", "-c", "echo hi"]
    assert "(deny file-write*)" in argv[2]


def test_bubblewrap_argv_shape(tmp_path):
    argv = sb.sandboxed_exec_argv(
        str(tmp_path), ["python", "-m", "pytest", "-q"], backend="bubblewrap")
    assert argv[0] == "bwrap"
    assert ["--ro-bind", "/", "/"] == argv[3:6]
    assert "--bind" in argv and os.path.realpath(str(tmp_path)) in argv
    assert argv[-4:] == ["python", "-m", "pytest", "-q"]


def test_policy_defaults_and_legacy_aliases(monkeypatch):
    monkeypatch.delenv("VORTOCODE_SANDBOX", raising=False)
    assert sb.sandbox_policy() == "auto"
    for value in ("1", "true", "yes", "on", "required"):
        monkeypatch.setenv("VORTOCODE_SANDBOX", value)
        assert sb.sandbox_policy() == "required"
    for value in ("0", "false", "no", "off"):
        monkeypatch.setenv("VORTOCODE_SANDBOX", value)
        assert sb.sandbox_policy() == "off"
    monkeypatch.setenv("VORTOCODE_SANDBOX", "sometimes")
    assert sb.sandbox_policy() == "invalid"


def test_backend_detection_mac_linux_and_unsupported(monkeypatch):
    monkeypatch.setattr(sb, "_probe_backend", lambda _backend, _binary: True)
    monkeypatch.setattr(sb.sys, "platform", "darwin")
    monkeypatch.setattr(sb.shutil, "which", lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None)
    assert sb.sandbox_backend() == "seatbelt"

    monkeypatch.setattr(sb.sys, "platform", "linux")
    monkeypatch.setattr(sb.shutil, "which", lambda name: "/usr/bin/bwrap" if name == "bwrap" else None)
    assert sb.sandbox_backend() == "bubblewrap"

    monkeypatch.setattr(sb.sys, "platform", "win32")
    assert sb.sandbox_backend() == ""


def test_backend_binary_must_be_runnable(monkeypatch):
    monkeypatch.setattr(sb.sys, "platform", "darwin")
    monkeypatch.setattr(sb.shutil, "which", lambda _name: "/usr/bin/sandbox-exec")
    monkeypatch.setattr(sb, "_probe_backend", lambda _backend, _binary: False)
    assert sb.sandbox_backend() == ""


def test_auto_decision_uses_backend_or_audited_interactive_fallback(monkeypatch):
    monkeypatch.delenv("VORTOCODE_SANDBOX", raising=False)
    monkeypatch.setattr(sb, "sandbox_backend", lambda: "seatbelt")
    isolated = sb.resolve_sandbox()
    assert isolated.allowed and isolated.isolated and isolated.backend == "seatbelt"

    monkeypatch.setattr(sb, "sandbox_backend", lambda: "")
    fallback = sb.resolve_sandbox()
    assert fallback.allowed and not fallback.isolated and fallback.fallback
    assert "显式降级" in fallback.reason


def test_unattended_auto_and_required_fail_closed_without_backend(monkeypatch):
    monkeypatch.setattr(sb, "sandbox_backend", lambda: "")
    monkeypatch.delenv("VORTOCODE_SANDBOX", raising=False)
    auto = sb.resolve_sandbox(require_isolation=True)
    assert not auto.allowed and "无人值守" in auto.reason

    monkeypatch.setenv("VORTOCODE_SANDBOX", "required")
    required = sb.resolve_sandbox()
    assert not required.allowed and "策略要求隔离" in required.reason


def test_explicit_off_is_only_unattended_host_escape_hatch(monkeypatch):
    monkeypatch.setenv("VORTOCODE_SANDBOX", "off")
    monkeypatch.setattr(sb, "sandbox_backend", lambda: "")
    decision = sb.resolve_sandbox(require_isolation=True)
    assert decision.allowed and not decision.isolated and not decision.fallback
    assert "显式关闭" in decision.reason


def test_invalid_policy_fails_closed(monkeypatch):
    monkeypatch.setenv("VORTOCODE_SANDBOX", "maybe")
    decision = sb.resolve_sandbox()
    assert not decision.allowed and decision.policy == "invalid"
    assert "仅支持 auto / required / off" in decision.reason


def test_run_command_auto_fallback_is_visible(monkeypatch, tmp_path):
    monkeypatch.delenv("VORTOCODE_SANDBOX", raising=False)
    monkeypatch.setattr(sb, "sandbox_backend", lambda: "")
    from src.agents.shell import run_command
    res = run_command(str(tmp_path), "echo audited-host")
    assert res["ok"] and "audited-host" in res["output"]
    assert res["sandbox"]["fallback"] is True
    assert "显式降级" in res["warning"]


def test_run_command_unattended_refuses_without_backend(monkeypatch, tmp_path):
    monkeypatch.delenv("VORTOCODE_SANDBOX", raising=False)
    monkeypatch.setattr(sb, "sandbox_backend", lambda: "")
    from src.agents.shell import run_command
    res = run_command(str(tmp_path), "echo must-not-run", require_isolation=True)
    assert not res["ok"] and res["code"] == -1
    assert "无人值守" in res["output"]


@pytest.mark.skipif("sys.platform != 'darwin' or shutil.which('sandbox-exec') is None",
                    reason="real write boundary requires macOS sandbox-exec")
def test_sandbox_blocks_write_outside_repo_allows_inside(monkeypatch, tmp_path):
    monkeypatch.setenv("VORTOCODE_SANDBOX", "required")
    from src.agents.shell import run_command
    inside = tmp_path / "ok.txt"
    res2 = run_command(str(tmp_path), f"echo y > '{inside}' && echo DONE")
    if not (res2["ok"] and inside.exists()):
        pytest.skip(f"此环境 sandbox-exec 不放行仓库内写：{(res2.get('output') or '')[:160]}")

    marker = os.path.expanduser("~/.vorto_sbx_test_marker")
    try:
        res = run_command(str(tmp_path), f"echo x > {marker} && echo WROTE")
        assert not res["ok"]
        assert not os.path.exists(marker)
    finally:
        if os.path.exists(marker):
            os.remove(marker)
