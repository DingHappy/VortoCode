"""远端集成（src/agents/vcs.py）单测——全程 mock，绝不真 push/开 PR。"""

from src.agents import vcs


class _R:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_open_pr_without_gh(monkeypatch):
    monkeypatch.setattr(vcs.shutil, "which", lambda _x: None)
    r = vcs.open_pr("/x", "b", "t")
    assert r["ok"] is False and "gh" in r["error"]            # gh 缺失 → 提示，不崩


def test_open_pr_success_extracts_url(monkeypatch):
    monkeypatch.setattr(vcs.shutil, "which", lambda _x: "/usr/bin/gh")
    monkeypatch.setattr(vcs.subprocess, "run",
                        lambda *a, **k: _R(0, "前言\nhttps://github.com/o/r/pull/9\n"))
    r = vcs.open_pr("/x", "b", "t")
    assert r["ok"] and r["url"] == "https://github.com/o/r/pull/9"


def test_open_pr_failure_reports_error(monkeypatch):
    monkeypatch.setattr(vcs.shutil, "which", lambda _x: "/usr/bin/gh")
    monkeypatch.setattr(vcs.subprocess, "run", lambda *a, **k: _R(1, "", "no commits between"))
    r = vcs.open_pr("/x", "b", "t")
    assert r["ok"] is False and "no commits" in r["error"]


def test_push_branch_command_and_parse(monkeypatch):
    seen = {}

    def fake_run(cmd, **k):
        seen["cmd"] = cmd
        return _R(0, "", "")
    monkeypatch.setattr(vcs.subprocess, "run", fake_run)
    r = vcs.push_branch("/repo", "vorto/x")
    assert r["ok"]
    assert seen["cmd"][:3] == ["git", "-C", "/repo"]
    assert "push" in seen["cmd"] and "vorto/x" in seen["cmd"]


def test_push_and_open_pr_skips_pr_when_push_fails(monkeypatch):
    called = {"pr": False}
    monkeypatch.setattr(vcs, "push_branch", lambda *a, **k: {"ok": False, "output": "rejected"})
    monkeypatch.setattr(vcs, "open_pr",
                        lambda *a, **k: (called.__setitem__("pr", True), {"ok": True})[1])
    r = vcs.push_and_open_pr("/x", "b", "t")
    assert r["ok"] is False and r["pushed"] is False and called["pr"] is False   # push 失败不开 PR
