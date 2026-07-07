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
import tempfile
from pathlib import Path
from typing import Awaitable, Callable, Optional


def _git(cwd, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=check)


# 共享 .git 上的写操作（worktree add/remove/prune、分支建/提交/apply）必须串行：多个并行
# run_isolated_task（asyncio.gather）若真并发跑这些会在 worktree 登记表/refs 上竞争。此前它们是
# 同步 subprocess 直接在事件循环上跑——侥幸被单线程串行化，但**冻住整个事件循环**（UI 卡死），
# 且"并行 worktree"在 git 阶段并未真并行。改用 _git_op：丢线程（不冻循环）+ 全局锁（防竞争），
# 而慢的子 agent 实现 / 跑测试在锁外 → 真并行。（py310+ 的 asyncio.Lock 可安全在模块级创建。）
_GIT_LOCK = asyncio.Lock()


async def _git_op(fn: Callable, *args, **kwargs):
    """把一个会碰共享 repo 的同步 git 操作丢线程执行并全局串行化（不冻事件循环、不竞争 .git）。"""
    async with _GIT_LOCK:
        return await asyncio.to_thread(fn, *args, **kwargs)


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
    from src.agents.dev_plan import ensure_state_gitignore
    ensure_state_gitignore(repo_root)              # dev_isolated-only 流程也要自忽略（没有计划文件可触发）
    path = _worktrees_dir(repo_root) / wid
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():                              # 残留则先清，避免 add 冲突
        remove_worktree(repo_root, path)
    _git(repo_root, "worktree", "add", "--detach", str(path), "HEAD")
    return path


# 收集 diff 时兜底排除的构建/缓存噪音（gitignore 语法）：隔离实现子 agent 自测会生成
# __pycache__/*.pyc 等产物，它们随 `git add -A` 进 diff 后，落分支的 `git apply` 会栽在
# 二进制补丁上（"cannot apply binary patch ... without full index line"）→ 绿了也落不了分支。
# 目标仓库没配 .gitignore 时必中招；VortoCode 自己有 .gitignore，故 dogfooding 一直没暴露。
# 这里用临时 excludesFile 在 `git add` 阶段就挡掉，不依赖目标仓库的 .gitignore。
_DIFF_ARTIFACT_IGNORES = (
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "*.egg-info/",
    "node_modules/",
    ".DS_Store",
)


def collect_diff(worktree) -> str:
    """worktree 内相对 HEAD 的全部改动（含新增文件）的 unified diff。

    用临时 excludesFile 把构建/缓存噪音（见 _DIFF_ARTIFACT_IGNORES）挡在 `git add` 之外，
    不依赖目标仓库自带 .gitignore——否则自测生成的 .pyc 等会污染 diff 并令落分支失败。
    """
    with tempfile.NamedTemporaryFile("w", suffix=".gitexclude",
                                     delete=False, encoding="utf-8") as f:
        f.write("\n".join(_DIFF_ARTIFACT_IGNORES) + "\n")
        excludes = f.name
    try:
        # `git add` 默认跳过被忽略的未跟踪文件；用 -c 注入 excludesFile 即兜底忽略上面这些。
        _git(worktree, "-c", f"core.excludesFile={excludes}", "add", "-A")
        return _git(worktree, "diff", "--cached").stdout
    finally:
        Path(excludes).unlink(missing_ok=True)


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


