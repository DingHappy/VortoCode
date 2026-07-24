"""统一沙箱 runner 的选择逻辑测试（离线，monkeypatch docker 可用性）。"""

import pytest

import src.sandbox.runner as runner
import src.sandbox.docker_sandbox as ds


@pytest.mark.asyncio
async def test_uses_docker_when_available(monkeypatch):
    monkeypatch.setattr(runner, "docker_available", lambda: True)

    class _FakeResult:
        success = True
        stdout = "hi"
        stderr = ""
        exit_code = 0

    class _FakeExec:
        def __init__(self, *a, **k):
            pass

        async def execute_in_sandbox(self, code, language="python", timeout=60):
            return _FakeResult()

    monkeypatch.setattr(ds, "SandboxExecutor", _FakeExec)

    r = await runner.run_code("print('hi')", "python")
    assert r.success is True
    assert r.runtime == "docker"
    assert r.isolated is True
    assert r.sandbox["backend"] == "docker" and r.sandbox["isolated"] is True


@pytest.mark.asyncio
async def test_refuses_without_docker_and_without_shell(monkeypatch):
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    monkeypatch.delenv("VORTOCODE_ENABLE_SHELL", raising=False)

    r = await runner.run_code("print('hi')", "python")
    assert r.success is False
    assert r.runtime == "none"
    assert r.isolated is False
    assert "VORTOCODE_ENABLE_SHELL" in r.error          # 明确告知如何开启


@pytest.mark.asyncio
async def test_host_fallback_when_shell_enabled(monkeypatch):
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    monkeypatch.setenv("VORTOCODE_ENABLE_SHELL", "1")

    r = await runner.run_code("print('it works')", "python")
    assert r.runtime == "host"
    assert r.isolated is False                         # 宿主机执行不算隔离
    assert r.success is True
    assert "it works" in r.stdout
    assert r.sandbox["policy"] == "off" and "显式关闭" in r.warning


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["auto", "required"])
async def test_run_code_shell_flag_cannot_bypass_required_isolation(monkeypatch, tmp_path, policy):
    from src.agents import sandbox as sb

    marker = tmp_path / "must-not-exist"
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    monkeypatch.setattr(sb, "sandbox_backend", lambda: "")
    monkeypatch.setenv("VORTOCODE_SANDBOX", policy)
    monkeypatch.setenv("VORTOCODE_ENABLE_SHELL", "1")

    r = await runner.run_code(
        f"from pathlib import Path; Path({str(marker)!r}).write_text('bypass')",
        "python",
        workspace=str(tmp_path),
    )

    assert not r.success and r.runtime == "none" and not r.isolated
    assert r.sandbox["allowed"] is False
    assert not marker.exists()


@pytest.mark.asyncio
async def test_run_code_uses_available_os_sandbox_without_host_shell_gate(monkeypatch, tmp_path):
    from src.agents import sandbox as sb

    monkeypatch.setattr(runner, "docker_available", lambda: False)
    monkeypatch.setattr(sb, "sandbox_backend", lambda: "bubblewrap")
    monkeypatch.setenv("VORTOCODE_SANDBOX", "required")
    monkeypatch.delenv("VORTOCODE_ENABLE_SHELL", raising=False)
    captured = {}

    async def fake_run(argv, timeout, *, runtime, isolated, cwd):
        captured.update(argv=argv, timeout=timeout, runtime=runtime,
                        isolated=isolated, cwd=cwd)
        return runner.RunResult(success=True, runtime=runtime, isolated=isolated)

    monkeypatch.setattr(runner, "_run_code_argv", fake_run)
    r = await runner.run_code("print('isolated')", "python", workspace=str(tmp_path))

    assert r.success and r.runtime == "bubblewrap" and r.isolated
    assert captured["argv"][0] == "bwrap"
    assert captured["cwd"] == str(tmp_path)
    assert r.sandbox["policy"] == "required"


@pytest.mark.asyncio
async def test_cloud_sandbox_threads_its_workspace_into_run_code(monkeypatch, tmp_path):
    from src.cloud_sandbox.manager import SandboxConfig, SandboxInstance, SandboxStatus

    captured = {}

    async def fake_run_code(code, language="python", timeout=30, workspace=None):
        captured.update(code=code, language=language, timeout=timeout, workspace=workspace)
        return runner.RunResult(success=True, stdout="ok", exit_code=0)

    monkeypatch.setattr(runner, "run_code", fake_run_code)
    sandbox = SandboxInstance(SandboxConfig(timeout=17))
    sandbox.status = SandboxStatus.RUNNING
    sandbox.work_dir = tmp_path

    result = await sandbox.execute("print('ok')", language="python")

    assert result.success and result.stdout == "ok"
    assert captured == {
        "code": "print('ok')",
        "language": "python",
        "timeout": 17,
        "workspace": str(tmp_path),
    }


@pytest.mark.asyncio
async def test_run_pytest_uses_docker_when_image_configured(monkeypatch, tmp_path):
    # 隔离为 opt-in：配置了测试镜像 + Docker 可用才走容器
    monkeypatch.setenv("VORTOCODE_SANDBOX_IMAGE", "myorg/pytest:latest")
    monkeypatch.setattr(runner, "docker_available", lambda: True)
    captured = {}

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"1 passed in 0.01s", b"")

    async def _fake_exec(*argv, **kw):
        captured["argv"] = argv
        return _FakeProc()

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", _fake_exec)

    r = await runner.run_pytest(str(tmp_path))
    assert r.runtime == "docker"
    assert r.isolated is True
    assert r.sandbox["backend"] == "docker" and r.sandbox["isolated"] is True
    argv = captured["argv"]
    assert "docker" in argv and "run" in argv            # 走容器
    assert "--network" in argv and "none" in argv          # 断网
    assert "-v" in argv and "-w" in argv                   # 挂载 + 工作目录
    assert "myorg/pytest:latest" in argv                   # 用配置的镜像


