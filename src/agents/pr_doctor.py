"""PR feedback diagnosis helpers.

This module stays UI-neutral: it reads the existing PR feedback surface and
turns it into a concise action plan for TUI/CLI callers.
"""
from __future__ import annotations

from typing import Any


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


def build_pr_doctor_report(repo_root: str, ref: str, feedback: dict) -> dict:
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
        "feedback": feedback,
    }


def pr_doctor_report(repo_root: str, ref: str) -> dict:
    from src.agents.vcs import pr_feedback

    return build_pr_doctor_report(repo_root, ref, pr_feedback(repo_root, ref))


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
