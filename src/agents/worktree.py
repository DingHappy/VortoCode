"""git worktree 隔离——在一次性工作树里跑"写"工作、收集 diff、保证清理。

让可写子 agent 在隔离的 worktree 里改代码，**绝不碰主工作区/主分支**；改动整体产出
unified diff 待人工确认。这是大工程量"并行实现"的地基（本步先做单个、串行、不自动并入）。
worktree 建在 .vortocode/worktrees/<id>（gitignored），不污染 git status。

本模块不依赖任何 agent/UI：跑什么由调用方注入（work 回调 / build_agent），便于确定性测试。
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Awaitable, Callable, Optional


def _git(cwd, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=check)


def _worktrees_dir(repo_root) -> Path:
    return Path(repo_root) / ".vortocode" / "worktrees"


def _apply_diff(path, diff: str) -> tuple[bool, str]:
    """把一个 unified diff 应用到 worktree。先直 apply；失败退一步 `--3way`。

    并行集成里前一块改过的上下文常让后一块直 apply 因"上下文不匹配"被拒，但真实改动并不重叠
    ——`--3way` 用 diff 的 base blob 做三方合并，这种情况能干净合上（rc=0、无冲突 marker）。
    真冲突时 3way 返回非 0 并留 `<<<<<<<` marker → 当失败处理（调用方会 reset 清掉，绝不提交 marker）。
    返回 (是否干净应用, 错误信息)。
    """
    common = ["git", "-C", str(path), "apply", "--whitespace=nowarn"]
    ap = subprocess.run(common, input=diff, capture_output=True, text=True)
    if ap.returncode == 0:
        return True, ""
    ap3 = subprocess.run(common + ["--3way"], input=diff, capture_output=True, text=True)
    if ap3.returncode == 0:                       # 三方干净合上（无 marker）
        return True, ""
    return False, (ap3.stderr or ap.stderr or "").strip()[:300]


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


def apply_diff_to_branch(repo_root, branch: str, diff: str, message: str) -> dict:
    """把 diff 应用到一个**新分支**并提交——用一次性 worktree，绝不碰主工作区/当前分支/main。

    返回 {ok, branch, error}。失败（apply/commit 出错）则删掉刚建的空分支，不留残留。
    """
    path = _worktrees_dir(repo_root) / ("apply-" + branch.replace("/", "-"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        remove_worktree(repo_root, path)
    add = _git(repo_root, "worktree", "add", "-b", branch, str(path), "HEAD", check=False)
    if add.returncode != 0:
        return {"ok": False, "branch": branch, "error": (add.stderr or "worktree add 失败").strip()[:300]}
    ok = False
    try:
        applied, err = _apply_diff(path, diff)    # 直 apply，失败退一步 3way
        if not applied:
            return {"ok": False, "branch": branch, "error": "git apply 失败: " + err}
        _git(path, "add", "-A")
        cm = _git(path, "commit", "-m", message, check=False)
        if cm.returncode != 0:
            return {"ok": False, "branch": branch, "error": "commit 失败: " + (cm.stderr or "").strip()[:300]}
        ok = True
        return {"ok": True, "branch": branch, "error": ""}
    finally:
        remove_worktree(repo_root, path)
        if not ok:
            _git(repo_root, "branch", "-D", branch, check=False)   # 失败：删掉残留空分支


def apply_diffs_to_branch(repo_root, branch: str, items: list) -> dict:
    """把多个 (diff, message) 依次 apply+commit 到**一个**新分支（一次性 worktree，不碰 main/工作区）。

    用于并行实现的"集成"：每块绿 diff 作为一个提交。某块应用不干净（如互相冲突）则跳过并记账。
    返回 {ok, branch, applied:[msg...], failed:[{msg,error}...]}；一块都没应用则删掉空分支。
    """
    path = _worktrees_dir(repo_root) / ("apply-" + branch.replace("/", "-"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        remove_worktree(repo_root, path)
    add = _git(repo_root, "worktree", "add", "-b", branch, str(path), "HEAD", check=False)
    if add.returncode != 0:
        return {"ok": False, "branch": branch, "applied": [],
                "failed": [{"msg": "(worktree add)", "error": (add.stderr or "").strip()[:300]}]}
    applied: list = []
    failed: list = []
    try:
        for diff, msg in items:
            if not (diff or "").strip():
                continue
            applied_ok, err = _apply_diff(path, diff)   # 直 apply，失败退一步 3way
            if not applied_ok:
                failed.append({"msg": msg, "error": err})
                _git(path, "reset", "--hard", "HEAD", check=False)   # 清掉 3way 冲突 marker/半成品
                _git(path, "clean", "-fd", check=False)              # 顺带清未跟踪残留，保持干净给下一块
                continue
            _git(path, "add", "-A")
            cm = _git(path, "commit", "-m", msg, check=False)
            if cm.returncode != 0:
                failed.append({"msg": msg, "error": "commit 失败: " + (cm.stderr or "").strip()[:200]})
                continue
            applied.append(msg)
        return {"ok": bool(applied), "branch": branch, "applied": applied, "failed": failed}
    finally:
        remove_worktree(repo_root, path)
        if not applied:
            _git(repo_root, "branch", "-D", branch, check=False)


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
        # pytest 慢且阻塞：丢线程跑，既不冻 UI、并行时多个测试也能真并发（各自独立 worktree）
        verification = (await asyncio.to_thread(run_tests, path, test_cmd)) if test_cmd else None
        return diff, conclusion, verification
    finally:
        remove_worktree(repo_root, path)
