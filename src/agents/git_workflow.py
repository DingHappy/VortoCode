"""Small git workflow helpers used by TUI commands.

These helpers intentionally expose only fixed git argv shapes. The TUI should
not pass arbitrary git flags through user input for write operations.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo_root: str, *args: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _changed_paths(porcelain: str) -> list[str]:
    paths: list[str] = []
    for line in (porcelain or "").splitlines():
        if not line or line.startswith("## "):
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path:
            paths.append(path)
    return paths


def status_summary(repo_root: str) -> dict:
    """Return a compact, read-only git status summary."""
    status = _git(repo_root, "status", "-sb", "--porcelain")
    if status.returncode != 0:
        return {"ok": False, "error": (status.stderr or status.stdout or "git status failed").strip()}
    diff_stat = _git(repo_root, "diff", "--stat")
    cached_stat = _git(repo_root, "diff", "--cached", "--stat")
    lines = (status.stdout or "").splitlines()
    branch = lines[0][3:].strip() if lines and lines[0].startswith("## ") else ""
    porcelain = "\n".join(lines[1:] if branch else lines)
    return {
        "ok": True,
        "branch": branch,
        "porcelain": porcelain,
        "paths": _changed_paths(porcelain),
        "diff_stat": (diff_stat.stdout or "").strip(),
        "cached_stat": (cached_stat.stdout or "").strip(),
    }


def format_status_summary(info: dict) -> str:
    if not info.get("ok"):
        return f"git status 失败: {info.get('error', '')}".rstrip()
    lines = [f"Git 状态: {info.get('branch') or '(unknown branch)'}"]
    porcelain = str(info.get("porcelain") or "").strip()
    if porcelain:
        lines.append("\n改动文件:")
        lines.extend(f"  {line}" for line in porcelain.splitlines())
    else:
        lines.append("\n工作区干净。")
    cached = str(info.get("cached_stat") or "").strip()
    unstaged = str(info.get("diff_stat") or "").strip()
    if cached:
        lines.append("\n已 staged diffstat:")
        lines.append(cached)
    if unstaged:
        lines.append("\n未 staged diffstat:")
        lines.append(unstaged)
    return "\n".join(lines)


def has_staged_changes(repo_root: str) -> bool:
    return _git(repo_root, "diff", "--cached", "--quiet").returncode == 1


def has_any_changes(repo_root: str) -> bool:
    info = status_summary(repo_root)
    return bool(info.get("ok") and info.get("porcelain"))


def commit_changes(repo_root: str, message: str, *, stage_all: bool = False) -> dict:
    """Create a local git commit.

    `stage_all=False` commits only already staged changes. `stage_all=True`
    first runs `git add -A`, then commits. Returns a small structured result.
    """
    msg = str(message or "").strip()
    if not msg:
        return {"ok": False, "error": "commit message is required"}
    if not (Path(repo_root) / ".git").exists():
        return {"ok": False, "error": "not a git repository"}
    if stage_all:
        add = _git(repo_root, "add", "-A")
        if add.returncode != 0:
            return {"ok": False, "error": "git add 失败: " + (add.stderr or add.stdout or "").strip()}
    if not has_staged_changes(repo_root):
        return {"ok": False, "error": "没有 staged 改动可提交（用 /commit all <message> 可先 git add -A）"}
    commit = _git(repo_root, "commit", "-m", msg, timeout=60)
    if commit.returncode != 0:
        return {"ok": False, "error": (commit.stderr or commit.stdout or "git commit failed").strip()}
    sha = _git(repo_root, "rev-parse", "--short", "HEAD")
    return {
        "ok": True,
        "sha": (sha.stdout or "").strip(),
        "output": (commit.stdout or commit.stderr or "").strip(),
    }
