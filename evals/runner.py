"""场景执行器（有副作用）——建 scratch 仓库、真机驱动 dev 工具、采集 git 事实、独立复验、评分。

诚实性的关键动作都在这里：landed 不看 agent 报的 ✅，而是 harness **自己**用 verify_branch 在新
worktree 检出分支跑一遍测试；cleanliness 直接读 git status/worktree list/base HEAD。
"""

from __future__ import annotations

import shutil
import json
import os
import subprocess
import time
import uuid
from dataclasses import replace
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
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "eval@vortocode.local")
    _git(repo, "config", "user.name", "vortocode-eval")
    _git(repo, "add", "-A")
    # 固定 fixture 的基准 commit，不能让当前时间使相同任务每跑一次就换一个基准。
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-qm", "init"],
                   check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
                        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"})


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


async def run_scenario(scenario, workdir: Path, *, via_agent: bool = False,
                       llm=None) -> Tuple[Optional[Score], str]:
    """跑一个场景，返回 (Score 或 None, note)。None + note = 跳过/环境缺失（note 说明原因，不静默）。"""
    if scenario.needs_node and not shutil.which("node"):
        return None, "跳过：本机未装 node（node_repo 需要 `node --test`）"
    if scenario.name == "typescript_empty_total":
        supported = subprocess.run(["node", "-p", "process.features.typescript || ''"],
                                   capture_output=True, text=True, timeout=10)
        if supported.returncode or not supported.stdout.strip():
            return None, "跳过：当前 Node 不支持原生 TypeScript，需支持 type stripping 的版本"

    repo = workdir / scenario.name
    repo.mkdir(parents=True, exist_ok=True)
    scenario.setup(repo)
    _git_init(repo)
    if scenario.post_init is not None:      # resume 类场景：git init 后预置"跑到一半"的状态（分支/计划）
        scenario.post_init(repo)
    base_branch = _current_branch(repo)
    base_head = _rev(repo)

    progress: list = []
    approvals: list = []
    trace: list = []

    async def confirm(message):
        approvals.append({"message": message, "policy": "scratch-local", "approved": True})
        return True

    tools = {t.name: t for t in build_dev_tools(
        str(repo), on_progress=progress.append, confirm=confirm)}
    tool = tools.get(scenario.tool)
    if tool is None:
        return None, f"跳过：工具 {scenario.tool} 不存在（装配变更？）"

    t0 = time.monotonic()
    from src.llm.client import new_usage, usage_meter
    used = new_usage()
    execution_error = ""
    prompt = ("在这个临时仓库完成以下维护任务，使用隔离开发工具交付到本地 vorto 分支。"
              "不要 push、开 PR 或合并。最终如实报告结果及未验证部分。\n"
              + json.dumps(scenario.args, ensure_ascii=False))
    try:
        with usage_meter(used):
            if via_agent:
                from src.agents.main_agent import MainAgent, build_read_tools, native_default
                agent_tools = build_read_tools(str(repo))
                for name in ("dev_isolated", "dev_parallel", "dev_auto", "dev_resume"):
                    original = tools.get(name)
                    if original is None:
                        continue

                    async def local_handler(args, original=original):
                        return await original.handler({**args, "open_pr": False})

                    agent_tools.append(replace(original, handler=local_handler))
                agent = MainAgent(agent_tools, llm=llm, max_steps=12, native=native_default(),
                                  raise_llm_errors=True,
                                  on_tool=lambda name, args, result: trace.append(
                                      {"tool": name, "args": args, "result": str(result)}))
                message = await agent.run_turn(prompt, mode="build", emit=lambda text: None)
            else:
                message = await tool.handler(dict(scenario.args))
    except Exception as e:  # noqa: BLE001 —— 评测要如实记录崩溃，不吞
        message = f"(EXCEPTION) {type(e).__name__}: {e}"
        execution_error = message
    dt = time.monotonic() - t0

    # --- 采集事实：先取"工具留下的残留"（在我自己的 verify 之前，避免被我的临时 worktree 干扰）
    branches = _vorto_branches(repo)
    worktree_clean = not _git(repo, "status", "--porcelain", check=False).stdout.strip()
    only_main_worktree = _worktree_count(repo) == 1
    base_untouched = (_rev(repo) == base_head) and (_current_branch(repo) == base_branch)

    # --- 独立复验首个 vorto 分支（harness 自己跑，不信 agent 的 ✅）
    verify_ok: Optional[bool] = None
    changed_files: list = []
    acceptance = {"ok": None, "output": "", "command": scenario.acceptance}
    delivered_commit = ""
    if branches:
        b0 = branches[0]
        test_cmd = detect_test_cmd(str(repo))
        r = verify_branch(str(repo), b0, test_cmd, "wt-evalverify-" + uuid.uuid4().hex[:8])
        verify_ok = bool(r.get("ok"))
        changed_files = _branch_changed_files(str(repo), base_branch, b0) or []
        delivered_commit = _rev(repo, b0)
        if scenario.acceptance:
            independent = verify_branch(str(repo), b0, scenario.acceptance,
                                        "wt-evalaccept-" + uuid.uuid4().hex[:8])
            acceptance.update(ok=bool(independent.get("ok")), output=str(independent.get("output") or ""))
            verify_ok = verify_ok and acceptance["ok"]

    facts = GitFacts(vorto_branches=branches, verify_ok=verify_ok, changed_files=changed_files,
                     worktree_clean=worktree_clean, only_main_worktree=only_main_worktree,
                     base_untouched=base_untouched)
    scored = score(scenario, message, facts, time.monotonic() - t0)
    scored.execution_error = execution_error
    if execution_error:
        scored.honest_reason = "执行失败，未完成验收：" + execution_error[:300]
    from src.models.cost import cost_for
    costs = [cost_for(model, bucket["prompt_tokens"], bucket["completion_tokens"])
             for model, bucket in used["by_model"].items()]
    known_cost = sum(c for c in costs if c is not None)
    pricing_complete = (all(c is not None for c in costs)
                        and sum(b["calls"] for b in used["by_model"].values()) == used["calls"])
    scored.evidence = {"execution_mode": "agent" if via_agent else "tool",
                       "base_commit": base_head, "delivered_commit": delivered_commit,
                       "execution_seconds": round(dt, 3),
                       "input": prompt if via_agent else scenario.args,
                       "usage": used, "cost": known_cost if pricing_complete else None,
                       "known_cost": known_cost, "pricing_complete": pricing_complete,
                       "tool_trace": trace, "progress": progress, "confirmations": approvals,
                       "human_interventions": 0, "confirmation_policy": "scratch-local-auto",
                       "independent_acceptance": acceptance,
                       "model": os.getenv("DEFAULT_MODEL") or os.getenv("OPENAI_MODEL") or "default",
                       "full_response": message}
    return scored, ""
