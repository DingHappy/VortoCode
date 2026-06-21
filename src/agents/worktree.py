"""git worktree 隔离——在一次性工作树里跑"写"工作、收集 diff、保证清理。

让可写子 agent 在隔离的 worktree 里改代码，**绝不碰主工作区/主分支**；改动整体产出
unified diff 待人工确认。这是大工程量"并行实现"的地基（本步先做单个、串行、不自动并入）。
worktree 建在 .vortocode/worktrees/<id>（gitignored），不污染 git status。

本模块不依赖任何 agent/UI：跑什么由调用方注入（work 回调 / build_agent），便于确定性测试。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Awaitable, Callable, Optional


def _git(cwd, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=check)


def _worktrees_dir(repo_root) -> Path:
    return Path(repo_root) / ".vortocode" / "worktrees"


def add_worktree(repo_root, wid: str) -> Path:
    """在 .vortocode/worktrees/<wid> 建一个基于当前 HEAD 的 detached worktree。"""
    path = _worktrees_dir(repo_root) / wid
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():                              # 残留则先清，避免 add 冲突
        remove_worktree(repo_root, path)
    _git(repo_root, "worktree", "add", "--detach", str(path), "HEAD")
    return path


def collect_diff(worktree) -> str:
    """worktree 内相对 HEAD 的全部改动（含新增文件）的 unified diff。"""
    _git(worktree, "add", "-A")                    # 暂存全部（含新文件）→ diff --cached 能看全
    return _git(worktree, "diff", "--cached").stdout


def remove_worktree(repo_root, path) -> None:
    """移除 worktree 并清理目录与登记（幂等、不抛）。"""
    path = Path(path)
    _git(repo_root, "worktree", "remove", "--force", str(path), check=False)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    _git(repo_root, "worktree", "prune", check=False)


async def in_worktree(repo_root, wid: str,
                      work: Callable[[Path], Awaitable]) -> tuple[str, object]:
    """在隔离 worktree 里 await work(path)，返回 (diff, work 的返回值)；无论成败都清理 worktree。"""
    path = add_worktree(repo_root, wid)
    try:
        result = await work(path)
        return collect_diff(path), result
    finally:
        remove_worktree(repo_root, path)


def run_tests(worktree, cmd: Optional[list] = None, timeout: int = 600) -> dict:
    """在 worktree 里跑测试做逐件验证，返回 {ok, output, cmd}。默认 `pytest -q`；输出截尾。"""
    import sys
    cmd = list(cmd) if cmd else [sys.executable, "-m", "pytest", "-q"]
    try:
        r = subprocess.run(cmd, cwd=str(worktree), capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr)[-4000:], "cmd": " ".join(cmd)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": f"测试超时（>{timeout}s）", "cmd": " ".join(cmd)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "output": f"测试无法运行: {e}", "cmd": " ".join(cmd)}


async def run_isolated_task(repo_root, wid: str, description: str,
                            build_agent: Callable[[str], object],
                            mode: str = "build",
                            test_cmd: Optional[list] = None) -> tuple[str, object, Optional[dict]]:
    """在隔离 worktree 里让可写子 agent 实现 description；给了 test_cmd 就在其中跑测试逐件验证。

    返回 (diff, 子 agent 结论, verification)。verification 为 None（未要求）或 {ok, output, cmd}。
    diff 在跑测试**之前**收集，避免测试产物混入（虽然 __pycache__/.pytest_cache 已 gitignore）。
    build_agent(worktree_path) -> 有 run_turn 的 agent（注入便于测试、也避免本模块依赖 MainAgent）。
    """
    path = add_worktree(repo_root, wid)
    try:
        agent = build_agent(str(path))
        conclusion = await agent.run_turn(description, mode=mode, emit=lambda _t: None)
        diff = collect_diff(path)                          # 先收 diff，再跑测试
        verification = run_tests(path, test_cmd) if test_cmd else None
        return diff, conclusion, verification
    finally:
        remove_worktree(repo_root, path)
