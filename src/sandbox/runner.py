"""统一代码执行入口：优先 Docker 真隔离，无 Docker 时按显式授权降级宿主机。

设计：
- Docker 可用 → 走 DockerSandbox（容器内执行，network 关闭），isolated=True。
- 无 Docker：仅当 AUTODEV_ENABLE_SHELL=1（显式授权）时降级到宿主机执行；
  否则 fail-closed 拒绝执行，避免"以为有隔离其实没有"的安全假象。
- 用 exec + 参数数组，杜绝 shell 字符串拼接注入。
"""

import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class RunResult(BaseModel):
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    runtime: str = "none"     # docker | host | none
    isolated: bool = False
    error: str = ""


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
    return os.getenv("AUTODEV_ENABLE_SHELL", "").strip().lower() in ("1", "true", "yes", "on")


async def run_code(code: str, language: str = "python", timeout: int = 30) -> RunResult:
    """统一执行入口：Docker 优先，必要时按授权降级宿主机。"""
    # 1. Docker 真隔离
    if docker_available():
        try:
            from .docker_sandbox import SandboxExecutor
            r = await SandboxExecutor().execute_in_sandbox(code, language, timeout)
            return RunResult(
                success=r.success, stdout=r.stdout or "",
                stderr=getattr(r, "stderr", "") or "", exit_code=getattr(r, "exit_code", 0),
                runtime="docker", isolated=True,
            )
        except Exception as e:
            logger.warning("Docker 执行失败，尝试降级宿主机: %s", e)

    # 2. 无隔离：未显式授权则拒绝（fail-closed）
    if not host_exec_allowed():
        return RunResult(
            success=False, runtime="none", isolated=False,
            error="无 Docker 隔离环境，且未开启宿主机执行（AUTODEV_ENABLE_SHELL=1），已拒绝执行。",
        )

    # 3. 已显式授权 → 宿主机降级执行（exec 数组，无 shell 注入）
    return await _run_on_host(code, language, timeout)


async def run_pytest(workspace: str, timeout: int = 120) -> RunResult:
    """在工作区运行 pytest。

    隔离为 opt-in：设置了含 pytest 的镜像 AUTODEV_SANDBOX_IMAGE 且 Docker 可用时，
    挂载工作区到容器内隔离执行（network 关闭，命令仅 pytest，无需联网）；
    否则宿主机执行——这是 dev 流程的内部受信执行（非未鉴权攻击面），不 fail-closed，
    以免在未配置测试镜像的常见环境下破坏开发闭环。
    """
    ws = str(Path(workspace).resolve())
    image = os.getenv("AUTODEV_SANDBOX_IMAGE", "").strip()
    if image and docker_available():
        argv = ["docker", "run", "--rm", "--network", "none",
                "-v", f"{ws}:/work", "-w", "/work", image, "python", "-m", "pytest", "-q"]
        return await _exec_argv(argv, timeout, runtime="docker", isolated=True)

    import sys
    return await _exec_shell(f"{sys.executable} -m pytest -q", ws, timeout)


async def _exec_argv(argv, timeout: int, runtime: str, isolated: bool) -> RunResult:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return RunResult(success=proc.returncode == 0, stdout=out.decode(errors="replace"),
                         exit_code=proc.returncode, runtime=runtime, isolated=isolated)
    except asyncio.TimeoutError:
        return RunResult(success=False, runtime=runtime, isolated=isolated, error=f"执行超时（{timeout}s）")
    except Exception as e:
        return RunResult(success=False, runtime=runtime, isolated=isolated, error=str(e))


async def _exec_shell(command: str, cwd: str, timeout: int) -> RunResult:
    try:
        proc = await asyncio.create_subprocess_shell(
            command, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return RunResult(success=proc.returncode == 0, stdout=out.decode(errors="replace"),
                         exit_code=proc.returncode, runtime="host", isolated=False)
    except asyncio.TimeoutError:
        return RunResult(success=False, runtime="host", error=f"执行超时（{timeout}s）")
    except Exception as e:
        return RunResult(success=False, runtime="host", error=str(e))


async def _run_on_host(code: str, language: str, timeout: int) -> RunResult:
    interpreters = {
        "python": ["python3", "-c", code],
        "javascript": ["node", "-e", code],
        "bash": ["bash", "-c", code],
    }
    argv = interpreters.get(language, ["sh", "-c", code])
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return RunResult(
            success=proc.returncode == 0, stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"), exit_code=proc.returncode,
            runtime="host", isolated=False,
        )
    except asyncio.TimeoutError:
        return RunResult(success=False, runtime="host", isolated=False,
                         error=f"执行超时（{timeout}s）")
    except Exception as e:
        return RunResult(success=False, runtime="host", isolated=False, error=str(e))
