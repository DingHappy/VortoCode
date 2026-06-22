"""只读 git 工具（git_status / show_diff，进 build_read_tools）—— 真 temp git 仓库，不触网。"""

import subprocess

import pytest

from src.agents.main_agent import build_read_tools


def _git(path, *a):
    return subprocess.run(["git", "-C", str(path), *a], capture_output=True, text=True, check=True)


def _init(path):
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")


def _tools(path):
    return {t.name: t for t in build_read_tools(str(path))}


def test_git_tools_registered_and_readonly(tmp_path):
    by = _tools(tmp_path)
    assert "git_status" in by and "show_diff" in by
    assert by["git_status"].read_only and by["show_diff"].read_only


@pytest.mark.asyncio
async def test_git_status_clean_and_dirty(tmp_path):
    _init(tmp_path)
    by = _tools(tmp_path)
    assert "干净" in await by["git_status"].handler({})           # 干净
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    out = await by["git_status"].handler({})
    assert "a.py" in out                                          # 改了文件 → 列出


@pytest.mark.asyncio
async def test_show_diff_working_tree(tmp_path):
    _init(tmp_path)
    (tmp_path / "a.py").write_text("x = 99\n", encoding="utf-8")
    out = await _tools(tmp_path)["show_diff"].handler({})
    assert "a.py" in out and "x = 99" in out and "-x = 1" in out  # stat + 内容


@pytest.mark.asyncio
async def test_show_diff_no_changes(tmp_path):
    _init(tmp_path)
    assert "无改动" in await _tools(tmp_path)["show_diff"].handler({})


@pytest.mark.asyncio
async def test_show_diff_branch_range(tmp_path):
    _init(tmp_path)
    # 默认分支名随 git 配置（main 或 master）——动态取，别硬编码
    base = _git(tmp_path, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    # 造一个 vorto 分支，改点东西，用 base...vorto/x 看它带来的改动（不 checkout）
    _git(tmp_path, "checkout", "-q", "-b", "vorto/x")
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    _git(tmp_path, "commit", "-q", "-am", "add y")
    _git(tmp_path, "checkout", "-q", base)
    out = await _tools(tmp_path)["show_diff"].handler({"ref": f"{base}...vorto/x"})
    assert "y = 2" in out                                         # 分支独有的改动可见
    # 工作区本身干净
    assert "干净" in await _tools(tmp_path)["git_status"].handler({})


@pytest.mark.asyncio
async def test_show_diff_bad_ref(tmp_path):
    _init(tmp_path)
    out = await _tools(tmp_path)["show_diff"].handler({"ref": "no-such-ref-xyz"})
    assert "出错" in out or "无改动" in out                        # 友好兜底，不抛
