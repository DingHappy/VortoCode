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


@pytest.mark.asyncio
async def test_refuses_without_docker_and_without_shell(monkeypatch):
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    monkeypatch.delenv("AUTODEV_ENABLE_SHELL", raising=False)

    r = await runner.run_code("print('hi')", "python")
    assert r.success is False
    assert r.runtime == "none"
    assert r.isolated is False
    assert "AUTODEV_ENABLE_SHELL" in r.error          # 明确告知如何开启


@pytest.mark.asyncio
async def test_host_fallback_when_shell_enabled(monkeypatch):
    monkeypatch.setattr(runner, "docker_available", lambda: False)
    monkeypatch.setenv("AUTODEV_ENABLE_SHELL", "1")

    r = await runner.run_code("print('it works')", "python")
    assert r.runtime == "host"
    assert r.isolated is False                         # 宿主机执行不算隔离
    assert r.success is True
    assert "it works" in r.stdout


@pytest.mark.asyncio
async def test_run_pytest_uses_docker_when_image_configured(monkeypatch, tmp_path):
    # 隔离为 opt-in：配置了测试镜像 + Docker 可用才走容器
    monkeypatch.setenv("AUTODEV_SANDBOX_IMAGE", "myorg/pytest:latest")
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
    argv = captured["argv"]
    assert "docker" in argv and "run" in argv            # 走容器
    assert "--network" in argv and "none" in argv          # 断网
    assert "-v" in argv and "-w" in argv                   # 挂载 + 工作目录
    assert "myorg/pytest:latest" in argv                   # 用配置的镜像


@pytest.mark.asyncio
async def test_run_pytest_host_when_no_image(monkeypatch, tmp_path):
    # 未配置镜像 → 即使 Docker 可用也走宿主机（不破坏默认开发流程）
    monkeypatch.delenv("AUTODEV_SANDBOX_IMAGE", raising=False)
    monkeypatch.setattr(runner, "docker_available", lambda: True)
    (tmp_path / "test_x.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")

    r = await runner.run_pytest(str(tmp_path), timeout=60)
    assert r.runtime == "host"
    assert r.isolated is False
