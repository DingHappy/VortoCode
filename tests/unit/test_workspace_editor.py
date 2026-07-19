import os
import subprocess

import pytest

from src.agents.workspace_editor import (
    WorkspaceEditError,
    apply_workspace_edit,
    content_sha256,
    prepare_workspace_edit,
)


def _hash(text: str) -> str:
    return content_sha256(text.encode("utf-8"))


def _git_init(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def test_workspace_edit_prepares_diff_and_preserves_mode(tmp_path):
    _git_init(tmp_path)
    path = tmp_path / "src" / "main.py"
    path.parent.mkdir()
    path.write_text("answer = 1\n", encoding="utf-8")
    os.chmod(path, 0o744)

    prepared = prepare_workspace_edit(tmp_path, "src/main.py", "answer = 2\n", _hash("answer = 1\n"))

    assert "--- a/src/main.py" in prepared.diff
    assert "+answer = 2" in prepared.diff
    new_hash = apply_workspace_edit(prepared)
    assert path.read_text(encoding="utf-8") == "answer = 2\n"
    assert new_hash == _hash("answer = 2\n")
    assert path.stat().st_mode & 0o777 == 0o744


def test_workspace_edit_rejects_stale_open_and_confirmation_races(tmp_path):
    _git_init(tmp_path)
    path = tmp_path / "main.py"
    path.write_text("one\n", encoding="utf-8")
    with pytest.raises(WorkspaceEditError, match="编辑期间"):
        prepare_workspace_edit(tmp_path, "main.py", "two\n", _hash("old\n"))

    prepared = prepare_workspace_edit(tmp_path, "main.py", "two\n", _hash("one\n"))
    path.write_text("three\n", encoding="utf-8")
    with pytest.raises(WorkspaceEditError, match="确认期间"):
        apply_workspace_edit(prepared)
    assert path.read_text(encoding="utf-8") == "three\n"


def test_workspace_edit_rejects_traversal_binary_and_symlink(tmp_path):
    _git_init(tmp_path)
    path = tmp_path / "main.py"
    path.write_text("safe\n", encoding="utf-8")
    with pytest.raises(WorkspaceEditError, match="越界"):
        prepare_workspace_edit(tmp_path, "../escape.py", "x", _hash("safe\n"))
    with pytest.raises(WorkspaceEditError, match="NUL"):
        prepare_workspace_edit(tmp_path, "main.py", "bad\0text", _hash("safe\n"))

    if hasattr(os, "symlink"):
        alias = tmp_path / "alias.py"
        alias.symlink_to(path)
        with pytest.raises(WorkspaceEditError, match="符号链接"):
            prepare_workspace_edit(tmp_path, "alias.py", "x", _hash("safe\n"))


def test_workspace_edit_rejects_git_internal_and_ignored_files(tmp_path):
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text("*.secret\n", encoding="utf-8")
    ignored = tmp_path / "token.secret"
    ignored.write_text("hidden\n", encoding="utf-8")
    with pytest.raises(WorkspaceEditError, match="Git tracked/untracked"):
        prepare_workspace_edit(tmp_path, "token.secret", "changed\n", _hash("hidden\n"))
    config = tmp_path / ".git" / "config"
    with pytest.raises(WorkspaceEditError, match="Git tracked/untracked"):
        prepare_workspace_edit(
            tmp_path, ".git/config", config.read_text(encoding="utf-8"),
            content_sha256(config.read_bytes()),
        )
