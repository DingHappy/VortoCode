"""路径边界防护：读/写/导航工具都不得越出仓库根（防提示注入 → 读仓库外密钥 → 外发）。

回归 2026-07 审计 P0#1：此前写工具（edit_file/write_file）用 resolve()+relative_to 防了 `..`/
绝对路径，但读工具 read_file / document_symbols / glob(dir) 直接 `Path(repo_root)/rel` 无校验，
能读仓库外任意文件。现读写共用 _resolve_within，行为一致。撤回修复即 FAIL。
"""

import pytest

from src.agents.main_agent import build_read_tools, build_write_tools, _resolve_within


def _read(root):
    return {t.name: t for t in build_read_tools(str(root))}


def _write(root):
    return {t.name: t for t in build_write_tools(str(root))}


# ---- _resolve_within 单元行为 ----

def test_resolve_within_rejects_parent_absolute_and_accepts_inside(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "ok.py").write_text("x = 1\n", encoding="utf-8")
    assert _resolve_within(repo, "ok.py") is not None            # 仓库内：放行
    assert _resolve_within(repo, "../../etc/passwd") is None      # `..` 越界：拒
    assert _resolve_within(repo, "/etc/passwd") is None           # 绝对路径：拒
    assert _resolve_within(repo, "  ") is None                    # 空：拒
    assert _resolve_within(repo, "@ok.py") is not None            # 去掉 @ 提及前缀仍在仓库内


# ---- read_file ----

@pytest.mark.asyncio
async def test_read_file_blocks_parent_traversal(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-KEY", encoding="utf-8")        # 仓库外的密钥文件
    out = await _read(repo)["read_file"].handler({"path": "../secret.txt"})
    assert "越界" in out and "TOP-SECRET-KEY" not in out


@pytest.mark.asyncio
async def test_read_file_blocks_absolute_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-KEY", encoding="utf-8")
    out = await _read(repo)["read_file"].handler({"path": str(secret)})
    assert "越界" in out and "TOP-SECRET-KEY" not in out


@pytest.mark.asyncio
async def test_read_file_inside_repo_still_works(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "in.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    out = await _read(repo)["read_file"].handler({"path": "in.py"})
    assert out == "a = 1\nb = 2\n"                                # 仓库内正常读，未被防护误伤


# ---- glob(dir) ----

@pytest.mark.asyncio
async def test_glob_blocks_dir_traversal(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "outside.py").write_text("x = 1\n", encoding="utf-8")
    out = await _read(repo)["glob"].handler({"pattern": "*.py", "dir": "../"})
    assert "越界" in out                                          # dir 指向仓库外：拒，不列 outside.py


# ---- document_symbols ----

@pytest.mark.asyncio
async def test_document_symbols_blocks_traversal(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    out = await _read(repo)["document_symbols"].handler({"path": "../../etc/hosts.py"})
    assert "越界" in out


# ---- write 工具：共享 helper 后仍防越界（回归） ----

@pytest.mark.asyncio
async def test_write_file_blocks_traversal_and_creates_nothing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    escaped = tmp_path / "escape.txt"
    out = await _write(repo)["write_file"].handler({"path": "../escape.txt", "content": "pwn"})
    assert "越界" in out and not escaped.exists()                 # 拒绝且没在仓库外落盘


@pytest.mark.asyncio
async def test_write_file_inside_repo_still_works(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    out = await _write(repo)["write_file"].handler({"path": "sub/new.txt", "content": "hi"})
    assert "新建" in out and (repo / "sub" / "new.txt").read_text(encoding="utf-8") == "hi"


@pytest.mark.asyncio
async def test_edit_file_blocks_traversal(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    out = await _write(repo)["edit_file"].handler(
        {"path": "../../etc/passwd", "old": "root", "new": "x"})
    assert "越界" in out
