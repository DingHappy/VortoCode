"""回合级工作区快照（src/memory/checkpoint.py，rewind v2）单测。

钉住的行为：影子仓库**绝不碰用户 .git**；快照覆盖 shell 副作用（不经工具写入的改动）；
还原是整树覆盖（含新增文件的清除）；尊重 .gitignore；开关可关。全离线（真 git，本地 tmp 仓库）。
"""

import subprocess

import pytest

from src.memory.checkpoint import (changed_since, checkpoints_enabled, list_snapshots,
                                   restore, snapshot)


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    """一个真实的小 git 仓库（用户的 .git），带 .gitignore。"""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "u@u.u")
    _git(tmp_path, "config", "user.name", "u")
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("v1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


# ------------------------------------------------------------ 隔离性：绝不碰用户的 .git
def test_snapshot_does_not_touch_user_git(repo):
    head_before = _git(repo, "rev-parse", "HEAD").stdout
    log_before = _git(repo, "log", "--oneline").stdout
    (repo / "a.py").write_text("v2\n", encoding="utf-8")
    assert snapshot(str(repo), "回合 1")
    assert _git(repo, "rev-parse", "HEAD").stdout == head_before      # 用户 HEAD 不动
    assert _git(repo, "log", "--oneline").stdout == log_before        # 用户 log 里没有快照提交
    st = _git(repo, "status", "--porcelain").stdout
    assert "a.py" in st                                              # 用户的未提交改动仍在（未被 add）
    assert (repo / ".vortocode" / "shadow.git" / "HEAD").is_file()   # 影子仓库在 .vortocode 下


# ------------------------------------------------------------ 核心价值：撤得回 shell 副作用
def test_restore_undoes_shell_side_effects(repo):
    """快照 → 模拟 run_command 跑脚本改了源码（不经工具写入）→ 还原。"""
    sha = snapshot(str(repo), "改之前")
    assert sha
    (repo / "a.py").write_text("被脚本改坏了\n", encoding="utf-8")     # 例：agent 跑 black/生成脚本
    (repo / "b.py").write_text("脚本新建的\n", encoding="utf-8")
    assert sorted(changed_since(str(repo), sha)) == ["a.py", "b.py"]

    res = restore(str(repo), sha)
    assert res["ok"] and sorted(res["restored"]) == ["a.py", "b.py"]
    assert (repo / "a.py").read_text(encoding="utf-8") == "v1\n"     # 修改被还原
    assert not (repo / "b.py").exists()                              # 快照之后新建的文件被清掉


def test_restore_noop_when_unchanged(repo):
    sha = snapshot(str(repo), "s")
    res = restore(str(repo), sha)
    assert res["ok"] and res["restored"] == []


# ------------------------------------------------------------ 快照面：尊重 .gitignore、不递归自身
def test_snapshot_respects_gitignore_and_skips_vortocode(repo):
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "big.js").write_text("x" * 100, encoding="utf-8")
    sha = snapshot(str(repo), "s")
    assert sha
    (repo / "node_modules" / "big.js").write_text("y" * 100, encoding="utf-8")
    changed = changed_since(str(repo), sha)
    assert not any("node_modules" in c for c in changed)     # 忽略文件不进快照面
    assert not any(".vortocode" in c for c in changed)       # 影子仓库自身也不进（不递归膨胀）


# ------------------------------------------------------------ 列表 / 去重 / 开关
def test_list_snapshots_new_to_old(repo):
    snapshot(str(repo), "第一回合")
    (repo / "a.py").write_text("v2\n", encoding="utf-8")
    snapshot(str(repo), "第二回合")
    snaps = list_snapshots(str(repo))
    assert [s["label"] for s in snaps] == ["第二回合", "第一回合"]


def test_snapshot_skips_duplicate_when_workspace_unchanged(repo):
    s1 = snapshot(str(repo), "一")
    s2 = snapshot(str(repo), "二")            # 工作区没变 → 不重复提交
    assert s1 == s2
    assert len(list_snapshots(str(repo))) == 1


def test_list_snapshots_empty_without_shadow(tmp_path):
    assert list_snapshots(str(tmp_path)) == []


def test_checkpoints_disabled_by_env(repo, monkeypatch):
    monkeypatch.setenv("VORTOCODE_CHECKPOINT", "0")
    assert checkpoints_enabled() is False
    assert snapshot(str(repo), "s") is None                  # 关了就不打快照
    assert not (repo / ".vortocode" / "shadow.git").exists()
    monkeypatch.setenv("VORTOCODE_CHECKPOINT", "1")
    assert checkpoints_enabled() is True


def test_snapshot_on_non_git_dir_is_safe(tmp_path):
    """目标目录不是 git 仓库也不炸（影子仓库自成一体，不依赖用户的 .git）。"""
    (tmp_path / "x.txt").write_text("hi", encoding="utf-8")
    sha = snapshot(str(tmp_path), "s")
    assert sha                                               # 照样能快照
    (tmp_path / "x.txt").write_text("changed", encoding="utf-8")
    assert restore(str(tmp_path), sha)["ok"]
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "hi"


def test_restore_unknown_sha_reports_error(repo):
    snapshot(str(repo), "s")
    res = restore(str(repo), "0" * 40)
    assert not res["ok"] and res["error"]
