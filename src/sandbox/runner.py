"""统一代码执行入口：优先 Docker，随后严格遵守共享 OS sandbox 策略。

设计：
- Docker 可用 → 走 DockerSandbox（容器内执行，network 关闭），isolated=True。
- 无 Docker → generated-code 路径按 ``resolve_sandbox(require_isolation=True)`` 选择
  Seatbelt/bubblewrap；``auto`` / ``required`` 无 backend 都 fail-closed。
- 只有显式 ``VORTOCODE_SANDBOX=off`` 且 ``VORTOCODE_ENABLE_SHELL=1`` 才允许宿主机执行。
- 用 exec + 参数数组，杜绝 shell 字符串拼接注入。
"""

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RunResult(BaseModel):
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    runtime: str = "none"     # docker | seatbelt | bubblewrap | host | none
    isolated: bool = False
    error: str = ""
    warning: str = ""
    sandbox: dict = Field(default_factory=dict)


_docker_cache: Optional[bool] = None


def docker_available() -> bool:
    """检测 Docker 是否可用（结果缓存；测试可 monkeypatch 本函数或重置缓存）。"""
    global _docker_cache
    if _docker_cache is not None:
        return _docker_cache
    ok = False
    if shutil.which("docker"):
        try:
            r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            ok = r.returncode == 0
        except Exception:
            ok = False
    _docker_cache = ok
    return ok


def host_exec_allowed() -> bool:
    from src.env_compat import env_compat
    return env_compat("VORTOCODE_ENABLE_SHELL", "AUTODEV_ENABLE_SHELL", "").strip().lower() \
        in ("1", "true", "yes", "on")


async def run_code(code: str, language: str = "python", timeout: int = 30,
                   workspace: Optional[str] = None) -> RunResult:
    """执行生成代码：Docker 优先，否则 OS 隔离；host 仅显式 off + shell gate。"""
    # 1. Docker 真隔离
    if docker_available():
        try:
            from .docker_sandbox import SandboxExecutor
            r = await SandboxExecutor().execute_in_sandbox(code, language, timeout)
            return RunResult(
                success=r.success, stdout=r.stdout or "",
                stderr=getattr(r, "stderr", "") or "", exit_code=getattr(r, "exit_code", 0),
                runtime="docker", isolated=True,
                sandbox={"policy": "docker", "backend": "docker", "allowed": True,
                         "isolated": True, "fallback": False,
                         "reason": "Docker 代码沙箱已启用。"},
            )
        except Exception as e:
            logger.warning("Docker 执行失败，尝试共享 OS sandbox 策略: %s", e)

    # 2. 生成代码属于无人值守路径：auto/required 无 backend 必须 fail-closed。
    from src.agents.sandbox import resolve_sandbox, sandboxed_exec_argv
    decision = resolve_sandbox(require_isolation=True)
    evidence = decision.to_dict()
    if not decision.allowed:
        return RunResult(
            success=False, runtime="none", isolated=False,
            error=decision.reason, sandbox=evidence,
        )

    # 3. 显式 off 仍需旧的 shell 能力门；环境变量不能单独绕过 sandbox policy。
    if not decision.isolated and not host_exec_allowed():
        error = ("OS 沙箱已显式关闭，但未开启宿主机代码执行 "
                 "（VORTOCODE_ENABLE_SHELL=1），已拒绝执行。")
        evidence = {**evidence, "allowed": False, "reason": error}
        return RunResult(success=False, runtime="none", isolated=False,
                         error=error, sandbox=evidence)

    workdir = str(Path(workspace or Path.cwd()).resolve())
    argv = _code_argv(code, language)
    if decision.isolated:
        argv = sandboxed_exec_argv(workdir, argv, backend=decision.backend)
    result = await _run_code_argv(
        argv,
        timeout,
        runtime=(decision.backend if decision.isolated else "host"),
        isolated=decision.isolated,
        cwd=workdir,
    )
    result.sandbox = evidence
    if not decision.isolated:
        result.warning = decision.reason
    return result


