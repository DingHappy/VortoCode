"""状态目录 `.vortocode` → `.vortoone`（ADR-018 批次 C）：第一次访问时整目录改名、原处留链接。"""
import os
import shutil
import subprocess

import pytest

from src.agents.capabilities import is_sensitive_repo_path
from src.utils import state_dir as sd

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")


@pytest.fixture(autouse=True)
def _migration_on(monkeypatch):
    monkeypatch.delenv("VORTOONE_STATE_DIR_MIGRATION", raising=False)


def test_migration_switched_off_only_locates(tmp_path, monkeypatch):
    """关掉搬家：只认位置，什么都不改（测试套件默认这样，见 conftest）。"""
    monkeypatch.setenv("VORTOONE_STATE_DIR_MIGRATION", "off")
    (tmp_path / ".vortocode").mkdir()
    assert sd.state_dir(tmp_path) == tmp_path / ".vortocode"
    assert not (tmp_path / ".vortocode").is_symlink() and not (tmp_path / ".vortoone").exists()
    monkeypatch.delenv("VORTOONE_STATE_DIR_MIGRATION")
    assert sd.state_dir(tmp_path) == tmp_path / ".vortoone"
    monkeypatch.setenv("VORTOONE_STATE_DIR_MIGRATION", "off")
    assert sd.state_dir(tmp_path) == tmp_path / ".vortoone"            # 搬过之后关掉也认新目录


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def _repo(root):
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "f.py").write_text("x = 1\n")


def test_legacy_directory_moves_and_leaves_a_link(tmp_path):
    (tmp_path / ".vortocode" / "goals").mkdir(parents=True)
    (tmp_path / ".vortocode" / "goals" / "g.json").write_text("{}")
    new = sd.state_dir(tmp_path)
    assert new == tmp_path / ".vortoone" and (new / "goals" / "g.json").read_text() == "{}"
    link = tmp_path / ".vortocode"
    assert link.is_symlink() and os.readlink(link) == ".vortoone"
    assert (link / "goals" / "g.json").read_text() == "{}"                  # 存着的旧绝对路径照样能打开
    assert sd.state_dir(tmp_path) == new                                     # 再来一次不出事


def test_nothing_is_created_when_there_is_no_state_yet(tmp_path):
    assert sd.state_dir(tmp_path) == tmp_path / ".vortoone"
    assert list(tmp_path.iterdir()) == []


def test_when_both_exist_the_new_one_wins_and_the_old_one_is_left_alone(tmp_path, caplog):
    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "old.txt").write_text("old")
    (tmp_path / ".vortoone").mkdir()
    assert sd.state_dir(tmp_path) == tmp_path / ".vortoone"
    assert (tmp_path / ".vortocode" / "old.txt").read_text() == "old" and not (tmp_path / ".vortocode").is_symlink()
    assert "都在" in caplog.text


@pytest.mark.skipif(os.geteuid() == 0, reason="root 不受目录权限限制")
def test_a_directory_that_cannot_be_moved_keeps_being_used(tmp_path):
    (tmp_path / ".vortocode").mkdir()
    tmp_path.chmod(0o555)
    try:
        assert sd.state_dir(tmp_path) == tmp_path / ".vortocode"
    finally:
        tmp_path.chmod(0o755)


@needs_git
def test_repository_with_committed_state_files_is_not_moved(tmp_path):
    """主人把 .vortocode/permissions.yaml 提交进了仓库：改名会让它在 git 里变成删除，所以不搬。"""
    _repo(tmp_path)
    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "permissions.yaml").write_text("deny: [shell]\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    assert sd.state_dir(tmp_path) == tmp_path / ".vortocode"
    assert not (tmp_path / ".vortoone").exists()
    assert _git(tmp_path, "status", "--porcelain") == ""


@needs_git
def test_ignored_state_stays_invisible_to_git_status_after_the_move(tmp_path):
    _repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".vortocode/\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "permissions.yaml").write_text("deny: [shell]\n")
    assert _git(tmp_path, "status", "--porcelain") == ""
    assert sd.state_dir(tmp_path) == tmp_path / ".vortoone"
    assert _git(tmp_path, "status", "--porcelain") == ""                    # 新目录与留下的链接都不冒出来
    exclude = (tmp_path / ".git" / "info" / "exclude").read_text()
    assert "/.vortocode" in exclude and "/.vortoone/" in exclude


@needs_git
def test_unignored_state_shows_up_under_the_new_name_only(tmp_path):
    """旧目录本来就在 git status 里（用户配置可以自己 add）：搬过之后照旧，只是换成新名字，链接不冒出来。"""
    _repo(tmp_path)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "AGENTS.md").write_text("本仓库的私有指令\n")
    sd.state_dir(tmp_path)
    assert _git(tmp_path, "status", "--porcelain").splitlines() == ["?? .vortoone/"]


def test_reaching_state_through_the_legacy_link_is_still_sensitive(tmp_path):
    """旧名字是链接：经它访问的钩子、权限配置照样算敏感路径，不能借旧名字绕过。"""
    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "hooks.yaml").write_text("hooks: []\n")
    sd.state_dir(tmp_path)
    for raw in (".vortocode/hooks.yaml", ".vortoone/hooks.yaml", "./.vortocode/../.vortoone/permissions.yaml"):
        assert is_sensitive_repo_path(raw, str(tmp_path)), raw


def test_application_support_and_xdg_directories_move_too(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    support = tmp_path / "Library" / "Application Support"
    (support / "VortoCode").mkdir(parents=True)
    (support / "VortoCode" / "trusted-folders.json").write_text("[]")
    assert sd.app_support_dir() == support / "VortoOne"
    assert (support / "VortoOne" / "trusted-folders.json").read_text() == "[]"
    assert (support / "VortoCode").is_symlink()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "xdg" / "vortocode").mkdir(parents=True)
    assert sd.xdg_dir("XDG_CONFIG_HOME", tmp_path / "unused") == tmp_path / "xdg" / "vortoone"
    assert (tmp_path / "xdg" / "vortocode").is_symlink()


def test_concurrent_movers_end_up_with_one_directory_and_one_link(tmp_path, monkeypatch):
    """两个进程同时搬：后到的那个 rename 落空（旧目录已经没了），照样拿到新目录。"""
    (tmp_path / ".vortocode").mkdir()
    real_rename = os.rename

    def racing_rename(src, dst):
        real_rename(src, dst)                      # 「别的进程」抢先搬完
        raise FileNotFoundError(src)

    monkeypatch.setattr(sd.os, "rename", racing_rename)
    assert sd.state_dir(tmp_path) == tmp_path / ".vortoone"
    assert (tmp_path / ".vortoone").is_dir()


def test_importing_modules_never_moves_anything(tmp_path):
    """搬家只在用到时发生，导入模块不许有这种副作用（曾有两个模块级常量在导入时定位家目录）。"""
    import sys
    from pathlib import Path
    (tmp_path / ".vortocode").mkdir()
    root = Path(__file__).resolve().parents[2]
    env = {k: v for k, v in os.environ.items() if k != "VORTOONE_STATE_DIR_MIGRATION"}
    env["HOME"] = str(tmp_path)
    subprocess.run([sys.executable, "-c", "import src.cli, src.agents.project, src.browser.cdp, src.web.server"],
                   cwd=root, env=env, check=True, capture_output=True)
    assert (tmp_path / ".vortocode").is_dir() and not (tmp_path / ".vortocode").is_symlink()
    assert not (tmp_path / ".vortoone").exists()
