"""Task branch review, stable hunk decisions, and delivery verification gates."""
from __future__ import annotations

import subprocess

import pytest

from src.agents.dev_plan import DevPlan, save_plan
from src.gateway.task_review import (
    apply_task_review_action,
    task_review_delivery_ready,
    task_review_diff,
    task_review_snapshot,
    task_review_summary,
    verify_task_review,
)
from src.gateway.tasks import BackgroundTask, TaskRunner


def _git(root, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=check,
    )


def _task_branch(root):
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "review@example.com")
    _git(root, "config", "user.name", "Task Review")
    original = [f"line {index}" for index in range(1, 25)]
    (root / "sample.txt").write_text("\n".join(original) + "\n", encoding="utf-8")
    _git(root, "add", "sample.txt")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "vorto/task-review")
    changed = list(original)
    changed[0] = "first accepted"
    changed[20] = "second rejected"
    (root / "sample.txt").write_text("\n".join(changed) + "\n", encoding="utf-8")
    _git(root, "add", "sample.txt")
    _git(root, "commit", "-qm", "agent changes")
    _git(root, "checkout", "-q", "main")
    plan = DevPlan.new(
        "review task", "vorto/task-review", "main", plan_id="review-plan",
    )
    save_plan(str(root), plan)
    task = BackgroundTask.new(
        "dev", "review task", plan_id=plan.plan_id, owner_session="sid-desktop",
    )
    task.status = "done"
    task.branch = plan.branch
    return task, original


def test_task_branch_snapshot_accept_and_reject_hunks(tmp_path):
    task, original = _task_branch(tmp_path)
    snapshot = task_review_snapshot(str(tmp_path), task)
    assert snapshot["branch"] == "vorto/task-review"
    assert snapshot["base"] == "main" and snapshot["mutable"] is True
    assert [item["path"] for item in snapshot["files"]] == ["sample.txt"]
    assert snapshot["review"]["coverage"] == {
        "known": True, "total_hunks": 2, "accepted_hunks": 0,
        "pending_hunks": 2, "stale_hunks": 0, "complete": False,
        "truncated": False,
        "pending": [
            {"path": "sample.txt", "hunk_id": snapshot["review"]["coverage"]["pending"][0]["hunk_id"]},
            {"path": "sample.txt", "hunk_id": snapshot["review"]["coverage"]["pending"][1]["hunk_id"]},
        ],
        "pending_truncated": False,
        "head": snapshot["head_oid"], "error": "",
    }
    assert snapshot["review"]["policy"]["require_all_hunks_decided"] is False

    diff = task_review_diff(str(tmp_path), task, "sample.txt")
    assert diff["scope"] == "branch" and len(diff["hunks"]) == 2
    first, second = diff["hunks"]
    accepted = apply_task_review_action(
        str(tmp_path), task, action="accept", path="sample.txt",
        hunk_id=first["id"], expected_sha256=first["sha256"],
    )
    assert accepted["snapshot"]["review"]["accepted_hunks"] == 1
    assert task_review_summary(str(tmp_path), task)["coverage"]["accepted_hunks"] == 1
    refreshed = task_review_diff(str(tmp_path), task, "sample.txt")
    assert refreshed["hunks"][0]["accepted"] is True

    with pytest.raises(RuntimeError, match="Diff 已变化"):
        apply_task_review_action(
            str(tmp_path), task, action="accept", path="sample.txt",
            hunk_id=first["id"], expected_sha256="0" * 64,
        )
    rejected = apply_task_review_action(
        str(tmp_path), task, action="reject", path="sample.txt",
        hunk_id=second["id"], expected_sha256=second["sha256"], confirm=True,
    )
    assert rejected["snapshot"]["review"]["verification_stale"] is True
    branch_text = _git(tmp_path, "show", "vorto/task-review:sample.txt").stdout.splitlines()
    main_text = _git(tmp_path, "show", "main:sample.txt").stdout.splitlines()
    assert branch_text[0] == "first accepted"
    assert branch_text[20] == original[20]
    assert main_text == original
    assert not list((tmp_path / ".vortocode" / "worktrees").glob("review-*"))


