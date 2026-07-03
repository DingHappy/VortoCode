"""C3 · PR 反馈闭环——vcs.pr_feedback（读 review+CI）+ build_dev_tools 的 pr_fix 工具。

不触网/不跑 gh：mock subprocess（vcs）与 worktree（pr_fix 的修复循环），聚焦①gh JSON 解析
②已 resolved 的行级评论被过滤 ③CI 失败提取 ④gh 缺失/无 PR 优雅降级 ⑤pr_fix vorto/* 硬闸
⑥findings→修复循环→push（确认门）⑦确认拒绝不 push。
"""
import json

import pytest

from src.agents import main_agent as ma
from src.agents import vcs


def _mk_gh_dispatcher(view=None, repo=None, graphql=None, has_gh=True):
    """造一个 subprocess.run 假实现：按 gh 子命令返回预设 JSON。"""
    def fake_run(cmd, cwd=None, capture_output=True, text=True):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        r = R()
        if cmd[:3] == ["gh", "pr", "view"]:
            r.stdout = json.dumps(view or {})
        elif cmd[:3] == ["gh", "repo", "view"]:
            r.stdout = json.dumps(repo or {})
        elif cmd[:3] == ["gh", "api", "graphql"]:
            r.stdout = json.dumps(graphql or {})
        else:
            r.stdout = "{}"
        return r
    return fake_run


def test_pr_feedback_parses_reviews_checks_and_filters_resolved(monkeypatch):
    monkeypatch.setattr(vcs.shutil, "which", lambda _n: "/usr/bin/gh")
    view = {
        "number": 42, "headRefName": "vorto/auto-abc",
        "reviews": [
            {"author": {"login": "codex"}, "state": "CHANGES_REQUESTED", "body": "这里有个空指针"},
            {"author": {"login": "bot"}, "state": "APPROVED", "body": ""},          # 无正文/approved → 忽略
        ],
        "statusCheckRollup": [
            {"name": "pytest", "conclusion": "FAILURE", "detailsUrl": "http://ci/1"},
            {"name": "lint", "conclusion": "SUCCESS"},                              # 绿 → 忽略
        ],
    }
    repo = {"owner": {"login": "me"}, "name": "VortoCode"}
    graphql = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
        {"isResolved": False, "comments": {"nodes": [
            {"path": "src/x.py", "line": 10, "body": "改这行", "author": {"login": "codex"}}]}},
        {"isResolved": True, "comments": {"nodes": [                                # 已 resolved → 过滤
            {"path": "src/y.py", "line": 5, "body": "旧的已解决评论", "author": {"login": "codex"}}]}},
    ]}}}}}
    monkeypatch.setattr(vcs.subprocess, "run", _mk_gh_dispatcher(view, repo, graphql))

    fb = vcs.pr_feedback("/repo", "vorto/auto-abc")
    assert fb["ok"] and fb["pr"] == 42 and fb["branch"] == "vorto/auto-abc"
    bodies = [c["body"] for c in fb["comments"]]
    assert "这里有个空指针" in bodies and "改这行" in bodies
    assert "旧的已解决评论" not in bodies                    # resolved 线程被过滤
    assert [c["name"] for c in fb["failing_checks"]] == ["pytest"]   # 只留失败检查
    line_comment = next(c for c in fb["comments"] if c["body"] == "改这行")
    assert line_comment["path"] == "src/x.py" and line_comment["line"] == 10


def test_pr_feedback_no_gh(monkeypatch):
    monkeypatch.setattr(vcs.shutil, "which", lambda _n: None)
    fb = vcs.pr_feedback("/repo", "vorto/x")
    assert fb["ok"] is False and "gh" in fb["error"]


def test_pr_feedback_no_pr(monkeypatch):
    monkeypatch.setattr(vcs.shutil, "which", lambda _n: "/usr/bin/gh")
    monkeypatch.setattr(vcs.subprocess, "run", _mk_gh_dispatcher(view={}))   # 无 number
    fb = vcs.pr_feedback("/repo", "vorto/x")
    assert fb["ok"] is False and "找不到" in fb["error"]