def apply_diffs_to_branch(repo_root, branch: str, items: list,
                          test_cmd: Optional[list] = None) -> dict:
    """把多个 (diff, message) 依次 apply+commit 到**一个**新分支（一次性 worktree，不碰 main/工作区）。

    用于并行实现的"集成"：每块绿 diff 作为一个提交。某块应用不干净（如互相冲突）则跳过并记账。
    返回 {ok, branch, applied:[msg...], failed:[{msg,error}...], integration}。
    一块都没应用则删掉空分支。

    **集成后验证（给了 test_cmd 时）**：各块只在自己的隔离 worktree（同一 base HEAD）单独验证过，
    组合到一个分支上可能语义冲突/相互破坏（A 改了 B 依赖的行为、共用的测试被同时影响…）——
    全部 apply+commit 后，在集成分支的 worktree 里**再跑一遍测试**，抓"单独绿、合起来红"。
    integration = {ok, output, cmd}（未给 test_cmd 则 None）。集成测试红**不删分支**（保留待修），
    但如实把 integration.ok 标 False，让上层别再谎报"全绿"。
    """
    path = _worktrees_dir(repo_root) / ("apply-" + branch.replace("/", "-"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        remove_worktree(repo_root, path)
    add = _git(repo_root, "worktree", "add", "-b", branch, str(path), "HEAD", check=False)
    if add.returncode != 0:
        return {"ok": False, "branch": branch, "applied": [], "integration": None,
                "failed": [{"msg": "(worktree add)", "error": (add.stderr or "").strip()[:300]}]}
    applied: list = []
    failed: list = []
    integration: Optional[dict] = None
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
        # 集成后验证：在合并了全部绿块的分支上再跑一遍测试（worktree 还没拆，正好就地测）
        if applied and test_cmd:
            integration = run_tests(path, test_cmd)
        return {"ok": bool(applied), "branch": branch, "applied": applied,
                "failed": failed, "integration": integration}
    finally:
        remove_worktree(repo_root, path)
        if not applied:
            _git(repo_root, "branch", "-D", branch, check=False)


async def in_worktree(repo_root, wid: str,
                      work: Callable[[Path], Awaitable]) -> tuple[str, object]:
    """在隔离 worktree 里 await work(path)，返回 (diff, work 的返回值)；无论成败都清理 worktree。"""
    path = await _git_op(add_worktree, repo_root, wid)     # git 操作丢线程 + 全局串行（不冻循环/不竞争）
    try:
        result = await work(path)                          # work（子 agent）在锁外 → 并行时真并发
        return await _git_op(collect_diff, path), result
    finally:
        await _git_op(remove_worktree, repo_root, path)


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
    path = await _git_op(add_worktree, repo_root, wid)     # git 操作丢线程 + 全局串行（不冻循环/不竞争）
    try:
        agent = build_agent(str(path))
        conclusion = await agent.run_turn(description, mode=mode, emit=lambda _t: None)  # 锁外，真并发
        diff = await _git_op(collect_diff, path)           # 先收 diff，再跑测试
        # pytest 慢且阻塞：丢线程跑（锁外），既不冻 UI、并行时多个测试也能真并发（各自独立 worktree）
        verification = (await asyncio.to_thread(run_tests, path, test_cmd)) if test_cmd else None
        return diff, conclusion, verification
    finally:
        await _git_op(remove_worktree, repo_root, path)


def ensure_branch(repo_root, branch: str, base: str = "HEAD") -> None:
    """branch 不存在则在 base 处建（不切换、不碰工作区/当前分支）。幂等。给依赖子任务一个落脚的基底分支。"""
    exists = _git(repo_root, "rev-parse", "--verify", "--quiet", branch, check=False).returncode == 0
    if not exists:
        _git(repo_root, "branch", branch, base, check=False)


async def run_dependent_on_branch(repo_root, wid: str, branch: str, description: str,
                                  build_agent: Callable[[str], object], message: str,
                                  test_cmd: Optional[list] = None) -> dict:
    """在 branch **之上**跑一个有依赖的子任务（接力）：worktree 检出 branch（于是子 agent 看得见
    前面已落的改动）→ 实现+自测 → 绿则**就地 commit 到 branch**（推进 tip，供下一个依赖子任务看见）；
    红/无改动则丢弃、branch 不动。返回 {ok, conclusion, output}。绝不碰主工作区/主分支。
    """
    path = _worktrees_dir(repo_root) / wid
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        await _git_op(remove_worktree, repo_root, path)
    # 检出 branch（非 detached）——git 操作丢线程 + 全局串行（不冻循环/不竞争共享 .git）
    add = await _git_op(_git, repo_root, "worktree", "add", str(path), branch, check=False)
    if add.returncode != 0:
        return {"ok": False, "conclusion": "", "output": "worktree add 失败: " + (add.stderr or "").strip()[:200]}
    try:
        agent = build_agent(str(path))
        conclusion = await agent.run_turn(description, mode="build", emit=lambda _t: None)   # 锁外
        ver = (await asyncio.to_thread(run_tests, path, test_cmd)) if test_cmd else {"ok": True, "output": ""}
        if not ver["ok"]:                                  # 自测没过：不提交，branch 保持原样
            return {"ok": False, "conclusion": conclusion, "output": ver["output"][-1500:]}
        await _git_op(_git, path, "add", "-A", check=False)
        cm = await _git_op(_git, path, "commit", "-m", message, check=False)
        if cm.returncode != 0:                             # 无改动 / 提交失败
            return {"ok": False, "conclusion": conclusion,
                    "output": "无改动或提交失败: " + (cm.stdout + cm.stderr).strip()[:200]}
        return {"ok": True, "conclusion": conclusion, "output": ""}
    finally:
        await _git_op(remove_worktree, repo_root, path)


def run_runtime_check(worktree, profile: dict, timeout: int = 180) -> dict:
    """在 worktree 里跑一条**运行时验证** profile（"真能跑起来"，不止"测试绿"），返回 {ok, name, cmd, output}。

    两种形态（见 verify_profiles）：
    - **serve + check**：后台起 serve（复用后台命令基础设施——沙箱同源、退出时按进程组 killpg 连根收），
      反复跑 check 探活直到通过或 ready_timeout 秒；无论成败**都停掉 serve**。判定=最后一次 check 是否通过。
      serve 中途自己崩了则带其输出提前判失败。
    - **只有 cmd**：阻塞跑 cmd，退出码判定（自包含冒烟/e2e/build/self-analyze）。
    危险命令（serve/check/cmd 任一）一律先被 is_dangerous 拦下：拒绝执行、判不通过。
    """
    import time
    from src.agents.shell import (is_dangerous, read_background,
                                   run_command_background, stop_background)
    name = str(profile.get("name") or "runtime")
    serve = str(profile.get("serve") or "").strip()
    check = str(profile.get("check") or "").strip()
    cmd = str(profile.get("cmd") or "").strip()

    def _guard(command: str) -> str:
        why = is_dangerous(command)
        return f"拒绝执行高危验证命令: {why}" if why else ""

    def _run_once(command: str, tmo: int) -> tuple[bool, str]:
        try:
            r = subprocess.run(command, shell=True, cwd=str(worktree),
                               capture_output=True, text=True, timeout=tmo)
            return r.returncode == 0, (r.stdout + r.stderr)
        except subprocess.TimeoutExpired:
            return False, f"命令超时（>{tmo}s）"
        except Exception as e:  # noqa: BLE001
            return False, f"命令无法运行: {e}"

    if serve:
        probe = check or cmd
        for command in (serve, probe):
            blocked = _guard(command)
            if blocked:
                return {"ok": False, "name": name, "cmd": command, "output": blocked}
        ready_timeout = int(profile.get("ready_timeout") or 30)
        bg = run_command_background(worktree, serve)
        if not bg.get("ok"):
            return {"ok": False, "name": name, "cmd": serve,
                    "output": f"起 serve 失败: {bg.get('error', '')}"}
        bid = bg["id"]
        ok, probe_out = False, ""
        try:
            deadline = time.monotonic() + ready_timeout
            while True:
                st = read_background(bid, tail=40)
                if st.get("ok") and st.get("status") == "exited":   # serve 先崩了 → 别再探
                    probe_out = f"serve 进程提前退出（code={st.get('code')}）:\n{st.get('output', '')}"
                    break
                ok, probe_out = _run_once(probe, min(ready_timeout, 30))
                if ok or time.monotonic() >= deadline:
                    break
                time.sleep(1.5)
            serve_tail = (read_background(bid, tail=25) or {}).get("output", "")
            out = probe_out + (f"\n--- serve 输出尾部 ---\n{serve_tail}" if serve_tail else "")
            return {"ok": ok, "name": name, "cmd": f"serve: {serve} | check: {probe}",
                    "output": out[-4000:]}
        finally:
            stop_background(bid)

    target = cmd or check
    blocked = _guard(target)
    if blocked:
        return {"ok": False, "name": name, "cmd": target, "output": blocked}
    ok, out = _run_once(target, timeout)
    return {"ok": ok, "name": name, "cmd": target, "output": out[-4000:]}


def verify_branch(repo_root, branch: str, test_cmd: list, wid: str,
                  runtime_profiles: Optional[list] = None) -> dict:
    """在临时 worktree 检出 branch 跑一遍测试做**最终集成验证**，返回 {ok, output, cmd}；清理 worktree。

    （dev_auto 把并行批 + 依赖接力都落到同一分支后，用它对整条分支做一次权威全量复验。）

    给了 runtime_profiles（`.vortocode/verify.yaml` 里标 auto 的运行时验证）且**单测先过**，就在
    同一 worktree 里再跑一遍运行时验证（"真能跑起来"）：结果并进 `result["runtime"]=[...]`，任一红
    则整体 ok=False。单测就红则跳过运行时（省时，反正已判失败）。
    """
    path = _worktrees_dir(repo_root) / wid
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        remove_worktree(repo_root, path)
    add = _git(repo_root, "worktree", "add", str(path), branch, check=False)
    if add.returncode != 0:
        return {"ok": False, "output": "worktree add 失败: " + (add.stderr or "").strip()[:200], "cmd": ""}
    try:
        result = run_tests(path, test_cmd)
        if result.get("ok") and runtime_profiles:
            runtime = [run_runtime_check(path, prof) for prof in runtime_profiles]
            result["runtime"] = runtime
            if not all(rc.get("ok") for rc in runtime):
                result["ok"] = False
        return result
    finally:
        remove_worktree(repo_root, path)
