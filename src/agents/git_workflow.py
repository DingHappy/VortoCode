"""Small git workflow helpers used by TUI commands.

These helpers intentionally expose only fixed git argv shapes. The TUI should
not pass arbitrary git flags through user input for write operations.
"""
from __future__ import annotations

import re
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
        root = Path(repo_root)
        for line in str(info.get("porcelain") or "").splitlines():
            if not line.startswith("?? "):
                continue
            raw = line[3:].strip()
            full = root / raw
            if full.is_dir():
                candidates = [
                    str(p.relative_to(root))
                    for p in full.rglob("*")
                    if p.is_file()
                ]
            else:
                candidates = [raw]
            for p in candidates:
                if not _path_matches(p, filters):
                    continue
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


def _candidate_test_paths_for_source(repo_root: str, source_path: str) -> list[str]:
    root = Path(repo_root)
    p = Path(source_path)
    stem = p.stem
    candidates = [
        root / "tests" / "unit" / f"test_{stem}.py",
        root / "tests" / f"test_{stem}.py",
        root / "tests" / "unit" / f"{stem}_test.py",
        root / "tests" / f"{stem}_test.py",
    ]
    found: list[str] = []
    for c in candidates:
        if c.is_file():
            found.append(str(c.relative_to(root)))
    tests_dir = root / "tests"
    if tests_dir.is_dir():
        for c in tests_dir.rglob(f"test_{stem}.py"):
            rel = str(c.relative_to(root))
            if rel not in found:
                found.append(rel)
        for c in tests_dir.rglob(f"{stem}_test.py"):
            rel = str(c.relative_to(root))
            if rel not in found:
                found.append(rel)
    return found


def changed_test_selection(repo_root: str, *, cached: bool = False, max_selectors: int = 8) -> dict:
    """Infer a small pytest selector set from changed files.

    This is intentionally deterministic and conservative: if we cannot map source
    files to tests, callers should fall back to the repository's normal test command.
    """
    review = change_review(repo_root, cached=cached)
    if not review.get("ok"):
        return {"ok": False, "error": review.get("error", "git diff failed"), "selectors": []}
    paths = list(review.get("paths") or [])
    if not paths:
        return {
            "ok": True,
            "scope": review.get("scope"),
            "changed_paths": [],
            "selectors": [],
            "fallback_full": False,
            "reason": "没有改动可验证。",
        }
    selectors: list[str] = []
    for p in paths:
        if _is_test_path(p) and Path(repo_root, p).is_file():
            selectors.append(p)
    for p in paths:
        if not _is_source_path(p):
            continue
        for c in _candidate_test_paths_for_source(repo_root, p):
            selectors.append(c)
    deduped: list[str] = []
    for s in selectors:
        if s not in deduped:
            deduped.append(s)
    truncated = len(deduped) > max_selectors
    deduped = deduped[:max_selectors]
    source_paths = review.get("source_paths") or []
    if deduped:
        reason = f"根据 {len(paths)} 个改动文件推断出 {len(deduped)} 个相关测试。"
        if truncated:
            reason += f" 结果较多，仅取前 {max_selectors} 个。"
        return {
            "ok": True,
            "scope": review.get("scope"),
            "changed_paths": paths,
            "selectors": deduped,
            "fallback_full": False,
            "reason": reason,
        }
    return {
        "ok": True,
        "scope": review.get("scope"),
        "changed_paths": paths,
        "selectors": [],
        "fallback_full": bool(source_paths),
        "reason": "没有找到可直接映射的测试文件；将回退到仓库默认测试命令。" if source_paths
        else "改动不是源码/测试文件；可按需运行默认测试命令。",
    }


def suggest_commit_message(repo_root: str, *, stage_all: bool = False) -> dict:
    """Suggest a deterministic conventional-ish commit message from current changes."""
    review = change_review(repo_root, cached=not stage_all)
    if not review.get("ok"):
        return {"ok": False, "error": review.get("error", "git diff failed")}
    paths = list(review.get("paths") or [])
    if not paths and not stage_all:
        return {"ok": False, "error": "没有 staged 改动可生成提交信息"}
    if stage_all and not paths:
        review = change_review(repo_root, cached=False)
        paths = list(review.get("paths") or [])
    if not paths:
        return {"ok": False, "error": "没有工作区改动可生成提交信息"}
    scopes = []
    for p in paths:
        parts = Path(p).parts
        if len(parts) >= 2 and parts[0] in {"src", "tests", "docs"}:
            scopes.append(parts[1] if parts[0] != "tests" else "tests")
        elif parts:
            scopes.append(parts[0].lstrip("."))
    scope = scopes[0] if scopes else ""
    if len(set(scopes)) > 1:
        scope = ""
    statuses = review.get("statuses") or {}
    has_tests = bool(review.get("test_paths"))
    has_src = bool(review.get("source_paths"))
    docs_only = all(_is_docs_path(p) for p in paths)
    deletes = any(str(statuses.get(p, "")).startswith("D") for p in paths)
    if docs_only:
        typ = "docs"
        action = "update documentation"
    elif has_tests and not has_src:
        typ = "test"
        action = "update tests"
    elif deletes:
        typ = "refactor"
        action = "remove obsolete code"
    elif has_src:
        typ = "feat" if any(str(statuses.get(p, "")).startswith(("A", "??")) for p in paths) else "fix"
        action = f"update {scope or 'implementation'}"
    else:
        typ = "chore"
        action = f"update {scope or 'project files'}"
    prefix = f"{typ}({scope})" if scope else typ
    return {"ok": True, "message": f"{prefix}: {action}", "paths": paths, "review": review}