@pytest.mark.asyncio
async def test_run_pytest_host_when_no_image(monkeypatch, tmp_path):
    # 测试套件显式 VORTOCODE_SANDBOX=off → 可信 fixture 可走宿主机。
    monkeypatch.delenv("VORTOCODE_SANDBOX_IMAGE", raising=False)
    monkeypatch.setenv("VORTOCODE_SANDBOX", "off")
    monkeypatch.setattr(runner, "docker_available", lambda: True)
    (tmp_path / "test_x.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")

    r = await runner.run_pytest(str(tmp_path), timeout=60)
    assert r.runtime == "host"
    assert r.isolated is False


@pytest.mark.asyncio
async def test_run_pytest_fails_closed_without_os_sandbox(monkeypatch, tmp_path):
    from src.agents import sandbox as sb
    monkeypatch.delenv("VORTOCODE_SANDBOX_IMAGE", raising=False)
    monkeypatch.delenv("VORTOCODE_SANDBOX", raising=False)
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    monkeypatch.setattr(sb, "sandbox_backend", lambda: "")

    r = await runner.run_pytest(str(tmp_path), timeout=5)
    assert not r.success and r.runtime == "none" and not r.isolated
    assert "无人值守" in r.error


@pytest.mark.asyncio
async def test_run_pytest_uses_available_os_sandbox(monkeypatch, tmp_path):
    from src.agents import sandbox as sb
    monkeypatch.delenv("VORTOCODE_SANDBOX_IMAGE", raising=False)
    monkeypatch.delenv("VORTOCODE_SANDBOX", raising=False)
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    monkeypatch.setattr(sb, "sandbox_backend", lambda: "bubblewrap")
    captured = {}

    async def fake_exec(argv, timeout, runtime, isolated, cwd=None):
        captured.update(argv=argv, timeout=timeout, runtime=runtime,
                        isolated=isolated, cwd=cwd)
        return runner.RunResult(success=True, runtime=runtime, isolated=isolated)

    monkeypatch.setattr(runner, "_exec_argv", fake_exec)
    r = await runner.run_pytest(str(tmp_path), timeout=5)
    assert r.success and r.runtime == "bubblewrap" and r.isolated
    assert captured["argv"][0] == "bwrap" and captured["cwd"] == str(tmp_path)


# ── 子进程凭据隔离：生成代码/测试子进程 env 必须过 child_env ──────────────────
# runner 跑的是任意生成代码（python3 -c / node -e / bash -c）与仓库可控 pytest，且 OS-沙箱
# 回退路径网络开放——密钥留在 env 即等于把中转站 key 递到不可信代码手里可外带。与 /api/runs
# （shell.py 走 child_env）同信任层，必须同口径。

@pytest.mark.asyncio
async def test_run_code_host_strips_operating_secret(monkeypatch):
    """host escape hatch 路径起的生成代码读不到操作密钥（真子进程，端到端）。"""
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    monkeypatch.setenv("VORTOCODE_SANDBOX", "off")          # 确定性走 host 分支（不依赖本机沙箱）
    monkeypatch.setenv("VORTOCODE_ENABLE_SHELL", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak")
    monkeypatch.delenv("VORTOCODE_ENV_PASSTHROUGH", raising=False)
    r = await runner.run_code(
        "import os; print('KEY=[' + os.environ.get('OPENAI_API_KEY', '') + ']')", "python")
    assert r.success, r.error or r.stdout
    assert "KEY=[]" in r.stdout, r.stdout                   # 密钥被剥，生成代码读到空


@pytest.mark.asyncio
async def test_run_code_host_honors_env_passthrough(monkeypatch):
    """allowlist 逃生口放行的密钥能被生成代码读到——钉住 child_env 的 allowlist 分支生效
    （区别于会连放行项也一并剥掉的硬编码黑名单）。注意：撤掉补丁本测仍绿（env 继承同值），
    故它不承载「剥密钥」这条安全属性——那由上面两条 placebo-proof 测试钉住。"""
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    monkeypatch.setenv("VORTOCODE_SANDBOX", "off")
    monkeypatch.setenv("VORTOCODE_ENABLE_SHELL", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-allowed")
    monkeypatch.setenv("VORTOCODE_ENV_PASSTHROUGH", "OPENAI_API_KEY")
    r = await runner.run_code(
        "import os; print('KEY=[' + os.environ.get('OPENAI_API_KEY', '') + ']')", "python")
    assert r.success, r.error
    assert "KEY=[sk-allowed]" in r.stdout, r.stdout


@pytest.mark.asyncio
async def test_exec_argv_passes_child_env(monkeypatch):
    """_exec_argv（run_pytest 用的执行助手）给子进程显式传过 child_env 的 env——剥密钥、保 PATH。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.delenv("VORTOCODE_ENV_PASSTHROUGH", raising=False)
    captured = {}

    async def _fake_exec(*argv, **kwargs):
        captured["env"] = kwargs.get("env")
        raise RuntimeError("_stop_after_capture")          # 捕获后即止，不真起进程

    monkeypatch.setattr(runner.asyncio, "create_subprocess_exec", _fake_exec)
    await runner._exec_argv(["true"], timeout=5, runtime="host", isolated=False)
    assert captured["env"] is not None, "必须显式传 env（env=None=继承整套=泄漏）"
    assert "OPENAI_API_KEY" not in captured["env"], "操作密钥必须被 child_env 剥出"
    assert captured["env"].get("PATH") == "/usr/bin", "非密钥项须保留"
