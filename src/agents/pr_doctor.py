"""PR feedback diagnosis helpers.

This module stays UI-neutral: it reads the existing PR feedback surface and
turns it into a concise action plan for TUI/CLI callers.
"""
from __future__ import annotations

from typing import Any


_FAILURE_CATEGORIES = {
    "test": {
        "label": "测试失败",
        "next_action": "先复现最小失败测试；修复断言、fixture 或业务行为后再跑相关 verify profile。",
        "patterns": (
            "pytest", "failed tests/", "failed test", "assertionerror", "assert ",
            "test_", "tests/", "unittest", "jest", "vitest", "rspec",
        ),
    },
    "lint": {
        "label": "Lint/格式失败",
        "next_action": "先运行对应 lint/format 命令；优先做机械修复，避免混入行为改动。",
        "patterns": (
            "ruff", "flake8", "eslint", "pylint", "lint", "black", "prettier",
            "would reformat", "format check", "trailing whitespace",
        ),
    },
    "type-check": {
        "label": "类型检查失败",
        "next_action": "先运行对应 type-check 命令；修复类型签名、None 分支或导入类型不一致。",
        "patterns": (
            "mypy", "pyright", "tsc", "type check", "type-check", "type error",
            "incompatible type", "has no attribute", "argument of type",
        ),
    },
    "dependency": {
        "label": "依赖/导入失败",
        "next_action": "先确认依赖声明、锁文件和 CI 安装步骤；不要只在本地环境补包。",
        "patterns": (
            "modulenotfounderror", "no module named", "importerror", "cannot import name",
            "no matching distribution", "could not find a version", "resolution impossible",
            "npm err", "pnpm", "poetry install", "pip install",
        ),
    },
    "environment": {
        "label": "环境/权限失败",
        "next_action": "先区分 CI 环境问题和代码问题；检查权限、缓存目录、系统依赖、secret 或命令是否存在。",
        "patterns": (
            "permission denied", "operation not permitted", "no space left", "command not found",
            "not found: command", "network is unreachable", "connection timed out", "rate limit",
            "secret", "xcode-select", "java_home", "modulecache",
        ),
    },
    "timeout": {
        "label": "超时/挂起",
        "next_action": "先定位卡住的测试或 worker；缩小选择器、补超时诊断，再修复阻塞点。",
        "patterns": (
            "timed out", "timeout", "cancelled", "canceled", "exceeded", "hung",
            "hanging", "deadline",
        ),
    },
    "build": {
        "label": "构建/语法失败",
        "next_action": "先运行最小构建或编译检查；修复语法、导入路径或构建配置。",
        "patterns": (
            "syntaxerror", "compileerror", "compilation failed", "build failed",
            "compileall", "failed to compile", "linker command failed",
        ),
    },
}


def _clip(text: Any, limit: int = 220) -> str:
    raw = " ".join(str(text or "").split())
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)].rstrip() + "…"


def _verify_suggestions(repo_root: str, checks: list[dict]) -> list[dict]:
    try:
        from src.agents.verify_profiles import load_verify_profiles
        loaded = load_verify_profiles(repo_root)
        profiles = loaded.get("profiles") or {} if loaded.get("ok") else {}
    except Exception:  # noqa: BLE001
        profiles = {}

    names = " ".join(str(c.get("name") or c.get("context") or "").lower() for c in checks)
    wanted: list[tuple[str, str]] = []
    if any(k in names for k in ("tui", "textual")):
        wanted.append(("tui", "失败检查看起来和 TUI/Textual 相关"))
    if any(k in names for k in ("pytest", "test", "unit")):
        wanted.append(("unit", "失败检查看起来是 Python 测试"))
    if any(k in names for k in ("git", "workflow", "preflight", "pr")):
        wanted.append(("git-workflow", "失败检查看起来和 Git/PR 流程相关"))
    wanted.append(("self-analyze", "提交前做一次确定性仓库扫描"))

    out: list[dict] = []
    for name, reason in wanted:
        if name not in profiles:
            continue
        if any(item["name"] == name for item in out):
            continue
        profile = profiles[name] or {}
        out.append({
            "name": name,
            "cmd": profile.get("cmd") or "",
            "reason": reason,
        })
    return out