def test_strict_policy_requires_every_current_hunk_and_rechecks_live_head(tmp_path):
    task, _ = _task_branch(tmp_path)
    policy = tmp_path / ".vortocode" / "review-policy.yaml"
    policy.parent.mkdir(exist_ok=True)
    policy.write_text(
        "task_branch:\n  require_all_hunks_decided: true\n", encoding="utf-8",
    )

    snapshot = task_review_snapshot(str(tmp_path), task)
    assert snapshot["review"]["policy"]["active"] is True
    assert snapshot["review"]["coverage"]["pending_hunks"] == 2
    ready, reason = task_review_delivery_ready(str(tmp_path), task)
    assert ready is False and "已接受 0/2" in reason

    for expected_accepted in (1, 2):
        hunk = next(
            item for item in task_review_diff(str(tmp_path), task, "sample.txt")["hunks"]
            if not item["accepted"]
        )
        applied = apply_task_review_action(
            str(tmp_path), task, action="accept", path="sample.txt",
            hunk_id=hunk["id"], expected_sha256=hunk["sha256"],
        )
        coverage = applied["snapshot"]["review"]["coverage"]
        assert coverage["accepted_hunks"] == expected_accepted
        assert task_review_summary(str(tmp_path), task)["coverage"]["accepted_hunks"] == expected_accepted

    assert task_review_delivery_ready(str(tmp_path), task) == (True, "")

    # Delivery does not trust the cached task-card projection: a new branch hunk
    # closes the gate until the exact current patch has its own evidence.
    _git(tmp_path, "checkout", "-q", "vorto/task-review")
    (tmp_path / "later.txt").write_text("unreviewed\n", encoding="utf-8")
    _git(tmp_path, "add", "later.txt")
    _git(tmp_path, "commit", "-qm", "late branch change")
    _git(tmp_path, "checkout", "-q", "main")
    ready, reason = task_review_delivery_ready(str(tmp_path), task)
    assert ready is False and "仍有 1 个待处理" in reason


def test_invalid_strict_policy_fails_closed_at_delivery(tmp_path):
    task, _ = _task_branch(tmp_path)
    policy = tmp_path / ".vortocode" / "review-policy.yaml"
    policy.parent.mkdir(exist_ok=True)
    policy.write_text(
        "task_branch:\n  require_all_hunks_decided: yes\n", encoding="utf-8",
    )
    snapshot = task_review_snapshot(str(tmp_path), task)
    assert "true/false" in snapshot["review"]["policy"]["error"]
    ready, reason = task_review_delivery_ready(str(tmp_path), task)
    assert ready is False and "团队审查策略无效" in reason


