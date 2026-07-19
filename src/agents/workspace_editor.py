"""Deterministic workspace edits for rich clients.

The client sends a relative path, full UTF-8 content, and the hash it opened.
This module prepares a reviewable diff and applies with a second compare-and-swap
check after confirmation. UI layers never receive an unrestricted filesystem API.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.agents.main_agent import _resolve_within

MAX_WORKSPACE_EDIT_BYTES = 1024 * 1024
MAX_WORKSPACE_DIFF_CHARS = 20_000
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class WorkspaceEditError(ValueError):
    """The proposed edit is invalid, stale, or outside the workspace boundary."""


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class PreparedWorkspaceEdit:
    root: Path
    path: Path
    relative_path: str
    expected_sha256: str
    content: bytes
    diff: str


def _workspace_file(root: Path, relative_path: str) -> tuple[Path, str]:
    raw = str(relative_path or "").strip().lstrip("@")
    path = _resolve_within(root, raw)
    if path is None:
        raise WorkspaceEditError("文件路径越界或非法")
    direct = root / raw
    if direct.is_symlink():
        raise WorkspaceEditError("源码编辑不允许覆盖符号链接")
    if not path.is_file():
        raise WorkspaceEditError("源码编辑目前只支持已存在的文本文件")
    relative = path.relative_to(root).as_posix()
    try:
        scoped = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(root), "ls-files", "-z",
             "--cached", "--others", "--exclude-standard", "--", relative],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise WorkspaceEditError(f"无法验证 Git 源码范围：{error}") from error
    listed = [item.decode("utf-8", errors="surrogateescape")
              for item in scoped.stdout.split(b"\0") if item]
    if scoped.returncode != 0 or relative not in listed:
        raise WorkspaceEditError("文件不在 Git tracked/untracked 且未忽略的源码范围内")
    return path, relative


def _read_current(path: Path) -> tuple[bytes, str]:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise WorkspaceEditError(f"无法读取当前文件：{error}") from error
    if len(data) > MAX_WORKSPACE_EDIT_BYTES:
        raise WorkspaceEditError("文件超过源码编辑上限（1 MiB）")
    if b"\0" in data:
        raise WorkspaceEditError("二进制文件不能在源码编辑器中保存")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkspaceEditError("文件不是有效 UTF-8 文本") from error
    return data, text


def prepare_workspace_edit(
    repo_root: str | Path,
    relative_path: str,
    content: str,
    expected_sha256: str,
) -> PreparedWorkspaceEdit:
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise WorkspaceEditError("项目目录不可用")
    expected = str(expected_sha256 or "").strip().lower()
    if _SHA256_RE.fullmatch(expected) is None:
        raise WorkspaceEditError("缺少有效的文件版本哈希，请重新打开文件")
    if not isinstance(content, str):
        raise WorkspaceEditError("源码内容必须是 UTF-8 文本")
    proposed = content.encode("utf-8")
    if len(proposed) > MAX_WORKSPACE_EDIT_BYTES:
        raise WorkspaceEditError("编辑内容超过保存上限（1 MiB）")
    if b"\0" in proposed:
        raise WorkspaceEditError("源码内容不能包含 NUL 字节")

    path, relative = _workspace_file(root, relative_path)
    current, before = _read_current(path)
    actual = content_sha256(current)
    if actual != expected:
        raise WorkspaceEditError("文件已在编辑期间发生变化；请重新加载后再保存")

    diff = "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        content.splitlines(keepends=True),
        fromfile=f"a/{relative}",
        tofile=f"b/{relative}",
    ))
    if len(diff) > MAX_WORKSPACE_DIFF_CHARS:
        diff = diff[:MAX_WORKSPACE_DIFF_CHARS] + "\n…（Desktop 编辑 diff 已截断）\n"
    return PreparedWorkspaceEdit(root, path, relative, expected, proposed, diff)


def apply_workspace_edit(prepared: PreparedWorkspaceEdit) -> str:
    """Apply after confirmation, checking the opened version again immediately before replace."""
    path, relative = _workspace_file(prepared.root, prepared.relative_path)
    if path != prepared.path or relative != prepared.relative_path:
        raise WorkspaceEditError("文件路径在确认期间发生变化；已拒绝保存")
    current, _text = _read_current(path)
    if content_sha256(current) != prepared.expected_sha256:
        raise WorkspaceEditError("文件在确认期间发生变化；已拒绝覆盖，请重新加载")
    if current == prepared.content:
        return prepared.expected_sha256

    mode = stat.S_IMODE(path.stat().st_mode)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.vortocode-", dir=path.parent, delete=False
        ) as handle:
            temporary = handle.name
            handle.write(prepared.content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        temporary = None
    except OSError as error:
        raise WorkspaceEditError(f"保存文件失败：{error}") from error
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    return content_sha256(prepared.content)
