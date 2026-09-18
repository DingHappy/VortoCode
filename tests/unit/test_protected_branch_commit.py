"""受保护分支上的提交要多问一次——别把用户送进"提交完了才发现开不了 PR"的死角。

真机诊断（2026-09-17）：界面允许直接在 main 上提交，但提交完想开 PR 时被拒——
`open_reviewed_pr` 说"拒绝直接从受保护分支创建 PR；请先切到功能分支"。两条规矩本该
成对，之前只有出口那一半。

只拦一次、不硬拒：单人仓库直接往 main 提交是正当用法。
"""

import subprocess

import pytest

from src.gateway.git_review import commit_reviewed


def _run(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    _run(tmp_path, "init", "-q", "-b", "main")
    _run(tmp_path, "config", "user.email", "t@example.com")
    _run(tmp_path, "config", "user.name", "T")
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    _run(tmp_path, "add", "a.txt")
    _run(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def _stage_a_change(repo):
    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    _run(repo, "add", "a.txt")


def test_commit_on_main_asks_first(repo):
    _stage_a_change(repo)
    with pytest.raises(ValueError) as error:
        commit_reviewed(str(repo), "改一行")
    assert "main" in str(error.value)
    assert "开 PR" in str(error.value)               # 说清楚为什么问，不是干拦
    assert _head_subject(repo) == "init"             # 真的没提交


def test_confirming_goes_through(repo):
    _stage_a_change(repo)
    result = commit_reviewed(str(repo), "改一行", confirm_protected=True)
    assert result["ok"] is True
    assert _head_subject(repo) == "改一行"


def test_feature_branch_is_not_touched(repo):
    _run(repo, "checkout", "-q", "-b", "vorto/fix")
    _stage_a_change(repo)
    result = commit_reviewed(str(repo), "改一行")     # 不用确认
    assert result["ok"] is True


def test_the_empty_message_check_still_comes_first(repo):
    """先怪空说明，再谈分支——报错要指向用户下一步真正该做的事。"""
    _stage_a_change(repo)
    with pytest.raises(ValueError, match="提交说明不能为空"):
        commit_reviewed(str(repo), "   ")


def _head_subject(repo) -> str:
    return _run(repo, "log", "-1", "--format=%s").stdout.strip()
