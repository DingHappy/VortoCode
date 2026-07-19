"""Current branch PR/CI delivery contracts for Desktop."""
import subprocess

import pytest

from src.gateway.pr_delivery import current_failed_check_log, current_pr_delivery


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
    )


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "checkout", "-qb", "vorto/desktop-ci")
    (tmp_path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "base.txt")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path


def test_current_delivery_uses_exact_current_branch(repo, monkeypatch):
    from src.agents import vcs

    seen = []
    monkeypatch.setattr(vcs, "pr_feedback", lambda root, ref: (
        seen.append((root, ref)) or {
            "ok": True, "pr": 7, "branch": ref, "checks": [], "comments": [],
            "failing_checks": [], "summary": {"total": 0, "failed": 0, "pending": 0, "passed": 0},
        }
    ))
    result = current_pr_delivery(str(repo))
    assert result["ok"] is True and result["pr"] == 7
    assert seen[0][1] == "vorto/desktop-ci"


def test_failed_log_accepts_only_current_server_issued_check(repo, monkeypatch):
    from src.agents import vcs

    snapshot = {
        "ok": True,
        "branch": "vorto/desktop-ci",
        "checks": [],
        "comments": [],
        "failing_checks": [{
            "id": "check-abc123", "name": "pytest",
            "link": "https://github.com/me/repo/actions/runs/123", "conclusion": "FAILURE",
        }],
        "summary": {"total": 1, "failed": 1, "pending": 0, "passed": 0},
    }
    monkeypatch.setattr(vcs, "pr_feedback", lambda *_a: snapshot)
    monkeypatch.setattr(vcs, "failed_check_log_excerpts", lambda *_a, **_k: {
        "ok": True,
        "logs": [{"name": "pytest", "run_id": "123", "job_name": "unit", "step_name": "test", "excerpt": "1 failed"}],
        "error": "",
    })
    result = current_failed_check_log(str(repo), "check-abc123")
    assert result["ok"] is True and result["log"]["excerpt"] == "1 failed"
    with pytest.raises(ValueError, match="失败列表"):
        current_failed_check_log(str(repo), "check-user-supplied")


def test_delivery_rest_endpoints(repo, monkeypatch):
    monkeypatch.chdir(repo)
    from fastapi.testclient import TestClient
    from src.agents import vcs
    from src.web.server import app

    snapshot = {
        "ok": True, "pr": 9, "branch": "vorto/desktop-ci", "comments": [], "checks": [],
        "failing_checks": [{
            "id": "check-rest", "name": "unit", "link": "https://github.com/o/r/actions/runs/9",
            "conclusion": "FAILURE", "state": "", "status": "", "workflow": "CI",
            "started_at": "", "completed_at": "", "failing": True, "pending": False,
        }],
        "summary": {"total": 1, "failed": 1, "pending": 0, "passed": 0},
    }
    monkeypatch.setattr(vcs, "pr_feedback", lambda *_a: snapshot)
    monkeypatch.setattr(vcs, "failed_check_log_excerpts", lambda *_a, **_k: {
        "ok": True, "logs": [{"name": "unit", "run_id": "9", "excerpt": "failed"}], "error": "",
    })
    client = TestClient(app)
    assert client.get("/api/git/delivery").json()["pr"] == 9
    log = client.get("/api/git/delivery/checks/check-rest/log")
    assert log.status_code == 200 and log.json()["log"]["excerpt"] == "failed"
    assert client.get("/api/git/delivery/checks/check-stale/log").status_code == 409
