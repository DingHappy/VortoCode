"""Safe, structured Git review operations for Desktop.

Only fixed git argv shapes are exposed.  Hunk mutations are guarded by a hash of
the exact patch shown to the user, so a stale Desktop cannot stage or revert a
different hunk after the worktree changes underneath it.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Optional

from src.agents.git_workflow import _parse_hunks
from src.gateway.change_sources import attribute_hunks, stable_hunk_id

_VALID_SCOPES = frozenset({"working", "staged"})
_VALID_ACTIONS = frozenset({"stage", "unstage", "revert"})
_MAX_FILES = 2_000
_MAX_DIFF_CHARS = 2_000_000
_BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
_PROTECTED_BRANCHES = frozenset({"main", "master", "develop", "development"})


def _git(
    repo_root: str,
    *args: str,
    input_text: Optional[str] = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _repo_root(repo_root: str) -> str:
    root = Path(repo_root).resolve()
    result = _git(str(root), "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise ValueError("当前 Runtime 目录不是 Git 仓库")
    return str(Path(result.stdout.strip()).resolve())


def normalize_git_path(path: str) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw or "\x00" in raw or "\n" in raw or "\r" in raw:
        raise ValueError("Git 文件路径为空或包含不支持的字符")
    candidate = PurePosixPath(raw)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("Git 文件路径必须是仓库内的规范相对路径")
    return candidate.as_posix()


def _status_entries(repo_root: str) -> tuple[list[dict], bool]:
    result = _git(repo_root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if result.returncode != 0:
        raise ValueError((result.stderr or result.stdout or "git status 失败").strip())
    chunks = (result.stdout or "").split("\0")
    entries = []
    index = 0
    while index < len(chunks):
        raw = chunks[index]
        index += 1
        if not raw:
            continue
        if len(raw) < 4:
            continue
        xy = raw[:2]
        path = raw[3:]
        original = ""
        if (xy[0] in {"R", "C"} or xy[1] in {"R", "C"}) and index < len(chunks):
            original = chunks[index]
            index += 1
        try:
            path = normalize_git_path(path)
            original = normalize_git_path(original) if original else ""
        except ValueError:
            continue
        index_status, worktree_status = xy[0], xy[1]
        untracked = xy == "??"
        entries.append({
            "path": path,
            "original_path": original,
            "status": xy,
            "index_status": index_status,
            "worktree_status": worktree_status,
            "staged": not untracked and index_status not in {" ", "?"},
            "unstaged": untracked or worktree_status not in {" ", "?"},
            "untracked": untracked,
            "conflicted": "U" in xy or xy in {"AA", "DD"},
        })
        if len(entries) >= _MAX_FILES:
            return entries, True
    return entries, False


def review_snapshot(repo_root: str) -> dict:
    root = _repo_root(repo_root)
    entries, truncated = _status_entries(root)
    branch = _git(root, "branch", "--show-current")
    head = _git(root, "rev-parse", "--short", "HEAD")
    return {
        "ok": True,
        "root": root,
        "branch": (branch.stdout or "").strip() if branch.returncode == 0 else "",
        "head": (head.stdout or "").strip() if head.returncode == 0 else "",
        "files": entries,
        "truncated": truncated,
    }


def review_watch_state(repo_root: str) -> dict:
    """Build a cheap repo-level invalidation token for realtime review clients.

    Exact hunk operations remain guarded by their patch SHA-256.  This token is
    only a wake-up signal: it combines Git status, HEAD, index metadata,
    worktree file metadata, and the private provenance-ledger revision so the
    Desktop can immediately re-fetch authoritative diffs after external edits.
    """
    root = _repo_root(repo_root)
    entries, truncated = _status_entries(root)
    head = _git(root, "rev-parse", "HEAD")
    head_oid = (head.stdout or "").strip() if head.returncode == 0 else ""
    # Do not use the index file's mtime here: a read-only ``git status`` may
    # refresh cached stat data and touch the index, which would make our own
    # poll trigger the next poll forever. ``ls-files --stage`` represents the
    # logical index (including conflict stages) and stays stable across reads.
    index_result = _git(root, "ls-files", "--stage", "-z")
    index_token = hashlib.sha256(
        (index_result.stdout or "").encode("utf-8", errors="surrogatepass")
    ).hexdigest() if index_result.returncode == 0 else ""

    def stat_token(path: Path) -> tuple[int, int, int]:
        try:
            stat = path.lstat()
            return stat.st_mtime_ns, stat.st_size, stat.st_mode
        except OSError:
            return 0, -1, 0

    material = {
        "head": head_oid,
        "index": index_token,
        "files": [{
            "path": item["path"],
            "status": item["status"],
            "stat": stat_token(Path(root) / item["path"]),
        } for item in entries],
    }
    from src.gateway.change_sources import change_source_revision
    source_revision = change_source_revision(root)
    baseline = hashlib.sha256(
        (json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
         + "\0" + source_revision).encode("utf-8")
    ).hexdigest()
    return {
        "baseline": baseline,
        "source_revision": source_revision,
        "head": head_oid,
        "files": len(entries),
        "paths": [item["path"] for item in entries[:100]],
        "truncated": truncated or len(entries) > 100,
    }


def _entry_for_path(repo_root: str, path: str) -> Optional[dict]:
    entries, _truncated = _status_entries(repo_root)
    return next((item for item in entries if item["path"] == path), None)


def _raw_diff(repo_root: str, path: str, scope: str, entry: Optional[dict]) -> str:
    if scope == "working" and entry and entry.get("untracked"):
        result = _git(
            repo_root,
            "diff", "--no-index", "--no-ext-diff", "--unified=3", "--", "/dev/null", path,
        )
        if result.returncode not in {0, 1}:
            raise ValueError((result.stderr or result.stdout or "读取未跟踪文件 diff 失败").strip())
    else:
        args = ["diff", "--no-ext-diff", "--unified=3"]
        if scope == "staged":
            args.append("--cached")
        args.extend(["--", path])
        result = _git(repo_root, *args)
        if result.returncode != 0:
            raise ValueError((result.stderr or result.stdout or "git diff 失败").strip())
    diff = result.stdout or ""
    if len(diff) > _MAX_DIFF_CHARS:
        raise ValueError("单文件 diff 超过 2,000,000 字符，请在外部编辑器审查")
    return diff


def _line_items(hunk: dict) -> list[dict]:
    old_line = int(hunk.get("old_start") or 0)
    new_line = int(hunk.get("new_start") or 0)
    items = []
    for index, text in enumerate(hunk.get("lines") or []):
        if index == 0 or str(text).startswith("@@"):
            items.append({"kind": "header", "text": text, "old_line": None, "new_line": None})
        elif str(text).startswith("+") and not str(text).startswith("+++"):
            items.append({"kind": "add", "text": text, "old_line": None, "new_line": new_line})
            new_line += 1
        elif str(text).startswith("-") and not str(text).startswith("---"):
            items.append({"kind": "remove", "text": text, "old_line": old_line, "new_line": None})
            old_line += 1
        elif str(text).startswith(" "):
            items.append({"kind": "context", "text": text, "old_line": old_line, "new_line": new_line})
            old_line += 1
            new_line += 1
        else:
            items.append({"kind": "meta", "text": text, "old_line": None, "new_line": None})
    return items


def _diff_baseline(repo_root: str, path: str, scope: str, diff: str) -> tuple[str, str]:
    head = _git(repo_root, "rev-parse", "HEAD")
    head_oid = (head.stdout or "").strip() if head.returncode == 0 else ""
    baseline = hashlib.sha256(
        f"{head_oid}\0{scope}\0{path}\0{diff}".encode("utf-8")
    ).hexdigest()
    return baseline, head_oid


def review_diff(repo_root: str, path: str, *, scope: str = "working") -> dict:
    root = _repo_root(repo_root)
    path = normalize_git_path(path)
    scope = str(scope or "working").strip().lower()
    if scope not in _VALID_SCOPES:
        raise ValueError("Diff scope 只支持 working / staged")
    entry = _entry_for_path(root, path)
    if entry is None:
        raise ValueError("文件已不在 Git 改动列表中，请刷新")
    if scope == "working" and not entry.get("unstaged"):
        baseline, head_oid = _diff_baseline(root, path, scope, "")
        return {"ok": True, "path": path, "scope": scope, "hunks": [], "binary": False,
                "diff": "", "baseline": baseline, "head": head_oid}
    if scope == "staged" and not entry.get("staged"):
        baseline, head_oid = _diff_baseline(root, path, scope, "")
        return {"ok": True, "path": path, "scope": scope, "hunks": [], "binary": False,
                "diff": "", "baseline": baseline, "head": head_oid}
    diff = _raw_diff(root, path, scope, entry)
    hunks = _parse_hunks(diff)
    serialized = []
    for hunk in hunks:
        patch = str(hunk.get("diff") or "") + "\n"
        raw_lines = [str(line) for line in (hunk.get("lines") or [])]
        serialized.append({
            "id": stable_hunk_id(path, str(hunk.get("section") or ""), raw_lines),
            "path": path,
            "header": str(hunk.get("header") or ""),
            "old_start": int(hunk.get("old_start") or 0),
            "new_start": int(hunk.get("new_start") or 0),
            "section": str(hunk.get("section") or ""),
            "sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
            "lines": _line_items(hunk),
            "raw_lines": raw_lines,
        })
    for item, source in zip(serialized, attribute_hunks(root, path, serialized)):
        item.update(source)
        item.pop("raw_lines", None)
    baseline, head_oid = _diff_baseline(root, path, scope, diff)
    return {
        "ok": True,
        "path": path,
        "scope": scope,
        "hunks": serialized,
        "binary": "GIT binary patch" in diff or "Binary files " in diff,
        "diff": diff,
        "baseline": baseline,
        "head": head_oid,
    }


def _apply_patch(repo_root: str, patch: str, *, cached: bool, reverse: bool) -> dict:
    args = ["apply", "--recount", "--whitespace=nowarn"]
    if cached:
        args.append("--cached")
    if reverse:
        args.append("--reverse")
    result = _git(repo_root, *args, input_text=patch)
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout or "git apply 失败").strip()}
    return {"ok": True}


def apply_review_action(
    repo_root: str,
    *,
    action: str,
    path: str,
    scope: str = "working",
    hunk_id: str = "",
    expected_sha256: str = "",
    confirm: bool = False,
) -> dict:
    root = _repo_root(repo_root)
    action = str(action or "").strip().lower()
    path = normalize_git_path(path)
    scope = str(scope or "working").strip().lower()
    if action not in _VALID_ACTIONS:
        raise ValueError("Git action 只支持 stage / unstage / revert")
    if scope not in _VALID_SCOPES:
        raise ValueError("Git scope 只支持 working / staged")
    if action == "revert" and not confirm:
        raise ValueError("撤销会丢弃本地修改，必须显式确认")
    expected_scope = "staged" if action == "unstage" else "working"
    if scope != expected_scope:
        raise ValueError(f"{action} 只能作用于 {expected_scope} diff")
    entry = _entry_for_path(root, path)
    if entry is None:
        return {"ok": False, "conflict": True, "error": "文件改动已变化，请刷新后重试"}
    if action == "revert" and entry.get("untracked"):
        raise ValueError("未跟踪文件不能在 Desktop 里撤销，避免误删；可暂存或在源码工作区处理")

    if hunk_id:
        if not expected_sha256:
            raise ValueError("逐块操作缺少当前 hunk 哈希")
        payload = review_diff(root, path, scope=scope)
        hunk = next((item for item in payload["hunks"] if item["id"] == hunk_id), None)
        if hunk is None or hunk["sha256"] != expected_sha256:
            return {"ok": False, "conflict": True, "error": "Diff 已变化，请刷新后重新选择 hunk"}
        # Re-read the internal parsed patch only after the public hash guard succeeds.
        current = _parse_hunks(_raw_diff(root, path, scope, entry))
        source = next((item for item in current if stable_hunk_id(
            path, str(item.get("section") or ""), item.get("lines") or [],
        ) == hunk_id), None)
        if source is None:
            return {"ok": False, "conflict": True, "error": "Diff 已变化，请刷新后重试"}
        patch = str(source.get("diff") or "") + "\n"
        result = _apply_patch(
            root,
            patch,
            cached=action in {"stage", "unstage"},
            reverse=action in {"unstage", "revert"},
        )
    else:
        paths = [path]
        if entry.get("original_path"):
            paths.insert(0, entry["original_path"])
        if action == "stage":
            command = _git(root, "add", "-A", "--", *paths)
        elif action == "unstage":
            has_head = _git(root, "rev-parse", "--verify", "HEAD").returncode == 0
            command = (
                _git(root, "reset", "-q", "HEAD", "--", *paths)
                if has_head
                else _git(root, "rm", "--cached", "-q", "--", *paths)
            )
        else:
            if entry.get("original_path"):
                raise ValueError("重命名文件暂不支持整文件撤销，请使用外部 Git 工具")
            command = _git(root, "restore", "--worktree", "--", path)
        result = (
            {"ok": True}
            if command.returncode == 0
            else {"ok": False, "error": (command.stderr or command.stdout or "Git 操作失败").strip()}
        )

    if not result.get("ok"):
        return result
    return {"ok": True, "snapshot": review_snapshot(root)}


def commit_reviewed(repo_root: str, message: str) -> dict:
    """Commit only the index that the user assembled in the review UI."""
    root = _repo_root(repo_root)
    message = str(message or "").strip()
    if not message:
        raise ValueError("提交说明不能为空")
    if len(message) > 500 or "\x00" in message:
        raise ValueError("提交说明超过 500 字符或包含无效字符")
    staged = _git(root, "diff", "--cached", "--quiet")
    if staged.returncode == 0:
        raise ValueError("没有已暂存改动；请先在 Diff 面板暂存文件或 hunk")
    if staged.returncode != 1:
        raise ValueError((staged.stderr or staged.stdout or "无法读取已暂存改动").strip())
    result = _git(root, "commit", "-m", message, timeout=60)
    if result.returncode != 0:
        return {"ok": False, "error": (result.stderr or result.stdout or "git commit 失败").strip()}
    sha = _git(root, "rev-parse", "--short", "HEAD")
    return {
        "ok": True,
        "sha": (sha.stdout or "").strip(),
        "output": (result.stdout or result.stderr or "").strip()[-2000:],
        "snapshot": review_snapshot(root),
    }


def open_reviewed_pr(
    repo_root: str,
    *,
    title: str,
    body: str = "",
    base: str = "main",
    confirm: bool = False,
) -> dict:
    """Push the current reviewed branch and create a Draft PR after an explicit gate."""
    root = _repo_root(repo_root)
    if not confirm:
        raise ValueError("推送分支并创建 PR 是外向操作，必须显式确认")
    title = str(title or "").strip()
    body = str(body or "").strip()
    base = str(base or "main").strip()
    if not title or len(title) > 300:
        raise ValueError("PR 标题不能为空且不能超过 300 字符")
    if len(body) > 10_000:
        raise ValueError("PR 描述不能超过 10,000 字符")
    if not _BRANCH_NAME.fullmatch(base):
        raise ValueError("PR base 分支名无效")
    if _git(root, "check-ref-format", "--branch", base).returncode != 0:
        raise ValueError("PR base 分支名不符合 Git ref 规则")
    branch_result = _git(root, "branch", "--show-current")
    branch = (branch_result.stdout or "").strip()
    if not branch or not _BRANCH_NAME.fullmatch(branch):
        raise ValueError("当前不在可推送的本地分支上")
    if branch.lower() in _PROTECTED_BRANCHES:
        raise ValueError(f"拒绝直接从受保护分支 {branch} 创建 PR；请先切到功能分支")
    staged = _git(root, "diff", "--cached", "--quiet")
    if staged.returncode == 1:
        raise ValueError("仍有已暂存但未提交的改动，请先提交再创建 PR")
    if staged.returncode != 0:
        raise ValueError((staged.stderr or staged.stdout or "无法读取暂存区状态").strip())
    base_ref = base
    if _git(root, "rev-parse", "--verify", base_ref).returncode != 0:
        base_ref = f"origin/{base}"
    ahead = _git(root, "rev-list", "--count", f"{base_ref}..HEAD")
    if ahead.returncode != 0:
        raise ValueError((ahead.stderr or ahead.stdout or f"找不到 base 分支 {base}").strip())
    try:
        ahead_count = int((ahead.stdout or "0").strip())
    except ValueError as error:
        raise ValueError("无法判断当前分支相对 base 的提交数量") from error
    if ahead_count < 1:
        raise ValueError(f"当前分支相对 {base} 没有可提交到 PR 的新提交")
    if not shutil.which("gh"):
        raise ValueError("gh CLI 不可用或未安装，无法安全创建 PR")

    from src.agents.vcs import push_and_open_pr

    result = push_and_open_pr(
        root,
        branch,
        title,
        body or title,
        base=base,
        remote="origin",
        draft=True,
    )
    return {
        "ok": bool(result.get("ok")),
        "pushed": bool(result.get("pushed")),
        "url": str(result.get("url") or ""),
        "error": str(result.get("error") or ""),
        "branch": branch,
        "base": base,
    }
