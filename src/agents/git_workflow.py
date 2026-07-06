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


def current_branch(repo_root: str) -> str:
    r = _git(repo_root, "branch", "--show-current")
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def pr_preview(repo_root: str, *, base: str = "main", title: str = "") -> dict:
    """Build a conservative local PR preview without network access."""
    branch = current_branch(repo_root)
    if not branch:
        return {"ok": False, "error": "当前不在命名分支上（detached HEAD 无法直接开 PR）"}
    if branch in {"main", "master"}:
        return {"ok": False, "error": f"当前分支是 {branch}，请先切到功能分支再开 PR"}
    info = status_summary(repo_root)
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error", "git status failed")}
    if str(info.get("porcelain") or "").strip():
        return {"ok": False, "error": "工作区还有未提交改动；请先 /commit 或清理后再开 PR"}
    base = str(base or "main").strip()
    base_ref = _git(repo_root, "rev-parse", "--verify", f"{base}^{{commit}}")
    if base_ref.returncode != 0:
        return {"ok": False, "error": f"找不到 base 分支/引用 {base}"}
    commits_r = _git(repo_root, "log", "--pretty=%s", f"{base}..HEAD")
    commits = [ln.strip() for ln in (commits_r.stdout or "").splitlines() if ln.strip()]
    if not commits:
        return {"ok": False, "error": f"{branch} 相对 {base} 没有可开 PR 的提交"}
    stat = _git(repo_root, "diff", "--stat", f"{base}...HEAD")
    files = _git(repo_root, "diff", "--name-only", f"{base}...HEAD")
    default_title = title.strip() or commits[0]
    body_lines = [
        "## Summary",
        *[f"- {c}" for c in commits[:12]],
        "",
        "## Changed Files",
        *[f"- {p}" for p in (files.stdout or "").splitlines()[:30] if p.strip()],
    ]
    if stat.stdout.strip():
        body_lines.extend(["", "## Diffstat", "```", stat.stdout.strip(), "```"])
    return {
        "ok": True,
        "branch": branch,
        "base": base,
        "title": default_title,
        "body": "\n".join(body_lines).strip(),
        "commits": commits,
        "changed_files": [p for p in (files.stdout or "").splitlines() if p.strip()],
        "diff_stat": stat.stdout.strip(),
    }


def format_pr_preview(preview: dict) -> str:
    if not preview.get("ok"):
        return f"PR 预览失败: {preview.get('error', '')}".rstrip()
    lines = [
        f"PR 预览: {preview.get('branch')} → {preview.get('base')}",
        f"title: {preview.get('title')}",
        "",
        "commits:",
    ]
    lines.extend(f"  - {c}" for c in preview.get("commits", [])[:12])
    files = preview.get("changed_files") or []
    if files:
        lines.extend(["", "changed files:"])
        lines.extend(f"  - {p}" for p in files[:30])
    return "\n".join(lines)
