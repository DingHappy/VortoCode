"""场景执行器（有副作用）——建 scratch 仓库、真机驱动 dev 工具、采集 git 事实、独立复验、评分。

诚实性的关键动作都在这里：landed 不看 agent 报的 ✅，而是 harness **自己**用 verify_branch 在新
worktree 检出分支跑一遍测试；cleanliness 直接读 git status/worktree list/base HEAD。
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple

from src.agents.main_agent import _branch_changed_files, build_dev_tools
from src.agents.test_detect import detect_test_cmd
from src.agents.worktree import verify_branch

from .scoring import GitFacts, Score, score


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=check)


def _git_init(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "eval@vortocode.local")
    _git(repo, "config", "user.name", "vortocode-eval")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")


def _rev(repo: Path, ref: str = "HEAD") -> str:
    return _git(repo, "rev-parse", ref).stdout.strip()


def _current_branch(repo: Path) -> str:
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _vorto_branches(repo: Path) -> list:
    out = _git(repo, "branch", "--list", "vorto/*", check=False).stdout
    return [ln.strip().lstrip("* ").strip() for ln in out.splitlines() if ln.strip()]


def _worktree_count(repo: Path) -> int:
    out = _git(repo, "worktree", "list", "--porcelain", check=False).stdout
    return sum(1 for ln in out.splitlines() if ln.startswith("worktree "))


async def _always_yes(_msg: str) -> bool:
    return True


async def run_scenario(scenario, workdir: Path) -> Tuple[Optional[Score], str]:
    """跑一个场景，返回 (Score 或 None, note)。None + note = 跳过/环境缺失（note 说明原因，不静默）。"""
    if scenario.needs_node and not shutil.which("node"):
        return None, "跳过：本机未装 node（node_repo 需要 `node --test`）"

    repo = workdir / scenario.name
    repo.mkdir(parents=True, exist_ok=True)
    scenario.setup(repo)
    _git_init(repo)
    if scenario.post_init is not None:      # resume 类场景：git init 后预置"跑到一半"的状态（分支/计划）
        scenario.post_init(repo)
    base_branch = _current_branch(repo)
    base_head = _rev(repo)

    progress: list = []
    tools = {t.name: t for t in build_dev_tools(
        str(repo), on_progress=progress.append, confirm=_always_yes)}
    tool = tools.get(scenario.tool)
    if tool is None:
        return None, f"跳过：工具 {scenario.tool} 不存在（装配变更？）"

    t0 = time.monotonic()
    try:
        message = await tool.handler(dict(scenario.args))
    except Exception as e:  # noqa: BLE001 —— 评测要如实记录崩溃，不吞
        message = f"(EXCEPTION) {type(e).__name__}: {e}"
    dt = time.monotonic() - t0

    # --- 采集事实：先取"工具留下的残留"（在我自己的 verify 之前，避免被我的临时 worktree 干扰）
    branches = _vorto_branches(repo)
    worktree_clean = not _git(repo, "status", "--porcelain", check=False).stdout.strip()
    only_main_worktree = _worktree_count(repo) == 1
    base_untouched = (_rev(repo) == base_head) and (_current_branch(repo) == base_branch)

    # --- 独立复验首个 vorto 分支（harness 自己跑，不信 agent 的 ✅）
    verify_ok: Optional[bool] = None
    changed_files: list = []
    if branches:
        b0 = branches[0]
        test_cmd = detect_test_cmd(str(repo))
        r = verify_branch(str(repo), b0, test_cmd, "wt-evalverify-" + uuid.uuid4().hex[:8])
        verify_ok = bool(r.get("ok"))
        changed_files = _branch_changed_files(str(repo), base_branch, b0) or []

    facts = GitFacts(vorto_branches=branches, verify_ok=verify_ok, changed_files=changed_files,
                     worktree_clean=worktree_clean, only_main_worktree=only_main_worktree,
                     base_untouched=base_untouched)
    return score(scenario, message, facts, dt), ""
