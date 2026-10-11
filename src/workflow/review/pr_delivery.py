"""Current-branch PR, review, and CI delivery state for Desktop."""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo_root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def current_pr_delivery(repo_root: str) -> dict:
    """Read the current local branch's GitHub PR and normalized checks."""
    from src.agents.vcs import pr_feedback

    root = Path(repo_root).resolve()
    top = _git(str(root), "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise ValueError("当前 Runtime 目录不是 Git 仓库")
    root = Path((top.stdout or "").strip()).resolve()
    branch_result = _git(str(root), "branch", "--show-current")
    branch = (branch_result.stdout or "").strip()
    if branch_result.returncode != 0 or not branch:
        return {
            "ok": False,
            "branch": "",
            "comments": [],
            "checks": [],
            "failing_checks": [],
            "summary": {"total": 0, "failed": 0, "pending": 0, "passed": 0},
            "error": "当前处于 detached HEAD，无法关联分支 PR",
        }
    result = pr_feedback(str(root), branch)
    result.setdefault("branch", branch)
    result.setdefault("checks", [])
    result.setdefault("comments", [])
    result.setdefault("failing_checks", [])
    result.setdefault("summary", {"total": 0, "failed": 0, "pending": 0, "passed": 0})
    return result


def current_failed_check_log(repo_root: str, check_id: str) -> dict:
    """Fetch one current failing check's log by server-issued id, never arbitrary user URL/run id."""
    from src.agents.vcs import failed_check_log_excerpts

    check_id = str(check_id or "").strip()
    if not check_id.startswith("check-") or len(check_id) > 32:
        raise ValueError("CI check id 无效")
    snapshot = current_pr_delivery(repo_root)
    if not snapshot.get("ok"):
        raise ValueError(snapshot.get("error") or "当前分支没有可读取的 PR")
    check = next(
        (item for item in snapshot.get("failing_checks") or [] if item.get("id") == check_id),
        None,
    )
    if check is None:
        raise ValueError("该检查已不在当前 PR 的失败列表中，请刷新")
    result = failed_check_log_excerpts(repo_root, [check], max_checks=1, max_chars=8_000)
    logs = result.get("logs") or []
    if not logs:
        return {"ok": False, "check_id": check_id, "log": None, "error": result.get("error") or "没有失败日志"}
    return {"ok": bool(result.get("ok")), "check_id": check_id, "log": logs[0], "error": result.get("error") or ""}
