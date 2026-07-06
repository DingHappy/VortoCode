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


def _path_matches(path: str, filters: list[str]) -> bool:
    if not filters:
        return True
    p = path.strip("/")
    for raw in filters:
        f = str(raw or "").strip().strip("/")
        if f and (p == f or p.startswith(f.rstrip("/") + "/")):
            return True
    return False


def _name_status_paths(output: str) -> list[dict]:
    files: list[dict] = []
    for line in (output or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[-1].strip()
        if path:
            files.append({"status": status, "path": path})
    return files


def _numstat_totals(output: str) -> tuple[int, int]:
    insertions = 0
    deletions = 0
    for line in (output or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            insertions += int(parts[0])
        except ValueError:
            pass
        try:
            deletions += int(parts[1])
        except ValueError:
            pass
    return insertions, deletions


def _is_test_path(path: str) -> bool:
    p = path.lower()
    name = Path(p).name
    return (
        p.startswith("tests/")
        or "/tests/" in p
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.js")
        or name.endswith(".test.ts")
        or name.endswith(".spec.js")
        or name.endswith(".spec.ts")
        or name.endswith("tests.swift")
    )


def _is_source_path(path: str) -> bool:
    if _is_test_path(path):
        return False
    return Path(path).suffix.lower() in {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".swift", ".go", ".rs", ".java",
        ".kt", ".rb", ".php", ".cs", ".c", ".cc", ".cpp", ".h", ".hpp",
    }


def _is_docs_path(path: str) -> bool:
    p = path.lower()
    return p.startswith("docs/") or Path(p).suffix in {".md", ".mdx", ".rst", ".txt"}


def _change_risks(paths: list[str], statuses: dict[str, str], insertions: int, deletions: int) -> list[str]:
    risks: list[str] = []
    total = insertions + deletions
    source_paths = [p for p in paths if _is_source_path(p)]
    test_paths = [p for p in paths if _is_test_path(p)]
    docs_only = bool(paths) and all(_is_docs_path(p) for p in paths)
    if source_paths and not test_paths:
        risks.append("源码有改动，但没有看到测试文件改动；合并前建议跑相关测试或补回归。")
    if len(paths) >= 20 or total >= 500:
        risks.append(f"改动规模偏大（{len(paths)} 个文件，+{insertions}/-{deletions}）；建议拆分提交或先做 targeted review。")
    if deletions >= 100 and deletions > insertions * 2:
        risks.append(f"删除量明显高于新增（+{insertions}/-{deletions}）；确认不是误删或生成物漂移。")
    sensitive = [
        p for p in paths
        if any(part in p.lower() for part in (
            "auth", "permission", "security", "secret", "token", "credential",
            "migration", "schema", "database", "deploy", ".github/workflows",
            "package-lock", "poetry.lock", "requirements", "pyproject.toml",
        ))
    ]
    if sensitive:
        shown = ", ".join(sensitive[:5])
        more = f" 等 {len(sensitive)} 个文件" if len(sensitive) > 5 else ""
        risks.append(f"涉及权限/配置/依赖/部署等高影响路径：{shown}{more}。")
    generated = [
        p for p in paths
        if any(part in p for part in (".build/", "node_modules/", "dist/", "__pycache__/", ".pytest_cache/"))
    ]
    if generated:
        risks.append("改动里包含构建缓存或生成目录；通常不应提交这些文件。")
    deleted = [p for p, st in statuses.items() if st.startswith("D")]
    if deleted and not docs_only:
        shown = ", ".join(deleted[:5])
        risks.append(f"包含删除文件：{shown}；确认调用点、文档和测试都已同步。")
    return risks


def change_review(repo_root: str, *, cached: bool = False, paths: list[str] | None = None) -> dict:
    """Build a local, deterministic pre-commit review summary for current changes."""
    filters = [str(p).strip() for p in (paths or []) if str(p).strip()]
    base_args = ["diff"]
    if cached:
        base_args.append("--cached")
    else:
        base_args.append("HEAD")
    if filters:
        base_args.append("--")
        base_args.extend(filters)
    name = _git(repo_root, *(base_args[:1] + ["--name-status"] + base_args[1:]))
    if name.returncode != 0:
        return {"ok": False, "error": (name.stderr or name.stdout or "git diff failed").strip()}
    num = _git(repo_root, *(base_args[:1] + ["--numstat"] + base_args[1:]))
    if num.returncode != 0:
        return {"ok": False, "error": (num.stderr or num.stdout or "git diff failed").strip()}
    files = _name_status_paths(name.stdout or "")
    statuses = {f["path"]: f["status"] for f in files}
    untracked: list[str] = []
    if not cached:
        info = status_summary(repo_root)
        for line in str(info.get("porcelain") or "").splitlines():
            if not line.startswith("?? "):
                continue
            p = line[3:].strip()
            if _path_matches(p, filters):
                untracked.append(p)
                statuses.setdefault(p, "??")
                files.append({"status": "??", "path": p})
    insertions, deletions = _numstat_totals(num.stdout or "")
    changed_paths = [f["path"] for f in files]
    return {
        "ok": True,
        "scope": "staged" if cached else "workspace",
        "paths": changed_paths,
        "statuses": statuses,
        "insertions": insertions,
        "deletions": deletions,
        "test_paths": [p for p in changed_paths if _is_test_path(p)],
        "source_paths": [p for p in changed_paths if _is_source_path(p)],
        "untracked": untracked,
        "risks": _change_risks(changed_paths, statuses, insertions, deletions),
    }


def format_change_review(review: dict) -> str:
    if not review.get("ok"):
        return f"变更审查失败: {review.get('error', '')}".rstrip()
    paths = review.get("paths") or []
    scope = "已 staged" if review.get("scope") == "staged" else "工作区"
    lines = [
        f"变更审查: {scope}",
        f"文件: {len(paths)} · +{review.get('insertions', 0)} / -{review.get('deletions', 0)}",
    ]
    if not paths:
        lines.append("")
        lines.append("没有可审查的改动。")
        return "\n".join(lines)
    statuses = review.get("statuses") or {}
    lines.append("")
    lines.append("文件概览:")
    for p in paths[:30]:
        lines.append(f"  {statuses.get(p, '?'):>3} {p}")
    if len(paths) > 30:
        lines.append(f"  ... 还有 {len(paths) - 30} 个文件")
    risks = review.get("risks") or []
    lines.append("")
    if risks:
        lines.append("需要留意:")
        lines.extend(f"- {r}" for r in risks)
    else:
        lines.append("未发现明显的提交前风险信号。")
    lines.append("")
    lines.append("建议下一步:")
    if review.get("test_paths"):
        lines.append("- 跑本次改动涉及的测试，确认测试仍然通过。")
    elif review.get("source_paths"):
        lines.append("- 先跑相关测试；如果行为有变化，补一条回归测试。")
    else:
        lines.append("- 文档/配置类改动为主，确认示例、路径和命令仍然准确。")
    lines.append("- 用 /diff stat 或 /diff 查看细节；确认后再 /commit。")
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