async def run_pytest(workspace: str, timeout: int = 120) -> RunResult:
    """在工作区运行 pytest。

    配置测试镜像且 Docker 可用时优先走容器；否则使用 agents.sandbox 的
    Seatbelt/bubblewrap 策略。生成代码属于无人值守执行，OS 沙箱不可用时默认 fail-closed，
    只有显式 ``VORTOCODE_SANDBOX=off`` 才允许宿主机执行。
    """
    from src.env_compat import env_compat
    ws = str(Path(workspace).resolve())
    image = env_compat("VORTOCODE_SANDBOX_IMAGE", "AUTODEV_SANDBOX_IMAGE", "").strip()
    if image and docker_available():
        argv = ["docker", "run", "--rm", "--network", "none",
                "-v", f"{ws}:/work", "-w", "/work", image, "python", "-m", "pytest", "-q"]
        result = await _exec_argv(argv, timeout, runtime="docker", isolated=True)
        result.sandbox = {
            "policy": "docker-image", "backend": "docker", "allowed": True,
            "isolated": True, "fallback": False,
            "reason": f"Docker 测试沙箱已启用（{image}，network=none）。",
        }
        return result

    import sys
    from src.agents.sandbox import resolve_sandbox, sandboxed_exec_argv
    decision = resolve_sandbox(require_isolation=True)
    evidence = decision.to_dict()
    if not decision.allowed:
        return RunResult(success=False, runtime="none", isolated=False,
                         error=decision.reason, sandbox=evidence)
    command = [sys.executable, "-m", "pytest", "-q"]
    if decision.isolated:
        command = sandboxed_exec_argv(ws, command, backend=decision.backend)
    result = await _exec_argv(
        command, timeout, runtime=(decision.backend if decision.isolated else "host"),
        isolated=decision.isolated, cwd=ws)
    result.sandbox = evidence
    if not decision.isolated:
        result.warning = decision.reason
    return result


async def _exec_argv(argv, timeout: int, runtime: str, isolated: bool,
                     cwd: Optional[str] = None) -> RunResult:
    # 过 child_env 剥操作密钥：OS-沙箱/host 分支跑的是仓库可控的 pytest（任意 conftest/测试
    # 代码），密钥留在 env + 回退路径网络开放 = 可外带。Docker 分支的 docker CLI 本不需要这些
    # 密钥、容器又拿镜像 env 非宿主 env，补上纯无害纵深。与 shell.py 的 run_command 同口径。
    from src.agents.sandbox import child_env
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=child_env(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return RunResult(success=proc.returncode == 0, stdout=out.decode(errors="replace"),
                         exit_code=proc.returncode, runtime=runtime, isolated=isolated)
    except asyncio.TimeoutError:
        return RunResult(success=False, runtime=runtime, isolated=isolated, error=f"执行超时（{timeout}s）")
    except Exception as e:
        return RunResult(success=False, runtime=runtime, isolated=isolated, error=str(e))


def _code_argv(code: str, language: str) -> list[str]:
    interpreters = {
        "python": ["python3", "-c", code],
        "javascript": ["node", "-e", code],
        "bash": ["bash", "-c", code],
    }
    return interpreters.get(language, ["sh", "-c", code])


async def _run_code_argv(argv: list[str], timeout: int, *, runtime: str,
                         isolated: bool, cwd: str) -> RunResult:
    # 过 child_env 剥操作密钥：这里跑的正是 `python3 -c / node -e / bash -c` 生成代码（可投毒），
    # OS-沙箱回退路径网络开放，密钥留在 env 即等于把中转站 key 递到不可信代码手里可外带。
    # 目标项目自身若需某密钥，走 VORTOCODE_ENV_PASSTHROUGH 显式放行。与 shell.py 同口径。
    from src.agents.sandbox import child_env
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=child_env(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return RunResult(
            success=proc.returncode == 0, stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"), exit_code=proc.returncode,
            runtime=runtime, isolated=isolated,
        )
    except asyncio.TimeoutError:
        return RunResult(success=False, runtime=runtime, isolated=isolated,
                         error=f"执行超时（{timeout}s）")
    except Exception as e:
        return RunResult(success=False, runtime=runtime, isolated=isolated, error=str(e))
