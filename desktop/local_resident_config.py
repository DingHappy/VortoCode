"""Load a native-managed resident snapshot before importing any runtime modules.

This launch mode owns no tasks, approval decisions or scheduler. All of those
remain in the original server. The file contains secrets and is never a source
of arbitrary commands or new capabilities.
"""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import stat
import socket
from typing import MutableMapping
import uuid

_MAX_CONFIG_BYTES = 64 * 1024
_MODEL_NAMES = frozenset(
    {
        "OPENAI_API_BASE",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "DEFAULT_MODEL",
        "LLM_MODEL_BALANCED",
        "LLM_MODEL_CHEAP",
        "LLM_MODEL_POWERFUL",
        "VORTOONE_MODEL_CHOICES",
        "VORTOONE_MODEL_ROLES",
        "VORTOONE_EXTERNAL_WORKERS",
        "VORTOONE_MODEL_CONTEXT_WINDOW",
        "VORTOONE_MODEL_CONTEXT_WINDOW_SOURCE",
    }
)
_PROVIDER_NAME = re.compile(r"VORTOONE_PROVIDER_[A-Z][A-Z0-9_]{0,23}_(BASE|KEY)")
_IDENTITY = re.compile(r"[a-zA-Z0-9-]{1,96}")


def _read_private(path: Path) -> dict:
    """Refuse links, non-owner files, shared directories, and unbounded input."""
    try:
        parent = path.parent.stat()
        if (
            not path.is_absolute()
            or path.parent.resolve(strict=True) != path.parent
            or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) != 0o700
        ):
            raise ValueError("本机常驻配置目录不属于当前用户或权限不安全")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > _MAX_CONFIG_BYTES
            ):
                raise ValueError("本机常驻配置文件不属于当前用户或权限不安全")
            raw = stream.read(_MAX_CONFIG_BYTES + 1)
        if len(raw) > _MAX_CONFIG_BYTES:
            raise ValueError("本机常驻配置过大")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("无法安全读取本机常驻配置；请从 Desktop 核对后重试") from None
    if not isinstance(value, dict):
        raise ValueError("本机常驻配置格式无效")
    return value


def _validated(path: Path) -> dict:
    value = _read_private(path)
    if (
        type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
        or type(value.get("port")) is not int
        or not 1024 <= value["port"] <= 65535
        or type(value.get("cron_enabled")) is not bool
    ):
        raise ValueError("本机常驻配置版本或排期设置无效")
    for name in ("revision", "model_revision"):
        if not isinstance(value.get(name), str) or not _IDENTITY.fullmatch(value[name]):
            raise ValueError("本机常驻配置代次无效")
    version = value.get("runtime_version")
    token = value.get("token")
    if (
        not isinstance(version, str)
        or not 1 <= len(version) <= 128
        or not isinstance(token, str)
        or not 32 <= len(token) <= 256
        or not all(0x21 <= ord(char) <= 0x7E for char in token)
    ):
        raise ValueError("本机常驻版本或访问凭据无效")
    root = value.get("workspace_root")
    if not isinstance(root, str) or "\0" in root:
        raise ValueError("本机常驻资料目录无效")
    workspace = Path(root)
    managed = path.parent
    if managed.parent.name == "generations":
        if managed.name != value["revision"]:
            raise ValueError("本机常驻配置代次与受管目录不符")
        managed = managed.parent.parent
    try:
        if (
            workspace != managed / "assistant"
            or workspace.resolve(strict=True) != workspace
            or not workspace.is_dir()
        ):
            raise ValueError("本机常驻资料目录不属于此安装")
    except OSError:
        raise ValueError("本机常驻资料目录不可用") from None
    model = value.get("model_env")
    if not isinstance(model, dict) or len(model) > 128:
        raise ValueError("本机常驻模型配置无效")
    for name, setting in model.items():
        if (
            name not in _MODEL_NAMES
            and not _PROVIDER_NAME.fullmatch(name)
            or setting is not None
            and (not isinstance(setting, str) or len(setting) > 8192 or "\0" in setting)
        ):
            raise ValueError("本机常驻模型配置含不支持的字段或值")
    if not all(
        isinstance(model.get(name), str) and model[name].strip()
        for name in ("OPENAI_API_BASE", "OPENAI_API_KEY", "DEFAULT_MODEL")
    ):
        raise ValueError("本机常驻模型配置尚未完成")
    return value


