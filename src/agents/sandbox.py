"""OS-level sandbox policy for shell and autonomous verification commands.

The policy is deliberately separate from command approval:

* ``auto`` (default) uses an available OS sandbox. Interactive commands may
  fall back to the host with explicit evidence; unattended execution may not.
* ``required`` fails closed when no supported sandbox is available.
* ``off`` is the only way to authorize host execution without isolation.

macOS uses Seatbelt (``sandbox-exec``); Linux uses bubblewrap (``bwrap``).
This batch restricts writes to the repository and temporary directories while
leaving reads and networking unchanged. Network and credential isolation are
separate trust-foundation batches.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Sequence

_TEMP_WRITE_SUBPATHS = ("/private/tmp", "/tmp", "/private/var/folders", "/var/folders")
_TEMP_WRITE_LITERALS = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/dtracehelper")
_TRUE = frozenset({"1", "true", "yes", "y", "on"})
_FALSE = frozenset({"0", "false", "no", "n", "off"})
_POLICIES = frozenset({"auto", "required", "off"})


@dataclass(frozen=True)
class SandboxDecision:
    policy: str
    backend: str
    allowed: bool
    isolated: bool
    fallback: bool
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _policy_value() -> tuple[str, str]:
    raw = str(os.getenv("VORTOCODE_SANDBOX") or "").strip().lower()
    if not raw:
        return "auto", ""
    if raw in _TRUE:
        return "required", ""  # legacy VORTOCODE_SANDBOX=1 meant explicitly enabled
    if raw in _FALSE:
        return "off", ""
    if raw in _POLICIES:
        return raw, ""
    return "invalid", (
        f"VORTOCODE_SANDBOX={raw!r} 无效；仅支持 auto / required / off "
        "（兼容 1/0、true/false）")


def sandbox_policy() -> str:
    """Return the normalized configured policy (``invalid`` for bad values)."""
    return _policy_value()[0]


@lru_cache(maxsize=8)
def _probe_backend(backend: str, binary: str) -> bool:
    """Run a no-op inside the backend; a binary can exist but be unusable in nested sandboxes."""
    if backend == "seatbelt":
        argv = [binary, "-p", "(version 1)\n(allow default)\n", "/usr/bin/true"]
    elif backend == "bubblewrap":
        argv = [binary, "--die-with-parent", "--ro-bind", "/", "/", "/bin/true"]
    else:
        return False
    try:
        return subprocess.run(argv, capture_output=True, timeout=3).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def sandbox_backend() -> str:
    """Return a present *and runnable* backend: seatbelt, bubblewrap, or empty."""
    if sys.platform == "darwin":
        binary = shutil.which("sandbox-exec")
        if binary and _probe_backend("seatbelt", binary):
            return "seatbelt"
    if sys.platform.startswith("linux"):
        binary = shutil.which("bwrap")
        if binary and _probe_backend("bubblewrap", binary):
            return "bubblewrap"
    return ""


def sandbox_available() -> bool:
    return bool(sandbox_backend())


def sandbox_install_hint() -> str:
    if sys.platform == "darwin":
        return "当前 macOS 缺少或无法启动 sandbox-exec（嵌套沙箱可能禁止）"
    if sys.platform.startswith("linux"):
        return "请安装并启用 bubblewrap（Ubuntu/Debian: apt install bubblewrap；需允许 user namespace）"
    return f"当前平台 {sys.platform} 尚无受支持的 OS 沙箱"


def resolve_sandbox(*, require_isolation: bool = False) -> SandboxDecision:
    """Resolve policy and platform into one auditable execution decision.

    ``require_isolation`` is used for unattended/generated-code paths. Explicit
    ``off`` remains an escape hatch for a trusted environment; ``auto`` may
    only fall back for an interactive, confirmed command.
    """
    policy, error = _policy_value()
    if error:
        return SandboxDecision(policy, "", False, False, False, error)
    if policy == "off":
        return SandboxDecision(
            policy, "", True, False, False,
            "⚠ OS 沙箱已由 VORTOCODE_SANDBOX=off 显式关闭；命令将在宿主机执行。",
        )
    backend = sandbox_backend()
    if backend:
        return SandboxDecision(
            policy, backend, True, True, False,
            f"OS 沙箱已启用（{backend}）。",
        )
    hint = sandbox_install_hint()
    if policy == "required" or require_isolation:
        why = "无人值守执行要求隔离" if require_isolation and policy == "auto" else "策略要求隔离"
        return SandboxDecision(
            policy, "", False, False, False,
            f"{why}，但 OS 沙箱不可用：{hint}。"
            "如确认环境可信，可显式设置 VORTOCODE_SANDBOX=off。",
        )
    return SandboxDecision(
        policy, "", True, False, True,
        f"⚠ OS 沙箱不可用，交互命令将显式降级到宿主机：{hint}。"
        "可设 VORTOCODE_SANDBOX=required 禁止降级。",
    )


def sandbox_enabled() -> bool:
    """Backward-compatible boolean view of the current interactive decision."""
    return resolve_sandbox().isolated


def _sbpl_quote(path: str) -> str:
    return path.replace("\\", "\\\\").replace('"', '\\"')


def build_sandbox_profile(repo_root: str) -> str:
    """Build the macOS Seatbelt profile (writes: repo + temp; reads/network: allowed)."""
    root = os.path.realpath(repo_root)
    write_subpaths = [root, *_TEMP_WRITE_SUBPATHS]
    allow_subpaths = "\n  ".join(f'(subpath "{_sbpl_quote(p)}")' for p in write_subpaths)
    allow_literals = "\n  ".join(f'(literal "{_sbpl_quote(p)}")' for p in _TEMP_WRITE_LITERALS)
    return (
        "(version 1)\n"
        "(allow default)\n"
        "(deny file-write*)\n"
        "(allow file-write*\n"
        f"  {allow_subpaths}\n"
        f"  {allow_literals})\n"
    )


def _bubblewrap_prefix(repo_root: str) -> list[str]:
    root = os.path.realpath(repo_root)
    argv = [
        "bwrap", "--die-with-parent", "--new-session",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
    ]
    # Match the existing Seatbelt contract: temporary directories remain
    # writable for compilers/package tools. Add these before the repo bind so a
    # repository under /tmp can still be rebound explicitly below.
    for temp in ("/tmp", "/var/tmp"):
        if os.path.isdir(temp):
            argv.extend(["--bind", temp, temp])
    argv.extend(["--bind", root, root, "--chdir", root])
    return argv


def sandboxed_exec_argv(repo_root: str, argv: Sequence[str], *, backend: str = "") -> list[str]:
    """Wrap an argv command in the selected OS sandbox."""
    selected = backend or sandbox_backend()
    command = [str(item) for item in argv]
    if selected == "seatbelt":
        return ["sandbox-exec", "-p", build_sandbox_profile(repo_root), *command]
    if selected == "bubblewrap":
        return [*_bubblewrap_prefix(repo_root), *command]
    raise RuntimeError("OS 沙箱不可用，无法构造隔离命令")


def sandboxed_argv(repo_root: str, cmd: str, *, backend: str = "") -> list[str]:
    """Wrap a shell command string in the selected OS sandbox."""
    return sandboxed_exec_argv(repo_root, ["/bin/sh", "-c", cmd], backend=backend)


# ── 子进程凭据隔离（信任地基）───────────────────────────────────────────────
# 起子进程默认继承主进程整套 env，而本沙箱放行出站网络（reads/network 不变，见模块头）——
# 两半合起来，一条投毒的 package.json script / Makefile target 就能把中转站 key 外带。
# 剥掉「VortoCode 自己用的操作密钥」即切断这条链：任何被起的子命令都不需要它们。
#
# 精确名单，刻意不用 *KEY*/*TOKEN* 通配符——本仓 env 里就有会被误伤的假阳性：
# VORTOCODE_CRON_TOKEN_BUDGET（数字预算）、VORTOCODE_MAX_CONTEXT_TOKENS、
# TEXTUAL_DISABLE_KITTY_KEY（UI 开关）。GITHUB_TOKEN 是「任务令牌」（gh 子命令要用），
# 故不在此列——它的穿透由下方 allowlist 决定。
_STRIPPED_ENV_KEYS = frozenset({
    "OPENAI_API_KEY",                                    # LLM 中转站钥匙
    "VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN",          # Web 永久 admin 主钥（含旧名）
    "VORTOCODE_RELAY_ADMIN_TOKEN",                       # 中转站 admin
    "VORTOCODE_DD_CLIENT_SECRET", "VORTOCODE_TG_TOKEN",  # IM 桥接凭据
})


def _env_passthrough_allowlist() -> frozenset[str]:
    """主人显式放行、准许穿透到子进程的变量名（逗号分隔）。

    走环境变量 VORTOCODE_ENV_PASSTHROUGH 而非 .vortocode/ 文件：能设主进程 env 的才是
    主人；若放仓库里，凡能写仓库的（含被投毒的生成代码）就能把密钥重新放行，等于多开
    一个投毒面。典型用途：目标项目自身用 OPENAI_API_KEY、其测试子进程需要它。
    变量名大小写敏感（Unix env 语义）：须与真实变量名一字不差，写错则 fail-closed（仍剥）。
    """
    raw = os.getenv("VORTOCODE_ENV_PASSTHROUGH", "")
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


def child_env() -> dict[str, str]:
    """构造子进程环境：主进程 env 去掉操作密钥，但保留 allowlist 放行项。

    所有起子进程处（run_command / run_command_background / run_tests）都必须用它——沙箱
    与非沙箱路径同样适用，VORTOCODE_SANDBOX=off 的宿主机 escape hatch 不能变成密钥泄漏
    口。PATH/HOME/代理/语言环境等一律保留，故 sandbox-exec/bwrap wrapper 与工具链不受影响。
    """
    allow = _env_passthrough_allowlist()
    return {k: v for k, v in os.environ.items()
            if k not in _STRIPPED_ENV_KEYS or k in allow}
