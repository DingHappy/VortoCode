"""任务小组开发成员的工作区与交付（ADR-014 S2）。

一轮开发 = 在登记项目里建一个一次性 worktree → 外部框架在里面改代码（不执行命令、不联网）→
VortoOne 收集改动、在 OS 沙箱里跑项目的验证配置 → 改动落到一个新分支 → 删掉 worktree。

- worktree 只含被跟踪的文件：**不**按 ``.worktreeinclude`` 复制 ``.env`` 一类本地文件（框架背后的模型能读到
  目录里的一切，密钥不能进去）。返工轮在上一轮的分支内容上继续。
- 交付只是项目仓库里的一个本地分支：不推送、不开 PR、不合并，由主人审。
- 验证用 ``worktree.run_tests``（与隔离开发流水线同一道沙箱闸）；项目没有匹配的验证配置就如实写「没跑」。
- 这里的 git / 测试都是阻塞调用，调用方放进线程里跑。
"""
from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from src.utils.state_dir import state_dir

_OUTPUT_TAIL = 1500
_MAX_FILES_SHOWN = 20


def branch_name(child_id: str, number: int) -> str:
    return f"vorto/team-{child_id}-r{number}"


def _git(cwd: Any, *args: str, check: bool = True, stdin: Optional[str] = None) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", "-C", str(cwd), *args], input=stdin, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {args[0]} 失败：{(result.stderr or result.stdout).strip()[:300]}")
    return result


def prepare(repo_root: str, child_id: str, number: int, previous_branch: str = "") -> Path:
    """建本轮的一次性 worktree（基于项目当前 HEAD）；返工轮先放上上一轮分支的改动。"""
    from src.utils.state_dir import ensure_state_gitignore
    ensure_state_gitignore(repo_root)
    path = state_dir(repo_root) / "worktrees" / f"team-{child_id}-r{number}"
    if path.exists():
        cleanup(repo_root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "worktree", "add", "--detach", str(path), "HEAD")
    if previous_branch:
        try:
            diff = _git(repo_root, "diff", "--binary", f"HEAD...{previous_branch}").stdout
            if diff.strip():
                _git(path, "apply", "--3way", "--whitespace=nowarn", stdin=diff)
        except Exception:
            cleanup(repo_root, path)
            raise
    return path


def cleanup(repo_root: str, path: Any) -> None:
    from src.agents.worktree import remove_worktree
    remove_worktree(repo_root, path)


def _verify_command(repo_root: str, files: List[str]) -> Optional[List[str]]:
    from src.agents.verify_profiles import recommend_verify_profiles
    from src.utils.python_exe import pytest_argv
    picked = recommend_verify_profiles(repo_root, files)
    if not picked.get("ok") or not picked.get("recommendations"):
        return None
    raw = str(picked["recommendations"][0].get("cmd") or "").strip()
    if not raw:
        return None
    argv = shlex.split(raw)
    if argv[0] == "pytest":
        return pytest_argv(*argv[1:])
    if argv[:3] == ["python", "-m", "pytest"]:
        return pytest_argv(*argv[3:])
    return argv


_SUMMARY_COUNT = re.compile(r"(\d+) (?:failed|errors?)\b")
_SUMMARY_LINE = re.compile(r"\d+ (?:failed|passed|errors?)\b.* in [\d.]+s")


def _verify(repo_root: str, path: Any, cmd: List[str], run_tests: Any = None) -> Dict[str, Any]:
    """跑验证；红了先原样重跑一次（排除不稳定的），再把仍失败的拿到改动前的代码上对照（排除本来就红的）。

    与隔离开发流水线同一套判定（``worktree.retry_failed`` / ``compare_with_base``）。解析出的失败数和 pytest
    汇总对不上（输出被截断等）时不改判，宁可误判红。"""
    from src.agents import worktree
    if run_tests is not None:                       # 测试注入：不做对照
        return run_tests(path, cmd)
    base = _git(path, "rev-parse", "HEAD").stdout.strip()
    result = worktree.run_tests(path, cmd, output_limit=40000)
    if result.get("ok"):
        return result
    output = str(result.get("output") or "")
    summary = next((line for line in reversed(output.splitlines()) if _SUMMARY_LINE.search(line)), "")
    reported = sum(int(n) for n in _SUMMARY_COUNT.findall(summary))
    if reported and reported != len(worktree.failed_test_ids(output)):
        return result
    judged = worktree.compare_with_base(repo_root, base, worktree.retry_failed(path, result))
    return judged


