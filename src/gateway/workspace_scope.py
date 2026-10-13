"""Workspace scope shared by Gateway entry points and agent assembly.

The process working directory is an implementation detail.  A session scope is
the authority boundary: ``general`` must not gain repository or host-process
tools merely because its managed data directory happens to exist on disk.
"""

from __future__ import annotations

import os
import re

GENERAL = "general"
SCRATCH = "scratch"
PROJECT = "project"
ALL_SCOPES = (GENERAL, SCRATCH, PROJECT)


def normalize_workspace_scope(value: object, *, default: str = PROJECT) -> str:
    """Return a known scope; unknown values fail closed to ``default``."""
    fallback = str(default or PROJECT).strip().lower()
    if fallback not in ALL_SCOPES:
        fallback = PROJECT
    candidate = str(value or "").strip().lower()
    return candidate if candidate in ALL_SCOPES else fallback


def current_workspace_scope() -> str:
    """Read the scope selected by the trusted process launcher.

    Project remains the compatibility default for CLI/TUI and manually started
    gateways.  Desktop always sets the variable explicitly.
    """
    return normalize_workspace_scope(os.getenv("VORTOCODE_WORKSPACE_SCOPE"))


def workspace_scope_snapshot(workdir: str) -> dict[str, object]:
    scope = current_workspace_scope()
    return {
        "scope": scope,
        "workdir": workdir if scope != GENERAL else None,
        "has_workspace": scope in (SCRATCH, PROJECT),
        "is_project": scope == PROJECT,
    }


_GENERAL_HTTP_PREFIXES = (
    "/api/status",
    "/api/health",
    "/api/cost/",
    "/api/auth/",
    "/api/agent/sessions",
    "/api/agent/activity",      # 助理模式动态：只读各会话的回合摘要，与会话列表同级，不碰项目/Shell/Git
    "/api/resident/projects",   # 常驻电脑上的项目登记表：只读名称/目录/端口，不读项目内容
    "/api/dispatches",          # 常驻助理的派活台账：任务单与下发（下发由人点，目标只限登记项目）
    "/api/assistant/",          # 常驻助理的名字/关于你/长期记忆（查看与删除）
    "/api/im/",                 # IM 桥状态与同机转发通知：常驻助理持有 Telegram 时 8080 经这里转发（真机曾被挡）
    "/api/artifacts",
    "/artifact/",
    "/artifacts",
    "/api/decisions",
    "/api/runtime-inbox",
    "/api/audit",
    "/api/journal",
    "/api/notices",
)
# 只放行精确形状（不是 /api/cron 前缀）：助理「定时」页的启停与执行历史。启停只认已有作业名+版本、
# 不收命令/提示词；手动触发 /run 与作业表全景仍需工作区（真机 2026-10-13：常驻助理上点历史 409）。
_GENERAL_HTTP_PATTERNS = (
    re.compile(r"/api/cron/[^/]+/(enabled|history)"),
)
_PROJECT_HTTP_PREFIXES = (
    "/api/git",
    "/api/github",
    "/api/repo-memory",
    "/api/context",
    "/api/projects",
    "/api/editor",
    "/api/skills",
    "/api/hooks",
    "/api/extensions",
)


def http_workspace_requirement(path: str, scope: str | None = None) -> str | None:
    """Return the scope required for an HTTP endpoint, or ``None`` if allowed.

    WebSocket agent authority is enforced by its tool set.  This is the second
    half of the boundary: Desktop REST panels must not turn General into a
    hidden project/shell workspace.
    """
    active = normalize_workspace_scope(scope or current_workspace_scope())
    if active != GENERAL:
        return None
    normalized = "/" + str(path or "").lstrip("/")
    if normalized == "/api/trust" and os.getenv("VORTOCODE_DESKTOP_SIDECAR") == "1":
        return None  # Desktop General can select read approval without gaining a project.
    if normalized in ("/", "/agent", "/ws", "/api/extensions/mcp", "/api/extensions/mcp/connect"):
        return None
    if any(normalized == prefix.rstrip("/") or normalized.startswith(prefix)
           for prefix in _GENERAL_HTTP_PREFIXES):
        return None
    if any(pattern.fullmatch(normalized) for pattern in _GENERAL_HTTP_PATTERNS):
        return None
    if any(normalized == prefix or normalized.startswith(prefix + "/")
           for prefix in _PROJECT_HTTP_PREFIXES):
        return PROJECT
    if normalized.startswith("/api/"):
        return SCRATCH
    return None
