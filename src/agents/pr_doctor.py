"""PR feedback diagnosis helpers.

This module stays UI-neutral: it reads the existing PR feedback surface and
turns it into a concise action plan for TUI/CLI callers.
"""
from __future__ import annotations

import re
from typing import Any


_PYTEST_SELECTOR_RE = re.compile(
    r"((?:tests|src|examples)/[A-Za-z0-9_./-]+\.py(?:::[A-Za-z0-9_./\-\[\]:]+)+)"
)

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


def _failure_text(checks: list[dict], check_logs: list[dict] | None = None) -> str:
    parts: list[str] = []
    for ck in checks:
        parts.append(str(ck.get("name") or ck.get("context") or ""))
        parts.append(str(ck.get("link") or ""))
    for item in check_logs or []:
        parts.append(str(item.get("name") or ""))
        parts.append(str(item.get("job_name") or ""))
        parts.append(str(item.get("step_name") or ""))
        parts.append(str(item.get("excerpt") or ""))
    return "\n".join(parts)


def extract_pytest_selectors(check_logs: list[dict] | None = None, *, limit: int = 3) -> list[str]:
    """Extract precise pytest selectors from failed log excerpts."""
    seen: set[str] = set()
    out: list[str] = []
    text = _failure_text([], check_logs)
    for match in _PYTEST_SELECTOR_RE.finditer(text):
        selector = match.group(1).strip().rstrip(".,;:")
        if selector in seen:
            continue
        seen.add(selector)
        out.append(selector)
        if len(out) >= limit:
            break
    return out


def _verify_slash(command: str) -> str:
    command = str(command or "").strip()
    if not command:
        return ""
    if command.startswith("/verify"):
        return command
    if command.startswith("/") or "运行项目" in command or "检查 " in command:
        return ""
    return f"/verify run {command}"


def _template(kind: str, title: str, command: str, detail: str, *,
              safe: bool = False, slash: str = "") -> dict:
    slash_cmd = slash or (_verify_slash(command) if safe else "")
    return {
        "kind": kind,
        "title": title,
        "command": command,
        "detail": detail,
        "safe": safe,
        "slash": slash_cmd,
    }


def repair_templates(checks: list[dict], check_logs: list[dict] | None,
                     classification: dict | None = None) -> list[dict]:
    """Return concrete repair templates for the classified failure shape."""
    classification = classification or classify_failed_checks(checks, check_logs)
    category = classification.get("category") or "unknown"
    text = _failure_text(checks, check_logs).lower()
    selectors = extract_pytest_selectors(check_logs)
    templates: list[dict] = []

    if category == "test":
        if selectors:
            cmd = "python -m pytest -q " + " ".join(selectors[:2])
            templates.append(_template(
                "verify",
                "复现最小失败测试",
                cmd,
                "先只跑日志里出现的失败 selector，确认本地可复现后再改代码。",
                safe=True,
            ))
        templates.append(_template(
            "verify",
            "修复后跑相关测试",
            "/verify unit",
            "没有更精确 selector 时，使用项目 unit profile 兜底验证。",
            safe=True,
            slash="/verify unit",
        ))
    elif category == "lint":
        if "ruff" in text:
            command = "ruff check . --fix"
        elif "prettier" in text:
            command = "prettier --write ."
        else:
            command = "运行项目 lint/format 命令"
        templates.append(_template(
            "fix",
            "机械修复 lint/格式",
            command,
            "先做格式或 lint 机械修复，再确认没有行为 diff 混入。",
            safe=False,
        ))
    elif category == "type-check":
        if "mypy" in text:
            command = "mypy ."
        elif "pyright" in text:
            command = "pyright"
        elif "tsc" in text:
            command = "npx tsc --noEmit"
        else:
            command = "运行项目 type-check 命令"
        templates.append(_template(
            "verify",
            "复现类型检查失败",
            command,
            "优先修类型签名、None 分支、导入类型或泛型不一致。",
            safe=not command.startswith("运行项目"),
        ))
    elif category == "dependency":
        templates.append(_template(
            "inspect",
            "修依赖声明而不是只补本地环境",
            "检查 pyproject.toml / requirements / lock file / CI install step",
            "确认缺失包或版本约束写进项目依赖声明，并更新对应锁文件。",
        ))
    elif category == "environment":
        templates.append(_template(
            "inspect",
            "先区分环境问题和源码问题",
            "检查 CI 权限、缓存目录、secret、系统依赖和命令可用性",
            "环境类失败通常不应通过业务代码绕过，先修 workflow 或诊断输出。",
        ))
    elif category == "timeout":
        command = "python -m pytest -q " + " ".join(selectors[:2]) if selectors else "/verify unit"
        templates.append(_template(
            "verify",
            "缩小超时范围",
            command,
            "先用最小 selector 或 unit profile 定位卡住点，再补超时/worker 诊断。",
            safe=True,
            slash=command if command.startswith("/verify") else "",
        ))
    elif category == "build":
        templates.append(_template(
            "verify",
            "复现最小构建/编译失败",
            "python -m compileall -q src tests",
            "若 CI 不是 Python 构建，换成对应 job 的 build command。",
            safe=True,
        ))

    if not templates:
        templates.append(_template(
            "inspect",
            "人工读取原始失败上下文",
            "/pr-check <ref>",
            "当前启发式无法稳定归类，先看原始 review/CI 反馈再决定修复路径。",
            slash="/pr-check <ref>",
        ))
    return templates


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
    classification = classify_failed_checks(checks, logs) if checks else {}
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
        "failure_classification": classification,
        "repair_templates": repair_templates(checks, logs, classification) if checks else [],
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

    templates = list(report.get("repair_templates") or [])
    if templates:
        lines += ["", "推荐修复模板:"]
        for item in templates[:4]:
            kind = f"[{item.get('kind')}]" if item.get("kind") else ""
            lines.append(f"- {kind} {item.get('title') or '修复步骤'}".strip())
            command = str(item.get("command") or "").replace("<ref>", ref)
            if command:
                lines.append(f"  命令: {command}")
            slash = str(item.get("slash") or "").replace("<ref>", ref)
            if slash:
                lines.append(f"  动作: {slash}")
            if item.get("detail"):
                lines.append(f"  说明: {item.get('detail')}")

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
