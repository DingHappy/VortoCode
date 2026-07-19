"""Session-scoped capability profiles for credential isolation.

Project files may narrow tool permissions, but they must never be able to grant
host credentials to an external-content session.  This module therefore keeps
the capability profile outside ``.vortocode/permissions.yaml`` and makes the
entry point choose it explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

HOST_PROCESS = "host_process"
AUTHENTICATED_OUTBOUND = "authenticated_outbound"
SENSITIVE_FILES = "sensitive_files"
EXTERNAL_CONTENT = "external_content"

LOCAL_PROFILE = "local"
EXTERNAL_PROFILE = "external"
UNATTENDED_PROFILE = "unattended"

_CREDENTIAL_CAPABILITIES = frozenset(
    {HOST_PROCESS, AUTHENTICATED_OUTBOUND, SENSITIVE_FILES}
)
_PROFILE_CAPABILITIES = {
    # A local development session can run tests, use Git credentials, and read
    # explicitly requested secret-bearing files.  It cannot ingest Web/MCP
    # content: use a separate external session so old credential context is not
    # carried across the trust boundary.
    LOCAL_PROFILE: _CREDENTIAL_CAPABILITIES,
    # Web/IM sessions can inspect external content but cannot touch host
    # processes, authenticated remotes, or secret-bearing repository files.
    EXTERNAL_PROFILE: frozenset({EXTERNAL_CONTENT}),
    # Cron/heartbeat is deliberately named separately for audit evidence even
    # though its current capability set is as restrictive as external.
    UNATTENDED_PROFILE: frozenset({EXTERNAL_CONTENT}),
}

_SAFE_ENV_TEMPLATES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.defaults",
}
_SENSITIVE_NAMES = {
    ".env",
    ".envrc",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials.json",
    "client_secret.json",
    "service-account.json",
}
_SENSITIVE_DIRS = {".ssh", ".aws", ".docker", ".gnupg", ".kube", "secrets"}
_SENSITIVE_STATE_DIRS = {".vortocode"}
_SENSITIVE_STATE_FILES = {"cli_session.json", "sessions.db", "sessions.db-shm", "sessions.db-wal"}
_SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore"}


def normalize_profile(profile: str | None) -> str:
    """Normalize a named profile; unknown values fail closed."""
    value = str(profile or "").strip().lower()
    return value if value in _PROFILE_CAPABILITIES else EXTERNAL_PROFILE


def _raw_sensitive_repo_path(path: object) -> bool:
    raw = str(path or "").strip().replace("\\", "/").lstrip("@/")
    if not raw:
        return False
    parts = [part.lower() for part in PurePosixPath(raw).parts if part not in {"", "."}]
    if not parts:
        return False
    name = parts[-1]
    if name in _SAFE_ENV_TEMPLATES:
        return False
    if (
        name in _SENSITIVE_NAMES
        or (name.startswith(".env.") and name not in _SAFE_ENV_TEMPLATES)
        or name.startswith("secrets.")
    ):
        return True
    if any(part in _SENSITIVE_DIRS or part in _SENSITIVE_STATE_DIRS for part in parts):
        return True
    if name in _SENSITIVE_STATE_FILES:
        return True
    return any(name.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def is_sensitive_repo_path(path: object, repo_root: str | None = None) -> bool:
    """Return whether a raw or resolved repository path can hold credentials/state."""
    if _raw_sensitive_repo_path(path):
        return True
    if not repo_root:
        return False
    raw = str(path or "").strip().lstrip("@")
    if not raw:
        return False
    try:
        root = Path(repo_root).resolve()
        resolved = (root / raw).resolve()
        rel = resolved.relative_to(root).as_posix()
    except (OSError, ValueError):
        return False
    return _raw_sensitive_repo_path(rel)


def _sensitive_arg(tool_name: str, args: dict, repo_root: str | None = None) -> str:
    keys = {
        "read_file": ("path",),
        "write_file": ("path",),
        "edit_file": ("path",),
        "document_symbols": ("path",),
        "list_files": ("dir",),
        "glob": ("dir",),
        "grep": ("dir",),
    }.get(tool_name, ())
    for key in keys:
        value = args.get(key)
        if value and is_sensitive_repo_path(value, repo_root):
            return str(value)
    return ""


@dataclass(frozen=True)
class SessionCapabilities:
    """Immutable-profile capability gate shared by one logical session."""

    profile: str
    repo_root: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile", normalize_profile(self.profile))

    @classmethod
    def for_profile(
        cls, profile: str | None, repo_root: str | None = None
    ) -> "SessionCapabilities":
        return cls(normalize_profile(profile), str(repo_root) if repo_root is not None else None)

    @property
    def allowed(self) -> frozenset[str]:
        return _PROFILE_CAPABILITIES[self.profile]

    def denied(
        self,
        tool_name: str,
        args: dict,
        required: Iterable[str] = (),
        *,
        external_content: bool = False,
    ) -> str | None:
        """Return a stable denial reason, or ``None`` when the call is allowed."""
        needed = set(required or ())
        sensitive = _sensitive_arg(tool_name, args, self.repo_root)
        if sensitive:
            needed.add(SENSITIVE_FILES)
        if external_content:
            needed.add(EXTERNAL_CONTENT)
        missing = sorted(needed - self.allowed)
        if not missing:
            return None
        if EXTERNAL_CONTENT in missing:
            return (
                f"会话能力 profile={self.profile} 不允许摄入外部内容；请新建 external 会话后再调用 "
                f"{tool_name}，不要把持凭据上下文带过信任边界"
            )
        detail = f"（敏感路径 {sensitive}）" if sensitive else ""
        return (
            f"会话能力 profile={self.profile} 缺少 {', '.join(missing)}{detail}；"
            "外部内容/无人值守会话不能通过确认、allow 规则或模式切换获得凭据能力"
        )

    def system_notice(self) -> str:
        caps = ", ".join(sorted(self.allowed)) or "none"
        if self.profile == LOCAL_PROFILE:
            rule = "可使用本机凭据能力，但禁止 Web/MCP 外部内容；外部调研请新建 external 会话。"
        else:
            rule = "可读取外部内容，但禁止宿主进程、认证远端和敏感文件能力。"
        return f"【会话能力】profile={self.profile}; capabilities={caps}。{rule}"

    def snapshot(self) -> dict[str, object]:
        return {"version": 1, "profile": self.profile}