# --------------------------------------------------------------------- pr_fix 工具
def _pr_fix_tool(tmp_path, confirm=None):
    return {t.name: t for t in ma.build_dev_tools(str(tmp_path), confirm=confirm)}["pr_fix"]


@pytest.mark.asyncio
async def test_pr_fix_rejects_non_vorto_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "pr_feedback",
                        lambda root, ref: {"ok": True, "pr": 1, "branch": "main",
                                           "comments": [{"body": "改", "author": "x", "path": None, "line": None}],
                                           "failing_checks": []})
    out = await _pr_fix_tool(tmp_path).handler({"pr": "1"})
    assert "拒绝" in out and "vorto/" in out                # 硬闸：非 vorto 分支不碰


@pytest.mark.asyncio
async def test_pr_fix_no_findings(tmp_path, monkeypatch):
    monkeypatch.setattr(vcs, "pr_feedback",
                        lambda root, ref: {"ok": True, "pr": 7, "branch": "vorto/auto-x",
                                           "comments": [], "failing_checks": []})
    out = await _pr_fix_tool(tmp_path).handler({"pr": "7"})
    assert "无需修" in out


@pytest.mark.asyncio
async def test_pr_fix_repairs_and_pushes_with_confirm(tmp_path, monkeypatch):
    import src.agents.worktree as wt
    monkeypatch.setattr(vcs, "pr_feedback",
                        lambda root, ref: {"ok": True, "pr": 9, "branch": "vorto/auto-x",
                                           "comments": [{"body": "修空指针", "author": "codex",
                                                         "path": "src/x.py", "line": 3}],
                                           "failing_checks": [{"name": "pytest", "link": "http://ci"}]})
    seen = {}

    async def fake_dep(repo, wid, branch, desc, build, msg, test_cmd=None):
        seen["desc"] = desc
        seen["branch"] = branch
        return {"ok": True, "conclusion": "c", "output": ""}
    monkeypatch.setattr(wt, "run_dependent_on_branch", fake_dep)
    pushed = {"n": 0}
    monkeypatch.setattr(vcs, "push_branch",
                        lambda root, branch, remote="origin": pushed.__setitem__("n", pushed["n"] + 1)
                        or {"ok": True, "output": ""})

    asked = []

    async def confirm(m):
        asked.append(m)
        return True
    out = await _pr_fix_tool(tmp_path, confirm).handler({"pr": "9"})
    assert "修空指针" in seen["desc"] and "pytest" in seen["desc"]   # 评论+CI失败都进了修复描述
    assert seen["branch"] == "vorto/auto-x"
    assert pushed["n"] == 1 and asked                       # 绿 → 经确认 push
    assert "已按 PR #9" in out and "push" in out


@pytest.mark.asyncio
async def test_pr_fix_confirm_denied_not_pushed(tmp_path, monkeypatch):
    import src.agents.worktree as wt
    monkeypatch.setattr(vcs, "pr_feedback",
                        lambda root, ref: {"ok": True, "pr": 3, "branch": "vorto/auto-x",
                                           "comments": [{"body": "改", "author": "x", "path": None, "line": None}],
                                           "failing_checks": []})
    monkeypatch.setattr(wt, "run_dependent_on_branch",
                        lambda *a, **k: _ok())
    pushed = {"n": 0}
    monkeypatch.setattr(vcs, "push_branch",
                        lambda *a, **k: pushed.__setitem__("n", pushed["n"] + 1) or {"ok": True, "output": ""})

    async def deny(_m):
        return False
    out = await _pr_fix_tool(tmp_path, deny).handler({"pr": "3"})
    assert pushed["n"] == 0 and "未 push" in out             # 拒绝 → 不 push


async def _ok():
    return {"ok": True, "conclusion": "c", "output": ""}