def preflight_report(repo_root: str, *, cached: bool = False) -> dict:
    """Build a local preflight summary before committing or opening a PR."""
    review = change_review(repo_root, cached=cached)
    if not review.get("ok"):
        return {"ok": False, "error": review.get("error", "git diff failed")}
    status = status_summary(repo_root)
    tests = changed_test_selection(repo_root, cached=cached)
    commit = suggest_commit_message(repo_root, stage_all=not cached)
    try:
        from src.agents.verify_profiles import recommend_verify_profiles
        verify_profiles = recommend_verify_profiles(repo_root, review.get("paths") or [])
    except Exception as e:  # noqa: BLE001
        verify_profiles = {"ok": False, "error": f"verify profile 推荐失败: {e}", "recommendations": []}
    return {
        "ok": True,
        "scope": "staged" if cached else "workspace",
        "status": status,
        "review": review,
        "tests": tests,
        "verify_profiles": verify_profiles,
        "commit": commit,
    }


def format_preflight_report(report: dict) -> str:
    if not report.get("ok"):
        return f"Preflight 失败: {report.get('error', '')}".rstrip()
    review = report.get("review") or {}
    tests = report.get("tests") or {}
    verify_profiles = report.get("verify_profiles") or {}
    commit = report.get("commit") or {}
    status = report.get("status") or {}
    scope = "已 staged" if report.get("scope") == "staged" else "工作区"
    paths = review.get("paths") or []
    lines = [
        f"Preflight: {scope}",
        f"分支: {status.get('branch') or '(unknown)'}",
        f"改动: {len(paths)} 个文件 · +{review.get('insertions', 0)} / -{review.get('deletions', 0)}",
    ]
    if not paths:
        lines.append("")
        lines.append("没有可检查的改动。")
        return "\n".join(lines)
    risks = review.get("risks") or []
    lines.append("")
    if risks:
        lines.append("风险信号:")
        lines.extend(f"- {r}" for r in risks[:8])
    else:
        lines.append("风险信号: 未发现明显提交前风险。")
    review_cmd = "/review" + (" cached" if report.get("scope") == "staged" else "")
    fix_cmd = "/review --fix" + (" cached" if report.get("scope") == "staged" else "")
    lines.append("")
    lines.append("建议审查:")
    lines.append(f"- {review_cmd}（只报 P0/P1；确认需要修复时用 {fix_cmd}）")
    selectors = tests.get("selectors") or []
    profile_recs = verify_profiles.get("recommendations") or []
    lines.append("")
    lines.append("建议验证:")
    if profile_recs:
        lines.append("- 推荐 profile:")
        for rec in profile_recs[:5]:
            reason = f" · {rec.get('reason')}" if rec.get("reason") else ""
            lines.append(f"  · /verify {rec.get('name')}{reason}")
    elif verify_profiles and not verify_profiles.get("ok"):
        lines.append(f"- verify profile 推荐不可用: {verify_profiles.get('error', '')}")
    if selectors:
        lines.append("- /verify --changed" + (" cached" if report.get("scope") == "staged" else ""))
        lines.extend(f"  · {s}" for s in selectors[:8])
    elif tests.get("changed_paths"):
        lines.append("- /verify --changed" + (" cached" if report.get("scope") == "staged" else ""))
        lines.append(f"  · {tests.get('reason')}")
    else:
        lines.append(f"- {tests.get('reason') or '没有改动可验证。'}")
    lines.append("")
    if commit.get("ok"):
        cmd = "/commit --suggest" if report.get("scope") == "staged" else "/commit all --suggest"
        lines.append(f"建议提交: {commit.get('message')}")
        lines.append(f"下一步: {cmd}")
    else:
        lines.append(f"建议提交: 暂不可生成（{commit.get('error', '')}）")
    lines.append("发布前: /diff stat 复核范围；功能分支上再 /pr preview。")
    return "\n".join(lines)


def _diff_payload(repo_root: str, *, cached: bool = False,
                  paths: list[str] | None = None) -> dict:
    filters = [str(p).strip() for p in (paths or []) if str(p).strip()]
    review = change_review(repo_root, cached=cached, paths=filters)
    if not review.get("ok"):
        return {"ok": False, "error": review.get("error", "git diff failed")}
    if not review.get("paths"):
        return {"ok": False, "error": "没有可审查的改动"}
    cmd = ["diff"]
    if cached:
        cmd.append("--cached")
    else:
        cmd.append("HEAD")
    if filters:
        cmd.append("--")
        cmd.extend(filters)
    r = _git(repo_root, *cmd, timeout=30)
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout or "git diff failed").strip()}
    return {
        "ok": True,
        "scope": "staged" if cached else "workspace",
        "diff": r.stdout or "",
        "review": review,
    }