def finish_round(repo_root: str, path: Any, *, child_id: str, number: int, objective: str,
                 run_tests: Any = None) -> Dict[str, Any]:
    """收集改动、跑验证、落分支。返回交付记录（不抛：失败写在 ``error`` 里）。"""
    from src.agents import worktree
    diff = worktree.collect_diff(path)
    if not diff.strip():
        return {"round": number, "files": [], "error": "成员没有改动任何文件"}
    files = list(dict.fromkeys(worktree.diff_paths(diff)))
    delivery: Dict[str, Any] = {"round": number, "files": files}
    cmd = _verify_command(repo_root, files)
    if cmd is None:
        delivery.update(verified=None, verify_cmd="", verify_output="项目没有匹配这些文件的验证配置，没有跑测试")
    else:
        outcome = _verify(repo_root, path, cmd, run_tests)
        delivery.update(verified=bool(outcome.get("ok")), verify_cmd=str(outcome.get("cmd") or " ".join(cmd)),
                        verify_output=str(outcome.get("output") or "")[-_OUTPUT_TAIL:])
        preexisting = (outcome.get("baseline") or {}).get("preexisting") or []
        if preexisting:
            delivery["preexisting_failures"] = len(preexisting)
        if outcome.get("flaky"):
            delivery["flaky"] = len(outcome["flaky"])
    branch = branch_name(child_id, number)
    message = f"team({child_id}): 第 {number} 轮\n\n{objective[:500]}"
    landed = worktree.apply_diff_to_branch(repo_root, branch, diff, message)
    if not landed.get("ok"):
        delivery["error"] = f"改动没能落到分支：{landed.get('error')}"
        return delivery
    delivery["branch"] = branch
    delivery['head'] = _git(repo_root, 'rev-parse', '--verify', f'refs/heads/{branch}').stdout.strip()
    return delivery


def branch_diff(repo_root: str, branch: str, limit: Optional[int] = 12000) -> str:
    """默认保留旧预览；完整文件读取必须成功，不能把读取失败报告成没有改动。"""
    diff = _git(repo_root, "diff", f"HEAD...{branch}", check=limit is None).stdout
    if limit is None or len(diff) <= limit:
        return diff or "（没有改动）"
    return diff[:limit] + f"\n…（改动太长，只给了前 {limit} 个字符）"


def describe(delivery: Dict[str, Any], project: str) -> str:
    """给协调人看的交付说明（代码拼的事实）。"""
    if delivery.get("error"):
        return f"【交付】没有交付：{delivery['error']}"
    files = delivery.get("files") or []
    shown = "、".join(files[:_MAX_FILES_SHOWN]) + (f" 等 {len(files)} 个" if len(files) > _MAX_FILES_SHOWN else "")
    verified = delivery.get("verified")
    status = "通过" if verified is True else "没通过" if verified is False else "没跑"
    lines = [f"【交付】分支 {delivery.get('branch')}（常驻电脑上 {project} 仓库的本地分支，未推送、未合并）",
             f"改动文件（{len(files)}）：{shown}",
             f"验证：{status}" + (f"（{delivery['verify_cmd']}）" if delivery.get("verify_cmd") else "")
             + (f"；另有 {delivery['preexisting_failures']} 个测试在改动前的代码上同样失败，不计入本次改动"
                if delivery.get("preexisting_failures") else "")
             + (f"；{delivery['flaky']} 个测试第一次失败、重跑通过（不稳定）" if delivery.get("flaky") else "")]
    if verified is not True and delivery.get("verify_output"):
        lines.append("验证输出（末尾）：\n" + str(delivery["verify_output"]))
    return "\n".join(lines)
