"""Git workflow helpers used by TUI commands."""

import subprocess

from src.agents.git_workflow import (commit_changes,
                                     format_pr_preview,
                                     format_status_summary,
                                     pr_preview,
                                     status_summary)


def _git(path, *args):
    return subprocess.run(["git", *args], cwd=str(path), capture_output=True, text=True)


def _init_repo(path):
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "x@example.com")
    _git(path, "config", "user.name", "x")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")


def test_status_summary_reports_staged_and_unstaged(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(tmp_path, "add", "staged.txt")
    (tmp_path / "base.txt").write_text("changed\n", encoding="utf-8")

    info = status_summary(str(tmp_path))
    text = format_status_summary(info)

    assert info["ok"] is True
    assert "staged.txt" in info["porcelain"]
    assert "base.txt" in info["porcelain"]
    assert "已 staged diffstat" in text
    assert "未 staged diffstat" in text


def test_commit_changes_staged_only(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")

    result = commit_changes(str(tmp_path), "add a")

    assert result["ok"] is True
    assert result["sha"]
    assert "add a" in _git(tmp_path, "log", "-1", "--pretty=%s").stdout


def test_commit_changes_all_stages_before_commit(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")

    result = commit_changes(str(tmp_path), "add all", stage_all=True)

    assert result["ok"] is True
    assert "add all" in _git(tmp_path, "log", "-1", "--pretty=%s").stdout


def test_commit_changes_rejects_no_staged_changes(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")

    result = commit_changes(str(tmp_path), "add a")

    assert result["ok"] is False
    assert "没有 staged" in result["error"]


def test_pr_preview_builds_title_body_and_file_list(tmp_path):
    _init_repo(tmp_path)
    _git(tmp_path, "checkout", "-qb", "feature/demo")
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "feat: add a")

    preview = pr_preview(str(tmp_path), base="main")
    text = format_pr_preview(preview)

    assert preview["ok"] is True
    assert preview["branch"] == "feature/demo"
    assert preview["base"] == "main"
    assert preview["title"] == "feat: add a"
    assert "a.txt" in preview["changed_files"]
    assert "## Summary" in preview["body"]
    assert "PR 预览: feature/demo → main" in text


def test_pr_preview_rejects_main_branch(tmp_path):
    _init_repo(tmp_path)

    preview = pr_preview(str(tmp_path), base="main")

    assert preview["ok"] is False
    assert "当前分支是" in preview["error"]


def test_pr_preview_rejects_dirty_worktree(tmp_path):
    _init_repo(tmp_path)
    _git(tmp_path, "checkout", "-qb", "feature/demo")
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")

    preview = pr_preview(str(tmp_path), base="main")

    assert preview["ok"] is False
    assert "未提交改动" in preview["error"]