_HUNK_RE = re.compile(
    r"@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<section>.*)"
)


def _parse_hunks(diff: str) -> list[dict]:
    hunks: list[dict] = []
    current_file = ""
    current: dict | None = None
    headers: list[str] = []
    for line in (diff or "").splitlines():
        if line.startswith("diff --git "):
            if current is not None:
                current["diff"] = "\n".join(headers + current["lines"])
                hunks.append(current)
                current = None
            headers = [line]
            parts = line.split()
            if len(parts) >= 4:
                current_file = parts[3][2:] if parts[3].startswith("b/") else parts[3]
            else:
                current_file = ""
            continue
        if line.startswith(("index ", "new file mode ", "deleted file mode ", "similarity index ",
                            "rename from ", "rename to ", "--- ", "+++ ")):
            headers.append(line)
            if line.startswith("+++ ") and line != "+++ /dev/null":
                p = line[4:].strip()
                current_file = p[2:] if p.startswith("b/") else p
            continue
        if line.startswith("@@ "):
            if current is not None:
                current["diff"] = "\n".join(headers + current["lines"])
                hunks.append(current)
            match = _HUNK_RE.match(line)
            old_start = int(match.group("old_start")) if match else 0
            new_start = int(match.group("new_start")) if match else 0
            current = {
                "id": f"H{len(hunks) + 1}",
                "path": current_file,
                "header": line,
                "old_start": old_start,
                "new_start": new_start,
                "section": (match.group("section").strip() if match else ""),
                "lines": [line],
            }
            continue
        if current is not None:
            current["lines"].append(line)
    if current is not None:
        current["diff"] = "\n".join(headers + current["lines"])
        hunks.append(current)
    return hunks


def diff_hunks_for_review(repo_root: str, *, cached: bool = False,
                          paths: list[str] | None = None, limit: int = 30000) -> dict:
    """Return parsed git diff hunks with stable H1/H2 ids for the current diff."""
    payload = _diff_payload(repo_root, cached=cached, paths=paths)
    if not payload.get("ok"):
        return payload
    hunks = _parse_hunks(str(payload.get("diff") or ""))
    for h in hunks:
        body = str(h.get("diff") or "")
        h["truncated"] = len(body) > limit
        if h["truncated"]:
            h["diff"] = body[:limit] + "\n...(hunk truncated)"
    return {
        "ok": True,
        "scope": payload.get("scope"),
        "hunks": hunks,
        "review": payload.get("review"),
    }


def format_diff_hunks(payload: dict) -> str:
    if not payload.get("ok"):
        return f"diff hunk 解析失败: {payload.get('error', '')}".rstrip()
    hunks = payload.get("hunks") or []
    scope = "已 staged" if payload.get("scope") == "staged" else "工作区"
    if not hunks:
        return f"{scope} 没有可审查的 diff hunk。"
    lines = [f"Diff hunks: {scope} · {len(hunks)} 个 hunk"]
    for h in hunks[:80]:
        loc = f"+{h.get('new_start', 0)}" if h.get("new_start") else "new"
        lines.append(f"- {h.get('id')} {h.get('path')}:{loc} {h.get('header')}")
    if len(hunks) > 80:
        lines.append(f"... 还有 {len(hunks) - 80} 个 hunk")
    lines.append("")
    lines.append("下一步: /review hunk H1 或 /review --fix hunk H1")
    return "\n".join(lines)


def diff_for_review(repo_root: str, *, cached: bool = False,
                    paths: list[str] | None = None, hunk_id: str = "",
                    limit: int = 30000) -> dict:
    """Return a bounded git diff payload suitable for LLM review."""
    if hunk_id:
        payload = diff_hunks_for_review(repo_root, cached=cached, paths=paths, limit=limit)
        if not payload.get("ok"):
            return payload
        target = str(hunk_id).strip().upper()
        for h in payload.get("hunks") or []:
            if str(h.get("id")).upper() == target:
                return {
                    "ok": True,
                    "scope": payload.get("scope"),
                    "diff": h.get("diff") or "",
                    "truncated": bool(h.get("truncated")),
                    "review": payload.get("review"),
                    "hunk": h,
                }
        return {"ok": False, "error": f"找不到 diff hunk {hunk_id}；先用 /diff hunks 查看当前编号"}
    payload = _diff_payload(repo_root, cached=cached, paths=paths)
    if not payload.get("ok"):
        return payload
    diff = str(payload.get("diff") or "")
    truncated = len(diff) > limit
    if truncated:
        diff = diff[:limit] + "\n...(diff truncated)"
    return {
        "ok": True,
        "scope": payload.get("scope"),
        "diff": diff,
        "truncated": truncated,
        "review": payload.get("review"),
    }


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
