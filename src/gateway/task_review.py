"""Structured review of durable background-task branches.

Live implementation worktrees stay Agent-owned: mutating one while its Agent is
writing would race and make verification meaningless. This module reviews the
durable ``vorto/*`` branch produced by a terminal task instead. Accepting a hunk
records bounded evidence; rejecting one creates an auditable branch commit and
invalidates prior verification until that exact new head passes again.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.agents.dev_plan import load_plan
from src.utils.state_dir import ensure_state_gitignore
from src.agents.git_workflow import _parse_hunks
from src.gateway.change_sources import stable_hunk_id
from src.gateway.git_review import _line_items, normalize_git_path
from src.gateway.review_policy import load_task_review_policy

_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$")
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")
_MAX_FILES = 2_000
_MAX_DIFF_CHARS = 2_000_000
_MAX_ACCEPTED = 4_000
_MAX_POLICY_DIFF_CHARS = 8_000_000
_MAX_PENDING_EVIDENCE = 200
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git(root: str | Path, *args: str, input_text: Optional[str] = None,
         timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args], input=input_text,
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def _repo_root(repo_root: str) -> str:
    requested = Path(repo_root).resolve()
    result = _git(requested, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise ValueError("当前 Runtime 目录不是 Git 仓库")
    return str(Path(result.stdout.strip()).resolve())


def _clean_task_id(value: Any) -> str:
    return _SAFE_ID.sub("_", str(value or "").strip())[:180]


def _store_path(repo_root: str, task_id: str) -> Path:
    return Path(repo_root) / ".vortocode" / "task_reviews" / f"{_clean_task_id(task_id)}.json"


def _load_store(repo_root: str, task_id: str) -> dict[str, Any]:
    try:
        payload = json.loads(_store_path(repo_root, task_id).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {"version": 1, "accepted": {}, "verification_stale": False}
    if not isinstance(payload, dict):
        return {"version": 1, "accepted": {}, "verification_stale": False}
    accepted = payload.get("accepted")
    payload["accepted"] = accepted if isinstance(accepted, dict) else {}
    payload["verification_stale"] = bool(payload.get("verification_stale"))
    return payload


def _save_store(repo_root: str, task_id: str, payload: dict[str, Any]) -> None:
    ensure_state_gitignore(repo_root)
    target = _store_path(repo_root, task_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    accepted = payload.get("accepted") if isinstance(payload.get("accepted"), dict) else {}
    payload["accepted"] = dict(list(accepted.items())[-_MAX_ACCEPTED:])
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def _task_context(repo_root: str, task: Any) -> dict[str, Any]:
    root = _repo_root(repo_root)
    plan = load_plan(root, str(getattr(task, "plan_id", "") or ""))
    branch = str(getattr(task, "branch", "") or (plan.branch if plan else "")).strip()
    base = str(plan.base if plan and plan.base else "main").strip()
    if not branch or not _BRANCH.fullmatch(branch):
        raise ValueError("任务没有可审查的本地分支")
    if not branch.startswith("vorto/"):
        raise ValueError("只允许审查隔离流水线产生的 vorto/* 分支")
    branch_ref = f"refs/heads/{branch}"
    head_result = _git(root, "rev-parse", "--verify", branch_ref)
    if head_result.returncode != 0:
        raise ValueError("任务分支不存在或已被删除")
    base_ref = base
    if _git(root, "rev-parse", "--verify", base_ref).returncode != 0:
        base_ref = f"origin/{base}"
    merge_base = _git(root, "merge-base", base_ref, branch_ref)
    if merge_base.returncode != 0:
        raise ValueError((merge_base.stderr or merge_base.stdout or "无法确定任务分支基线").strip())
    return {
        "root": root,
        "plan": plan,
        "branch": branch,
        "branch_ref": branch_ref,
        "base": base,
        "base_ref": base_ref,
        "merge_base": merge_base.stdout.strip(),
        "head": head_result.stdout.strip(),
        "mutable": str(getattr(task, "status", "") or "") == "done",
        "task_id": str(getattr(task, "id", "") or ""),
        "plan_id": str(getattr(task, "plan_id", "") or ""),
        "owner_session": str(getattr(task, "owner_session", "") or ""),
    }


def _name_status(context: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    result = _git(
        context["root"], "diff", "--name-status", "-z", "--find-renames",
        f"{context['merge_base']}..{context['branch_ref']}",
    )
    if result.returncode != 0:
        raise ValueError((result.stderr or result.stdout or "读取任务分支文件失败").strip())
    tokens = [item for item in result.stdout.split("\0") if item]
    files: list[dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        index += 1
        if index >= len(tokens):
            break
        original = ""
        if status.startswith(("R", "C")):
            if index + 1 >= len(tokens):
                break
            original = normalize_git_path(tokens[index])
            path = normalize_git_path(tokens[index + 1])
            index += 2
        else:
            path = normalize_git_path(tokens[index])
            index += 1
        code = status[:1] or "M"
        files.append({
            "path": path,
            "original_path": original,
            "status": status[:12],
            "index_status": code,
            "worktree_status": " ",
            "staged": False,
            "unstaged": True,
            "untracked": False,
            "conflicted": False,
        })
        if len(files) >= _MAX_FILES:
            return files, True
    return files, False


def _review_state(repo_root: str, task: Any) -> dict[str, Any]:
    payload = _load_store(repo_root, str(getattr(task, "id", "") or ""))
    verification = payload.get("verification") if isinstance(payload.get("verification"), dict) else None
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {
        "known": False, "total_hunks": 0, "accepted_hunks": 0,
        "pending_hunks": 0, "complete": False, "truncated": False,
    }
    return {
        "accepted_hunks": int(coverage.get("accepted_hunks") or len(payload.get("accepted") or {})),
        "verification_stale": bool(payload.get("verification_stale")),
        "verification": verification,
        "last_mutation": payload.get("last_mutation") if isinstance(payload.get("last_mutation"), dict) else None,
        "coverage": coverage,
        "policy": load_task_review_policy(repo_root),
    }


def task_review_summary(repo_root: str, task: Any) -> dict[str, Any] | None:
    """Cheap task-card projection; does not invoke Git."""
    if not (getattr(task, "branch", "") or getattr(task, "plan_id", "")):
        return None
    return _review_state(repo_root, task)


def task_review_snapshot(repo_root: str, task: Any) -> dict[str, Any]:
    with _LOCK:
        context = _task_context(repo_root, task)
        files, truncated = _name_status(context)
        store = _load_store(context["root"], context["task_id"])
        review = _review_state(context["root"], task)
        coverage = _review_coverage(context, store)
        store["coverage"] = coverage
        _save_store(context["root"], context["task_id"], store)
        review["coverage"] = coverage
        review["accepted_hunks"] = coverage["accepted_hunks"]
        return {
            "ok": True,
            "target": "task",
            "task_id": context["task_id"],
            "root": context["root"],
            "branch": context["branch"],
            "base": context["base"],
            "head": context["head"][:12],
            "head_oid": context["head"],
            "files": files,
            "truncated": truncated,
            "mutable": context["mutable"],
            "mutation_reason": "" if context["mutable"] else "任务仍在执行或未成功完成，当前只允许查看",
            "review": review,
        }


def _raw_task_diff(context: dict[str, Any], path: str) -> str:
    result = _git(
        context["root"], "diff", "--no-ext-diff", "--unified=3", "--find-renames",
        f"{context['merge_base']}..{context['branch_ref']}", "--", path,
    )
    if result.returncode != 0:
        raise ValueError((result.stderr or result.stdout or "读取任务分支 diff 失败").strip())
    diff = result.stdout or ""
    if len(diff) > _MAX_DIFF_CHARS:
        raise ValueError("单文件 diff 超过 2,000,000 字符，请在外部工具审查")
    return diff


def _review_coverage(context: dict[str, Any], store: dict[str, Any]) -> dict[str, Any]:
    """Compare current branch hunks with exact accepted evidence in one Git read."""
    result = _git(
        context["root"], "diff", "--no-ext-diff", "--unified=3", "--find-renames",
        f"{context['merge_base']}..{context['branch_ref']}",
    )
    if result.returncode != 0:
        return {
            "known": False, "total_hunks": 0, "accepted_hunks": 0,
            "pending_hunks": 0, "complete": False, "truncated": False,
            "pending": [], "pending_truncated": False,
            "head": context["head"], "error": "无法读取任务分支审查覆盖率",
        }
    diff = result.stdout or ""
    if len(diff) > _MAX_POLICY_DIFF_CHARS:
        return {
            "known": False, "total_hunks": 0, "accepted_hunks": 0,
            "pending_hunks": 0, "complete": False, "truncated": True,
            "pending": [], "pending_truncated": True,
            "head": context["head"], "error": "任务分支 diff 超过 8,000,000 字符",
        }
    accepted = store.get("accepted") if isinstance(store.get("accepted"), dict) else {}
    current: dict[str, str] = {}
    pending = []
    for hunk in _parse_hunks(diff):
        path = normalize_git_path(str(hunk.get("path") or ""))
        raw_lines = [str(line) for line in (hunk.get("lines") or [])]
        hunk_id = stable_hunk_id(path, str(hunk.get("section") or ""), raw_lines)
        patch = str(hunk.get("diff") or "") + "\n"
        sha256 = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        key = f"{path}|{hunk_id}"
        current[key] = sha256
        evidence = accepted.get(key)
        if not (isinstance(evidence, dict) and evidence.get("sha256") == sha256):
            pending.append({"path": path, "hunk_id": hunk_id})
    accepted_count = sum(
        1 for key, sha256 in current.items()
        if isinstance(accepted.get(key), dict) and accepted[key].get("sha256") == sha256
    )
    stale_count = sum(
        1 for key, evidence in accepted.items()
        if key not in current or not isinstance(evidence, dict)
        or evidence.get("sha256") != current.get(key)
    )
    total = len(current)
    return {
        "known": True,
        "total_hunks": total,
        "accepted_hunks": accepted_count,
        "pending_hunks": len(pending),
        "stale_hunks": stale_count,
        "complete": accepted_count == total,
        "truncated": False,
        "pending": pending[:_MAX_PENDING_EVIDENCE],
        "pending_truncated": len(pending) > _MAX_PENDING_EVIDENCE,
        "head": context["head"],
        "error": "",
    }


def _cache_review_coverage(context: dict[str, Any], store: dict[str, Any]) -> None:
    store["coverage"] = _review_coverage(context, store)
    _save_store(context["root"], context["task_id"], store)


def _task_like(context: dict[str, Any]) -> Any:
    return type("TaskReviewTarget", (), {
        "id": context["task_id"],
        "branch": context["branch"],
        "plan_id": context["plan_id"],
        "owner_session": context["owner_session"],
        "status": "done" if context["mutable"] else "running",
    })()


def task_review_diff(repo_root: str, task: Any, path: str) -> dict[str, Any]:
    context = _task_context(repo_root, task)
    path = normalize_git_path(path)
    files, _ = _name_status(context)
    if not any(item["path"] == path for item in files):
        raise ValueError("文件已不在任务分支改动列表中，请刷新")
    diff = _raw_task_diff(context, path)
    store = _load_store(context["root"], context["task_id"])
    accepted = store.get("accepted") if isinstance(store.get("accepted"), dict) else {}
    hunks = []
    for hunk in _parse_hunks(diff):
        patch = str(hunk.get("diff") or "") + "\n"
        raw_lines = [str(line) for line in (hunk.get("lines") or [])]
        hunk_id = stable_hunk_id(path, str(hunk.get("section") or ""), raw_lines)
        sha256 = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        evidence = accepted.get(f"{path}|{hunk_id}")
        hunks.append({
            "id": hunk_id,
            "path": path,
            "header": str(hunk.get("header") or ""),
            "old_start": int(hunk.get("old_start") or 0),
            "new_start": int(hunk.get("new_start") or 0),
            "section": str(hunk.get("section") or ""),
            "sha256": sha256,
            "source": "agent",
            "source_tool": f"task:{context['task_id']}",
            "source_at": "",
            "accepted": bool(isinstance(evidence, dict) and evidence.get("sha256") == sha256),
            "lines": _line_items(hunk),
        })
    baseline = hashlib.sha256(
        f"{context['head']}\0{context['merge_base']}\0{path}\0{diff}".encode("utf-8")
    ).hexdigest()
    return {
        "ok": True,
        "target": "task",
        "task_id": context["task_id"],
        "path": path,
        "scope": "branch",
        "hunks": hunks,
        "binary": "GIT binary patch" in diff or "Binary files " in diff,
        "diff": diff,
        "baseline": baseline,
        "head": context["head"],
    }


def _find_hunk(context: dict[str, Any], path: str, hunk_id: str,
               expected_sha256: str) -> tuple[dict[str, Any], str]:
    public = task_review_diff(context["root"], _task_like(context), path)
    shown = next((item for item in public["hunks"] if item["id"] == hunk_id), None)
    if shown is None or shown["sha256"] != expected_sha256:
        raise RuntimeError("Diff 已变化，请刷新后重新选择 hunk")
    for source in _parse_hunks(_raw_task_diff(context, path)):
        raw_lines = [str(line) for line in (source.get("lines") or [])]
        if stable_hunk_id(path, str(source.get("section") or ""), raw_lines) == hunk_id:
            return shown, str(source.get("diff") or "") + "\n"
    raise RuntimeError("Diff 已变化，请刷新后重试")


def _branch_checkout_path(root: str, branch_ref: str) -> str:
    listed = _git(root, "worktree", "list", "--porcelain")
    current_path = ""
    for line in listed.stdout.splitlines() if listed.returncode == 0 else []:
        if line.startswith("worktree "):
            current_path = line[9:].strip()
        elif line == f"branch {branch_ref}":
            return current_path
    return ""


def apply_task_review_action(
    repo_root: str,
    task: Any,
    *,
    action: str,
    path: str,
    hunk_id: str,
    expected_sha256: str,
    confirm: bool = False,
) -> dict[str, Any]:
    action = str(action or "").strip().lower()
    if action not in {"accept", "reject"}:
        raise ValueError("任务审查 action 只支持 accept / reject")
    if not hunk_id or not expected_sha256:
        raise ValueError("任务逐块审查缺少稳定 hunk ID 或精确哈希")
    path = normalize_git_path(path)
    with _LOCK:
        context = _task_context(repo_root, task)
        if not context["mutable"]:
            raise ValueError("任务仍在执行或未成功完成，只允许查看改动")
        if action == "reject" and not confirm:
            raise ValueError("撤销任务分支改动会创建新提交，必须显式确认")
        _shown, patch = _find_hunk(context, path, hunk_id, expected_sha256)
        store = _load_store(context["root"], context["task_id"])
        accepted = store.setdefault("accepted", {})
        key = f"{path}|{hunk_id}"
        if action == "accept":
            accepted[key] = {
                "path": path, "hunk_id": hunk_id, "sha256": expected_sha256,
                "branch": context["branch"], "head": context["head"], "at": _now(),
            }
            _cache_review_coverage(context, store)
            return {"ok": True, "action": "accept", "snapshot": task_review_snapshot(context["root"], task)}

        checked_out = _branch_checkout_path(context["root"], context["branch_ref"])
        if checked_out:
            raise ValueError(f"任务分支正被 worktree 使用，暂不能撤销：{checked_out}")
        wid = f"review-{_clean_task_id(context['task_id'])[:40]}-{uuid.uuid4().hex[:8]}"
        worktree = Path(context["root"]) / ".vortocode" / "worktrees" / wid
        worktree.parent.mkdir(parents=True, exist_ok=True)
        from src.agents.worktree_bindings import (
            bind_worktree_owner, forget_worktree_binding, record_worktree_binding,
        )
        add = _git(context["root"], "worktree", "add", "--detach", str(worktree), context["head"])
        if add.returncode != 0:
            raise ValueError((add.stderr or add.stdout or "创建审查 worktree 失败").strip())
        try:
            with bind_worktree_owner(
                task_id=context["task_id"], owner_session=context["owner_session"],
                plan_id=context["plan_id"],
            ):
                record_worktree_binding(context["root"], wid)
            applied = _git(worktree, "apply", "--recount", "--whitespace=nowarn", "--reverse", input_text=patch)
            if applied.returncode != 0:
                raise RuntimeError((applied.stderr or applied.stdout or "撤销 hunk 失败").strip())
            staged = _git(worktree, "add", "-A")
            if staged.returncode != 0:
                raise ValueError((staged.stderr or staged.stdout or "暂存审查改动失败").strip())
            committed = _git(
                worktree, "commit", "-m", f"review: reject {path} {hunk_id[:12]}", timeout=90,
            )
            if committed.returncode != 0:
                raise ValueError((committed.stderr or committed.stdout or "提交审查改动失败").strip())
            new_head = _git(worktree, "rev-parse", "HEAD")
            if new_head.returncode != 0:
                raise ValueError("无法读取审查提交")
            if _branch_checkout_path(context["root"], context["branch_ref"]):
                raise RuntimeError("任务分支在审查期间被其他 worktree 使用，请重试")
            updated = _git(
                context["root"], "update-ref", context["branch_ref"],
                new_head.stdout.strip(), context["head"],
            )
            if updated.returncode != 0:
                raise RuntimeError("任务分支已变化，拒绝覆盖；请刷新后重试")
            accepted.pop(key, None)
            store["verification_stale"] = True
            store["last_mutation"] = {
                "action": "reject", "path": path, "hunk_id": hunk_id,
                "old_head": context["head"], "head": new_head.stdout.strip(), "at": _now(),
            }
            store.pop("verification", None)
            _cache_review_coverage({**context, "head": new_head.stdout.strip()}, store)
        finally:
            _git(context["root"], "worktree", "remove", "--force", str(worktree), timeout=90)
            _git(context["root"], "worktree", "prune")
            forget_worktree_binding(context["root"], wid)
        return {"ok": True, "action": "reject", "snapshot": task_review_snapshot(context["root"], task)}


def verify_task_review(repo_root: str, task: Any) -> dict[str, Any]:
    """Run the detected project tests on the exact reviewed branch head."""
    with _LOCK:
        context = _task_context(repo_root, task)
        if not context["mutable"]:
            raise ValueError("任务未成功完成，当前不能执行交付重验")
        wid = f"verify-{_clean_task_id(context['task_id'])[:40]}-{uuid.uuid4().hex[:8]}"
        worktree = Path(context["root"]) / ".vortocode" / "worktrees" / wid
        worktree.parent.mkdir(parents=True, exist_ok=True)
        add = _git(context["root"], "worktree", "add", "--detach", str(worktree), context["head"])
        if add.returncode != 0:
            raise ValueError((add.stderr or add.stdout or "创建验证 worktree 失败").strip())
        try:
            from src.agents.test_detect import detect_test_cmd
            from src.agents.worktree import run_tests
            from src.agents.worktree_bindings import bind_worktree_owner, record_worktree_binding

            with bind_worktree_owner(
                task_id=context["task_id"], owner_session=context["owner_session"],
                plan_id=context["plan_id"],
            ):
                record_worktree_binding(context["root"], wid)
            selector = context["plan"].test_sel if context["plan"] is not None else ""
            command = detect_test_cmd(str(worktree), selector)
            result = run_tests(worktree, command)
        finally:
            _git(context["root"], "worktree", "remove", "--force", str(worktree), timeout=90)
            _git(context["root"], "worktree", "prune")
            from src.agents.worktree_bindings import forget_worktree_binding
            forget_worktree_binding(context["root"], wid)
        store = _load_store(context["root"], context["task_id"])
        store["verification_stale"] = not bool(result.get("ok"))
        store["verification"] = {
            "ok": bool(result.get("ok")),
            "head": context["head"],
            "cmd": str(result.get("cmd") or "")[:500],
            "output": str(result.get("output") or "")[-4_000:],
            "sandbox": result.get("sandbox") if isinstance(result.get("sandbox"), dict) else {},
            "at": _now(),
        }
        _cache_review_coverage(context, store)
        return {"ok": bool(result.get("ok")), "verification": store["verification"],
                "snapshot": task_review_snapshot(context["root"], task)}


def task_review_delivery_ready(repo_root: str, task: Any) -> tuple[bool, str]:
    state = _review_state(repo_root, task)
    if state["verification_stale"]:
        return False, "任务分支在审查中被修改，旧测试证据已失效；请先重新验证"
    policy = state["policy"]
    if policy.get("error"):
        return False, f"团队审查策略无效：{policy['error']}"
    if policy.get("require_all_hunks_decided"):
        context = _task_context(repo_root, task)
        coverage = _review_coverage(
            context, _load_store(context["root"], context["task_id"]),
        )
        if not coverage["known"]:
            return False, f"无法确认全部 hunk 已完成决策：{coverage.get('error') or '覆盖率未知'}"
        if not coverage["complete"]:
            return False, (
                "团队策略要求所有 hunk 已决策："
                f"已接受 {coverage['accepted_hunks']}/{coverage['total_hunks']}，"
                f"仍有 {coverage['pending_hunks']} 个待处理"
            )
    return True, ""