def prepare_resident(
    config_path: str | Path, *, environ: MutableMapping[str, str] | None = None
) -> tuple[Path, int]:
    """Validate first; then pin independent configuration and return the launch target."""
    value = _validated(Path(config_path))
    env = os.environ if environ is None else environ
    for name in list(env):
        if (
            name.startswith(
                ("VORTOONE_", "VORTOCODE_", "AUTODEV_", "LLM_", "OPENAI_", "ANTHROPIC_")
            )
            or name == "DEFAULT_MODEL"
        ):
            del env[name]
    env.update(
        {name: setting for name, setting in value["model_env"].items() if setting is not None}
    )
    env.update(
        {
            # No package or inherited dotenv may silently refill deliberately absent switches or keys.
            "PYTHON_DOTENV_DISABLED": "1",
            "VORTOONE_API_TOKEN": value["token"],
            "VORTOONE_WORKSPACE_SCOPE": "general",
            "VORTOONE_MODEL_CONFIGURATION_REVISION": value["model_revision"],
            "VORTOONE_CONFIGURATION_LAUNCH_ID": uuid.uuid4().hex,
            "VORTOONE_CRON": "1" if value["cron_enabled"] else "0",
            "VORTOONE_HEARTBEAT": "0",
            "VORTOONE_IM": "",
            "VORTOONE_IM_BINDINGS_FILE": "",
            "VORTOONE_SUPERVISOR_PID": "",
            "VORTOONE_DESKTOP_SIDECAR": "0",
            "VORTOONE_ENABLE_SHELL": "0",
            "VORTOONE_ENABLE_BROWSER": "0",
            "VORTOONE_ENABLE_DESKTOP_CONTROL": "0",
            "VORTOONE_ENABLE_ANDROID_CONTROL": "0",
            "VORTOONE_EXPERIMENTAL_EXTERNAL_WORKERS": "0",
        }
    )
    return Path(value["workspace_root"]), value["port"]


def _socket_directory_name(root: Path, uid: int) -> str:
    identity = hashlib.sha256(
        b"vortoone-local-resident-socket-v1\0"
        + str(uid).encode("ascii")
        + b"\0"
        + os.fsencode(root)
    ).hexdigest()[:16]
    return f"vortoone-resident-{uid}-{identity}"


def resident_socket_path(root: Path) -> Path:
    """Derive the same short endpoint as native code without inheriting TMPDIR."""
    if not root.is_absolute():
        raise ValueError("本机常驻资料目录必须是绝对路径")
    canonical = root.resolve(strict=True)
    path = (
        Path("/tmp").resolve(strict=True)
        / _socket_directory_name(canonical, os.getuid())
        / "service.sock"
    )
    if len(os.fsencode(path)) > 103:
        raise ValueError("本机常驻私有连接路径超过系统限制")
    return path


def prepare_resident_socket(workspace: Path) -> Path:
    """Create a private placeholder without unlinking a possibly active server.

    Uvicorn retains an existing socket's mode. Its lifespan acquires the original
    runtime lease before asyncio replaces the placeholder/stale socket, so a
    duplicate start cannot remove a live endpoint before ownership is checked.
    """
    parent = workspace.parent
    try:
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or parent.resolve() != parent
        ):
            raise ValueError("本机常驻连接目录权限不安全")
        path = resident_socket_path(parent)
        try:
            path.parent.mkdir(mode=0o700)
        except FileExistsError:
            pass
        private = path.parent.lstat()
        if (
            not stat.S_ISDIR(private.st_mode)
            or private.st_uid != os.getuid()
            or stat.S_IMODE(private.st_mode) != 0o700
            or path.parent.resolve() != path.parent
        ):
            raise ValueError("本机常驻私有连接目录已存在但权限不安全；未修改原目录")
        try:
            endpoint = path.lstat()
        except FileNotFoundError:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as placeholder:
                placeholder.bind(str(path))
                path.chmod(0o600)
            endpoint = path.lstat()
        if (
            not stat.S_ISSOCK(endpoint.st_mode)
            or endpoint.st_uid != os.getuid()
            or stat.S_IMODE(endpoint.st_mode) != 0o600
        ):
            raise ValueError("本机常驻连接不是受管的私有通道；未替换原文件")
        return path
    except OSError:
        raise ValueError("无法准备本机常驻私有连接；请核对安装目录和路径长度") from None
