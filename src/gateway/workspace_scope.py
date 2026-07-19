"""Workspace scope shared by Gateway entry points and agent assembly.

The process working directory is an implementation detail.  A session scope is
the authority boundary: ``general`` must not gain repository or host-process
tools merely because its managed data directory happens to exist on disk.
"""

from __future__ import annotations

import os

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
    "/api/artifacts",
    "/artifact/",
    "/artifacts",
    "/api/decisions",
    "/api/runtime-inbox",
    "/api/audit",
    "/api/journal",
    "/api/notices",
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
    if normalized in ("/", "/agent", "/ws"):
        return None
    if any(normalized == prefix.rstrip("/") or normalized.startswith(prefix)
           for prefix in _GENERAL_HTTP_PREFIXES):
        return None
    if any(normalized == prefix or normalized.startswith(prefix + "/")
           for prefix in _PROJECT_HTTP_PREFIXES):
        return PROJECT
    if normalized.startswith("/api/"):
        return SCRATCH
    return None