def test_task_rest_delivery_uses_strict_policy_not_desktop_state(tmp_path, monkeypatch):
    task, _ = _task_branch(tmp_path)
    policy = tmp_path / ".vortocode" / "review-policy.yaml"
    policy.parent.mkdir(exist_ok=True)
    policy.write_text(
        "task_branch:\n  require_all_hunks_decided: true\n", encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    import src.web.routers.tasks as tasks_router
    from src.web.server import app

    async def worker(item, progress):
        return "done"

    runner = TaskRunner(str(tmp_path), worker)
    runner.ledger.save(task)
    monkeypatch.setattr(tasks_router, "_RUNNER", runner)
    client = TestClient(app)

    listed = client.get("/api/tasks").json()["tasks"][0]
    assert listed["branch_review"]["policy"]["active"] is True
    blocked = client.post(f"/api/tasks/{task.id}/open_pr")
    assert blocked.status_code == 409
    assert "已接受 0/2" in blocked.json()["detail"]


def test_reject_requires_terminal_task_and_explicit_confirmation(tmp_path):
    task, _ = _task_branch(tmp_path)
    hunk = task_review_diff(str(tmp_path), task, "sample.txt")["hunks"][0]
    with pytest.raises(ValueError, match="显式确认"):
        apply_task_review_action(
            str(tmp_path), task, action="reject", path="sample.txt",
            hunk_id=hunk["id"], expected_sha256=hunk["sha256"],
        )
    task.status = "running"
    with pytest.raises(ValueError, match="只允许查看"):
        apply_task_review_action(
            str(tmp_path), task, action="accept", path="sample.txt",
            hunk_id=hunk["id"], expected_sha256=hunk["sha256"],
        )
    with pytest.raises(ValueError, match="只允许查看"):
        apply_task_review_action(
            str(tmp_path), task, action="reject", path="sample.txt",
            hunk_id=hunk["id"], expected_sha256=hunk["sha256"], confirm=True,
        )


def test_reject_invalidates_delivery_until_exact_branch_reverifies(tmp_path, monkeypatch):
    task, _ = _task_branch(tmp_path)
    hunk = task_review_diff(str(tmp_path), task, "sample.txt")["hunks"][0]
    apply_task_review_action(
        str(tmp_path), task, action="reject", path="sample.txt",
        hunk_id=hunk["id"], expected_sha256=hunk["sha256"], confirm=True,
    )
    assert task_review_delivery_ready(str(tmp_path), task)[0] is False

    import src.agents.worktree as worktree
    outcomes = iter((False, True))
    monkeypatch.setattr(worktree, "run_tests", lambda path, command: {
        "ok": (ok := next(outcomes)), "cmd": "pytest -q",
        "output": "all green" if ok else "one test failed",
        "sandbox": {"isolated": True},
    })
    failed = verify_task_review(str(tmp_path), task)
    assert failed["ok"] is False
    assert failed["snapshot"]["review"]["verification_stale"] is True
    assert task_review_delivery_ready(str(tmp_path), task)[0] is False

    verified = verify_task_review(str(tmp_path), task)
    assert verified["ok"] is True
    assert verified["verification"]["head"] == _git(
        tmp_path, "rev-parse", "vorto/task-review",
    ).stdout.strip()
    assert task_review_delivery_ready(str(tmp_path), task) == (True, "")


def test_task_review_rest_endpoints(tmp_path, monkeypatch):
    task, _ = _task_branch(tmp_path)
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    import src.web.routers.tasks as tasks_router
    from src.web.server import app

    async def worker(item, progress):
        return "done"

    runner = TaskRunner(str(tmp_path), worker)
    runner.ledger.save(task)
    monkeypatch.setattr(tasks_router, "_RUNNER", runner)
    client = TestClient(app)

    snapshot = client.get(f"/api/tasks/{task.id}/review")
    assert snapshot.status_code == 200 and snapshot.json()["files"]
    diff = client.get(
        f"/api/tasks/{task.id}/review/diff", params={"path": "sample.txt"},
    )
    hunk = diff.json()["hunks"][0]
    accepted = client.post(f"/api/tasks/{task.id}/review/action", json={
        "action": "accept", "path": "sample.txt", "hunk_id": hunk["id"],
        "expected_sha256": hunk["sha256"],
    })
    assert accepted.status_code == 200
    rejected_hunk = diff.json()["hunks"][1]
    rejected = client.post(f"/api/tasks/{task.id}/review/action", json={
        "action": "reject", "path": "sample.txt", "hunk_id": rejected_hunk["id"],
        "expected_sha256": rejected_hunk["sha256"], "confirm": True,
    })
    assert rejected.status_code == 200
    blocked_pr = client.post(f"/api/tasks/{task.id}/open_pr")
    assert blocked_pr.status_code == 409
    assert "重新验证" in blocked_pr.json()["detail"]
    assert client.get("/api/tasks/missing/review").status_code == 404
    assert client.get(
        f"/api/tasks/{task.id}/review/diff", params={"path": "../escape"},
    ).status_code == 409
