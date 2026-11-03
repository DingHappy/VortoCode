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
# 研究员：给同事用的资料助手——查资料、整理、可在**自己的沙盒工作区**里写脚本辅助收集，
# 但不碰主项目代码、不外发。凭据维度与 external 同样严（碰不到主机进程/认证远端/敏感文件），
# 单独命名是为了**审计能一眼看出这是谁的会话**——与 unattended 单独命名同一个理由。
RESEARCHER_PROFILE = "researcher"
# Private-project research has a tool boundary in addition to credential isolation.
# The older researcher profile intentionally permits scripts and is not this policy.
RESEARCH_READ_PROFILE = "research_read"
RESEARCH_READ_TOOLS = frozenset({"read_file", "list_files", "grep"})
# 主人终端（手机 App）发起的回合（ADR-012）：手机入口在公网上，签名只证明「是你的手机」，不证明
# 这一步该交给公网入口做。所以只开：读与检索、查资料（外部内容照常打污点）、改你自己的事项与记忆
# （OWN_DATA，可撤销）。命令执行、写文件、操控电脑与浏览器、派开发任务、对外发送一律不在这里做——
# 对外发送以后以审批卡形式出现（阶段 B），现在请到 Desktop 上做。
OWNER_DEVICE_PROFILE = "owner_device"
OWNER_DEVICE_WRITE_TOOLS = frozenset({"personal_goal_create", "personal_goal_update", "save_memory",
                                      # 只是「拟」：任务单要你在手机上 Face ID 下发，Codex 的话要你 Face ID 批准才发
                                      "propose_task", "propose_codex_message", "propose_codex_interrupt",
                                      # 任务小组（ADR-014）：开小组 / 加预算按「执行」类问人，回话 / 验收按 OWN_DATA
                                      "team_propose", "team_reply", "team_decide", "team_add_budget", "team_stop"})
# 即使申报只读也不开：操控 / 截取电脑屏幕、发送文件与图片、读宿主任意文件、派子任务或给执行中的任务补指令、运行技能脚本
_OWNER_DEVICE_DENIED_PREFIXES = ("desktop_", "dev_", "send_")
_OWNER_DEVICE_DENIED_TOOLS = frozenset({"read_local_file", "screenshot_page", "task", "research_parallel",
                                        "task_followup", "task_review", "task_acknowledge",
                                        "use_personal_skill", "use_skill", "pr_fix"})

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
    # Researcher assistants ingest a lot of Web content by design but must never
    # reach host credentials or secret-bearing files: same capability set as
    # external, named separately so audit trails identify whose session it was.
    RESEARCHER_PROFILE: frozenset({EXTERNAL_CONTENT}),
    RESEARCH_READ_PROFILE: frozenset({EXTERNAL_CONTENT}),
    # 主人终端（App）：能查资料，碰不到宿主进程、认证远端和敏感文件；工具面另有白名单（见 denied）。
    OWNER_DEVICE_PROFILE: frozenset({EXTERNAL_CONTENT}),
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


def _owner_device_denied(tool_name: str, *, read_only: bool, outward: bool) -> str | None:
    if outward:
        return "手机上不能直接对外发送；请到 Desktop 上确认这一步（手机上的审批卡稍后接入）"
    if tool_name in _OWNER_DEVICE_DENIED_TOOLS or tool_name.startswith(_OWNER_DEVICE_DENIED_PREFIXES):
        return f"手机发起的对话不能使用 {tool_name}；需要的话请到 Desktop 上做"
    if not read_only and tool_name not in OWNER_DEVICE_WRITE_TOOLS:
        return "手机发起的对话只能改你自己的事项和记忆；写文件、执行命令等请到 Desktop 上做"
    return None


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
        read_only: bool = True,
        outward: bool = False,
    ) -> str | None:
        """Return a stable denial reason, or ``None`` when the call is allowed."""
        if self.profile == RESEARCH_READ_PROFILE and (
            tool_name not in RESEARCH_READ_TOOLS or not read_only or outward
        ):
            return "只读研究策略仅允许项目内 read_file/list_files/grep；不能升级、出网、执行或派活"
        if self.profile == OWNER_DEVICE_PROFILE:
            reason = _owner_device_denied(tool_name, read_only=read_only, outward=outward)
            if reason:
                return reason
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
        if self.profile == RESEARCH_READ_PROFILE:
            rule = "仅允许项目内文件读取与检索；无写入、命令、联网、凭据、MCP 或委派权限。"
        elif self.profile == OWNER_DEVICE_PROFILE:
            rule = ("来自主人的手机：可读取与检索、查资料、改主人自己的事项与记忆；"
                    "不能执行命令、写文件、操控电脑或浏览器、派开发任务或对外发送——这些请主人到 Desktop 上做。")
        elif self.profile == LOCAL_PROFILE:
            rule = "可使用本机凭据能力，但禁止 Web/MCP 外部内容；外部调研请新建 external 会话。"
        else:
            rule = "可读取外部内容，但禁止宿主进程、认证远端和敏感文件能力。"
        return f"【会话能力】profile={self.profile}; capabilities={caps}。{rule}"

    def snapshot(self) -> dict[str, object]:
        return {"version": 1, "profile": self.profile}