def classify_failed_checks(checks: list[dict], check_logs: list[dict] | None = None) -> dict:
    """Classify CI failures from check names and short log excerpts."""
    sources: list[str] = []
    states: list[str] = []
    for ck in checks:
        sources.append(str(ck.get("name") or ck.get("context") or ""))
        state = str(ck.get("conclusion") or ck.get("state") or "")
        states.append(state)
        sources.append(state)
    for item in check_logs or []:
        sources.append(str(item.get("name") or ""))
        sources.append(str(item.get("job_name") or ""))
        sources.append(str(item.get("step_name") or ""))
        sources.append(str(item.get("excerpt") or ""))
        sources.append(str(item.get("error") or ""))
    haystack = "\n".join(sources).lower()
    state_text = " ".join(states).lower()
    if any(marker in state_text for marker in ("timed_out", "timed out", "timeout", "cancelled", "canceled")):
        spec = _FAILURE_CATEGORIES["timeout"]
        return {
            "category": "timeout",
            "label": spec["label"],
            "confidence": "high",
            "evidence": state_text.strip(),
            "next_action": spec["next_action"],
        }
    best: tuple[str, str, int] | None = None
    for category, spec in _FAILURE_CATEGORIES.items():
        for pattern in spec["patterns"]:
            idx = haystack.find(pattern)
            if idx < 0:
                continue
            if best is None or idx < best[2]:
                best = (category, pattern, idx)
    if best is None:
        return {
            "category": "unknown",
            "label": "未知失败",
            "confidence": "low",
            "evidence": "",
            "next_action": "先查看失败日志摘录和原始 CI；必要时运行 /pr-check 获取完整反馈。",
        }
    category, evidence, _idx = best
    spec = _FAILURE_CATEGORIES[category]
    return {
        "category": category,
        "label": spec["label"],
        "confidence": "medium",
        "evidence": evidence,
        "next_action": spec["next_action"],
    }


def build_pr_doctor_report(repo_root: str, ref: str, feedback: dict, *,
                           check_logs: list[dict] | None = None,
                           check_log_error: str = "") -> dict:
    """Build a normalized PR diagnosis from ``vcs.pr_feedback`` output."""
    if not feedback.get("ok"):
        return {
            "ok": False,
            "ref": ref,
            "error": feedback.get("error") or "PR 反馈读取失败",
            "feedback": feedback,
        }
    comments = list(feedback.get("comments") or [])
    checks = list(feedback.get("failing_checks") or [])
    branch = str(feedback.get("branch") or "")
    has_findings = bool(comments or checks)
    can_fix = has_findings and branch.startswith("vorto/")
    cannot_fix_reason = ""
    if has_findings and not can_fix:
        cannot_fix_reason = f"PR head 分支是 {branch or '未知'}，自动修复只允许 vorto/* 分支"
    logs = check_logs or []
    return {
        "ok": True,
        "ref": ref,
        "pr": feedback.get("pr") or ref,
        "branch": branch,
        "comments": comments,
        "failing_checks": checks,
        "has_findings": has_findings,
        "can_fix": can_fix,
        "cannot_fix_reason": cannot_fix_reason,
        "verify_suggestions": _verify_suggestions(repo_root, checks),
        "failure_classification": classify_failed_checks(checks, logs) if checks else {},
        "check_logs": logs,
        "check_log_error": check_log_error,
        "feedback": feedback,
    }


def pr_doctor_report(repo_root: str, ref: str) -> dict:
    from src.agents.vcs import failed_check_log_excerpts, pr_feedback

    feedback = pr_feedback(repo_root, ref)
    logs: list[dict] = []
    log_error = ""
    if feedback.get("ok") and feedback.get("failing_checks"):
        log_result = failed_check_log_excerpts(repo_root, list(feedback.get("failing_checks") or []))
        logs = list(log_result.get("logs") or [])
        if not log_result.get("ok"):
            log_error = str(log_result.get("error") or "")
    return build_pr_doctor_report(repo_root, ref, feedback, check_logs=logs, check_log_error=log_error)


def format_pr_doctor_report(report: dict) -> str:
    ref = str(report.get("ref") or "")
    if not report.get("ok"):
        return f"PR Doctor 读取失败（{ref}）: {report.get('error', '')}"

    pr = report.get("pr") or ref
    branch = report.get("branch") or "未知分支"
    comments = list(report.get("comments") or [])
    checks = list(report.get("failing_checks") or [])
    lines = [
        f"PR Doctor #{pr} · {branch}",
        f"待处理 review 评论: {len(comments)}",
        f"失败检查: {len(checks)}",
        "",
    ]
    if not comments and not checks:
        lines += [
            "结论: 没有待处理 review 评论，CI 也没有失败检查。",
            "",
            "建议动作:",
            f"- /pr-check {ref} 需要原始反馈时再查看",
        ]
        return "\n".join(lines)

    if report.get("can_fix"):
        lines.append("结论: 需要修复；可以确认后切到 build 并运行 pr_fix。")
    else:
        lines.append("结论: 需要人工处理；当前 PR 不能自动修复。")
        reason = str(report.get("cannot_fix_reason") or "").strip()
        if reason:
            lines.append(f"原因: {reason}")

    if checks:
        lines += ["", "失败检查:"]
        for ck in checks[:10]:
            name = ck.get("name") or ck.get("context") or "check"
            link = f" — {ck.get('link')}" if ck.get("link") else ""
            lines.append(f"- {name}{link}")
        if len(checks) > 10:
            lines.append(f"- ... 还有 {len(checks) - 10} 个")

    logs = [item for item in (report.get("check_logs") or [])
            if item.get("excerpt") or item.get("job_name") or item.get("step_name")]
    if logs:
        lines += ["", "失败定位/日志摘录:"]
        for item in logs[:3]:
            run = f" run {item.get('run_id')}" if item.get("run_id") else ""
            lines.append(f"- {item.get('name') or 'check'}{run}:")
            if item.get("job_name") or item.get("step_name"):
                loc = str(item.get("job_name") or "job")
                if item.get("step_name"):
                    loc += f" > {item.get('step_name')}"
                lines.append(f"  位置: {loc}")
            for ln in str(item.get("excerpt") or "").splitlines()[:40]:
                lines.append(f"  {ln}")
    elif checks and report.get("check_log_error"):
        lines += ["", f"失败日志摘录: 未读取（{report.get('check_log_error')}）"]

    classification = report.get("failure_classification") or {}
    if classification:
        lines += [
            "",
            "失败类型判断:",
            f"- {classification.get('label', '未知失败')}（置信度: {classification.get('confidence', 'low')}）",
        ]
        if classification.get("evidence"):
            lines.append(f"  证据: {classification.get('evidence')}")
        lines.append(f"  建议: {classification.get('next_action')}")

    if comments:
        lines += ["", "Review 待办:"]
        for c in comments[:20]:
            loc = f"{c.get('path')}:{c.get('line')}" if c.get("path") and c.get("line") else str(c.get("path") or "PR")
            author = c.get("author") or "?"
            lines.append(f"- [{author}] {loc} {_clip(c.get('body'), 180)}")
        if len(comments) > 20:
            lines.append(f"- ... 还有 {len(comments) - 20} 条")

    lines += ["", "建议动作:"]
    if report.get("can_fix"):
        lines.append(f"- /pr-fix {ref}  自动修复 review/CI 反馈，push 前仍会二次确认")
    else:
        lines.append(f"- /pr-check {ref}  查看原始 review/CI 反馈")
    for rec in report.get("verify_suggestions") or []:
        lines.append(f"- /verify {rec['name']}  {rec.get('reason') or rec.get('cmd') or ''}".rstrip())
    if not report.get("verify_suggestions"):
        lines.append("- /verify --changed  根据当前改动推断相关测试")
    return "\n".join(lines)
