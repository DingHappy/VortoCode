"""Structured Desktop Git review and hunk mutation contracts."""
import subprocess
from pathlib import Path

import pytest

from src.gateway.git_review import (
    apply_review_action,
    commit_reviewed,
    normalize_git_path,
    open_reviewed_pr,
    review_diff,
    review_snapshot,
    review_watch_state,
)
from src.gateway.review_threads import ReviewConflict, ReviewThreadStore
from src.gateway.change_sources import (
    capture_workspace_state,
    record_change_source,
    record_workspace_side_effects,
)


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False,
    )


def _init_repo(root):
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "desktop@example.com")
    _git(root, "config", "user.name", "Desktop Test")
    lines = [f"line {index}\n" for index in range(40)]
    (root / "sample.txt").write_text("".join(lines), encoding="utf-8")
    _git(root, "add", "sample.txt")
    _git(root, "commit", "-qm", "initial")
    return lines


def test_snapshot_and_diff_expose_files_hunks_and_line_numbers(tmp_path):
    lines = _init_repo(tmp_path)
    lines[2] = "first changed\n"
    lines[32] = "second changed\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")

    snapshot = review_snapshot(str(tmp_path))
    assert snapshot["files"][0]["path"] == "sample.txt"
    assert snapshot["files"][0]["unstaged"] is True
    payload = review_diff(str(tmp_path), "sample.txt", scope="working")
    assert len({item["id"] for item in payload["hunks"]}) == 2
    assert all(item["id"].startswith("h-") for item in payload["hunks"])
    added = [line for line in payload["hunks"][0]["lines"] if line["kind"] == "add"]
    assert added[0]["new_line"] == 3 and added[0]["text"] == "+first changed"
    assert len(payload["hunks"][0]["sha256"]) == 64
    assert len(payload["baseline"]) == 64
    assert payload["head"]


def test_review_watch_state_changes_for_external_edits_index_and_attribution(tmp_path, monkeypatch):
    lines = _init_repo(tmp_path)
    ledger = tmp_path.parent / f"{tmp_path.name}-watch-sources.json"
    monkeypatch.setenv("VORTOCODE_CHANGE_SOURCE_STORE", str(ledger))
    clean = review_watch_state(str(tmp_path))
    assert clean["files"] == 0 and len(clean["baseline"]) == 64

    before = "".join(lines)
    lines[2] = "changed outside Desktop\n"
    after = "".join(lines)
    (tmp_path / "sample.txt").write_text(after, encoding="utf-8")
    edited = review_watch_state(str(tmp_path))
    assert edited["baseline"] != clean["baseline"]
    assert edited["paths"] == ["sample.txt"]

    record_change_source(str(tmp_path), "sample.txt", before, after, source="agent")
    attributed = review_watch_state(str(tmp_path))
    assert attributed["source_revision"] != edited["source_revision"]
    assert attributed["baseline"] != edited["baseline"]

    _git(tmp_path, "add", "sample.txt")
    staged = review_watch_state(str(tmp_path))
    assert staged["baseline"] != attributed["baseline"]

    # Authoritative Desktop refreshes must not mutate the watch token, or the
    # watcher would publish a new invalidation after every invalidation.
    review_snapshot(str(tmp_path))
    review_diff(str(tmp_path), "sample.txt", scope="staged")
    repeated = review_watch_state(str(tmp_path))
    assert repeated == staged


def test_hunk_identity_survives_unrelated_hunk_inserted_before_it(tmp_path):
    lines = _init_repo(tmp_path)
    lines[32] = "second changed\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")
    initial = review_diff(str(tmp_path), "sample.txt")
    original = initial["hunks"][0]

    lines[2] = "first changed\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")
    refreshed = review_diff(str(tmp_path), "sample.txt")
    second = next(hunk for hunk in refreshed["hunks"] if any(
        line["text"] == "+second changed" for line in hunk["lines"]
    ))

    assert second["id"] == original["id"]
    # The stable identity survives, while the exact-patch guard intentionally
    # changes because the file snapshot/index header changed underneath it.
    assert second["sha256"] != original["sha256"]
    assert refreshed["baseline"] != initial["baseline"]


def test_hunk_source_attribution_uses_user_owned_hash_ledger(tmp_path, monkeypatch):
    lines = _init_repo(tmp_path)
    ledger = tmp_path.parent / f"{tmp_path.name}-change-sources.json"
    monkeypatch.setenv("VORTOCODE_CHANGE_SOURCE_STORE", str(ledger))
    before = "".join(lines)
    lines[2] = "agent changed\n"
    after = "".join(lines)
    (tmp_path / "sample.txt").write_text(after, encoding="utf-8")
    record_change_source(
        str(tmp_path), "sample.txt", before, after, source="agent",
        session="sid-review", turn="turn-1", tool="edit_file",
    )

    first = review_diff(str(tmp_path), "sample.txt")["hunks"][0]
    assert first["source"] == "agent"
    assert first["source_session"] == "sid-review"
    assert first["source_tool"] == "edit_file"
    assert ledger.is_file() and not Path(tmp_path, ".vortocode", "change-sources.json").exists()

    lines[3] = "external changed\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")
    mixed = review_diff(str(tmp_path), "sample.txt")["hunks"][0]
    assert mixed["source"] == "mixed"


def test_unrecorded_and_desktop_user_changes_are_distinct(tmp_path, monkeypatch):
    lines = _init_repo(tmp_path)
    monkeypatch.setenv("VORTOCODE_CHANGE_SOURCE_STORE", str(tmp_path.parent / "sources-user.json"))
    before = "".join(lines)
    lines[2] = "user changed\n"
    after = "".join(lines)
    (tmp_path / "sample.txt").write_text(after, encoding="utf-8")
    assert review_diff(str(tmp_path), "sample.txt")["hunks"][0]["source"] == "external"

    record_change_source(str(tmp_path), "sample.txt", before, after, source="user", tool="desktop_editor")
    assert review_diff(str(tmp_path), "sample.txt")["hunks"][0]["source"] == "user"


def test_hook_command_side_effects_are_attributed_without_copying_source(tmp_path, monkeypatch):
    lines = _init_repo(tmp_path)
    ledger = tmp_path.parent / f"{tmp_path.name}-hook-sources.json"
    monkeypatch.setenv("VORTOCODE_CHANGE_SOURCE_STORE", str(ledger))
    before_state = capture_workspace_state(str(tmp_path))

    lines[2] = "formatted by hook\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")
    assert record_workspace_side_effects(
        str(tmp_path), before_state, source="hook", tool="hook:format",
    ) == 1

    hunk = review_diff(str(tmp_path), "sample.txt")["hunks"][0]
    assert hunk["source"] == "hook"
    assert hunk["source_tool"] == "hook:format"
    ledger_text = ledger.read_text(encoding="utf-8")
    assert "formatted by hook" not in ledger_text


def test_hook_touching_an_already_dirty_hunk_is_classified_mixed(tmp_path, monkeypatch):
    lines = _init_repo(tmp_path)
    monkeypatch.setenv("VORTOCODE_CHANGE_SOURCE_STORE", str(tmp_path.parent / "mixed-hook.json"))
    lines[2] = "user change\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")
    before_state = capture_workspace_state(str(tmp_path))

    lines[3] = "hook change\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")
    assert record_workspace_side_effects(
        str(tmp_path), before_state, source="hook", tool="hook:format",
    ) == 1

    assert review_diff(str(tmp_path), "sample.txt")["hunks"][0]["source"] == "mixed"


def test_stage_unstage_and_revert_single_hunks(tmp_path):
    lines = _init_repo(tmp_path)
    lines[2] = "first changed\n"
    lines[32] = "second changed\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")

    working = review_diff(str(tmp_path), "sample.txt", scope="working")
    first = working["hunks"][0]
    result = apply_review_action(
        str(tmp_path), action="stage", path="sample.txt", scope="working",
        hunk_id=first["id"], expected_sha256=first["sha256"],
    )
    assert result["ok"] is True
    assert "first changed" in _git(tmp_path, "diff", "--cached").stdout
    assert "second changed" not in _git(tmp_path, "diff", "--cached").stdout
    assert "second changed" in _git(tmp_path, "diff").stdout

    staged = review_diff(str(tmp_path), "sample.txt", scope="staged")["hunks"][0]
    assert apply_review_action(
        str(tmp_path), action="unstage", path="sample.txt", scope="staged",
        hunk_id=staged["id"], expected_sha256=staged["sha256"],
    )["ok"] is True
    assert _git(tmp_path, "diff", "--cached", "--quiet").returncode == 0
    empty_staged = review_diff(str(tmp_path), "sample.txt", scope="staged")
    assert empty_staged["hunks"] == [] and len(empty_staged["baseline"]) == 64 and empty_staged["head"]

    working = review_diff(str(tmp_path), "sample.txt", scope="working")
    second = working["hunks"][1]
    with pytest.raises(ValueError, match="显式确认"):
        apply_review_action(
            str(tmp_path), action="revert", path="sample.txt", scope="working",
            hunk_id=second["id"], expected_sha256=second["sha256"], confirm=False,
        )
    assert apply_review_action(
        str(tmp_path), action="revert", path="sample.txt", scope="working",
        hunk_id=second["id"], expected_sha256=second["sha256"], confirm=True,
    )["ok"] is True
    content = (tmp_path / "sample.txt").read_text(encoding="utf-8")
    assert "first changed" in content and "line 32" in content


def test_stale_hunk_hash_fails_closed(tmp_path):
    lines = _init_repo(tmp_path)
    lines[2] = "first changed\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")
    hunk = review_diff(str(tmp_path), "sample.txt")["hunks"][0]
    lines[3] = "changed after review\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")

    result = apply_review_action(
        str(tmp_path), action="stage", path="sample.txt", scope="working",
        hunk_id=hunk["id"], expected_sha256=hunk["sha256"],
    )
    assert result["ok"] is False and result["conflict"] is True
    assert _git(tmp_path, "diff", "--cached", "--quiet").returncode == 0


def test_review_threads_persist_and_track_resolution(tmp_path):
    lines = _init_repo(tmp_path)
    lines[2] = "first changed\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")
    hunk = review_diff(str(tmp_path), "sample.txt")["hunks"][0]
    added = next(item for item in hunk["lines"] if item["kind"] == "add")

    created = ReviewThreadStore(str(tmp_path)).create(
        path="sample.txt",
        scope="working",
        hunk_id=hunk["id"],
        expected_sha256=hunk["sha256"],
        line=added["new_line"],
        side="new",
        body="这里需要保留兼容分支",
    )
    assert created["status"] == "open"
    assert created["hunkSha256"] == hunk["sha256"]
    assert (tmp_path / ".vortocode" / "review_threads.json").is_file()
    assert "review_threads.json" in (tmp_path / ".vortocode" / ".gitignore").read_text(encoding="utf-8")
    assert _git(tmp_path, "status", "--short").stdout == " M sample.txt\n"

    restored = ReviewThreadStore(str(tmp_path)).list()
    assert restored == [created]
    sent = ReviewThreadStore(str(tmp_path)).update_status(created["id"], "sent")
    assert sent and sent["status"] == "sent" and sent["sentAt"]
    resolved = ReviewThreadStore(str(tmp_path)).update_status(created["id"], "resolved")
    assert resolved and resolved["status"] == "resolved" and resolved["resolvedAt"]
    reopened = ReviewThreadStore(str(tmp_path)).update_status(created["id"], "open")
    assert reopened and reopened["status"] == "open" and reopened["resolvedAt"] == ""
    assert ReviewThreadStore(str(tmp_path)).delete(created["id"]) is True
    assert ReviewThreadStore(str(tmp_path)).list() == []


def test_review_thread_rejects_stale_hunk(tmp_path):
    lines = _init_repo(tmp_path)
    lines[2] = "first changed\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")
    hunk = review_diff(str(tmp_path), "sample.txt")["hunks"][0]
    added = next(item for item in hunk["lines"] if item["kind"] == "add")
    lines[3] = "changed after review\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")

    with pytest.raises(ReviewConflict, match="Diff 已变化"):
        ReviewThreadStore(str(tmp_path)).create(
            path="sample.txt",
            scope="working",
            hunk_id=hunk["id"],
            expected_sha256=hunk["sha256"],
            line=added["new_line"],
            side="new",
            body="不能落在过期 diff 上",
        )


def test_whole_file_actions_and_untracked_revert_guard(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "new file.txt").write_text("new\n", encoding="utf-8")
    snapshot = review_snapshot(str(tmp_path))
    new_file = next(item for item in snapshot["files"] if item["path"] == "new file.txt")
    assert new_file["untracked"] is True

    assert apply_review_action(
        str(tmp_path), action="stage", path="new file.txt", scope="working",
    )["ok"] is True
    assert "new file.txt" in _git(tmp_path, "diff", "--cached", "--name-only").stdout
    assert apply_review_action(
        str(tmp_path), action="unstage", path="new file.txt", scope="staged",
    )["ok"] is True
    with pytest.raises(ValueError, match="未跟踪文件"):
        apply_review_action(
            str(tmp_path), action="revert", path="new file.txt", scope="working", confirm=True,
        )
    assert (tmp_path / "new file.txt").is_file()


def test_untracked_file_hunk_can_roundtrip_through_index(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "new.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    working = review_diff(str(tmp_path), "new.txt", scope="working")
    assert len(working["hunks"]) == 1
    hunk = working["hunks"][0]
    assert apply_review_action(
        str(tmp_path), action="stage", path="new.txt", scope="working",
        hunk_id=hunk["id"], expected_sha256=hunk["sha256"],
    )["ok"] is True
    staged = review_diff(str(tmp_path), "new.txt", scope="staged")["hunks"][0]
    assert apply_review_action(
        str(tmp_path), action="unstage", path="new.txt", scope="staged",
        hunk_id=staged["id"], expected_sha256=staged["sha256"],
    )["ok"] is True
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "alpha\nbeta\n"


def test_git_path_validation_rejects_escape_and_absolute_paths():
    assert normalize_git_path("src/main.py") == "src/main.py"
    for unsafe in ("../secret", "/tmp/secret", "src/../secret", "bad\nname"):
        with pytest.raises(ValueError):
            normalize_git_path(unsafe)


def test_reviewed_commit_contains_only_staged_hunks(tmp_path):
    lines = _init_repo(tmp_path)
    lines[2] = "first changed\n"
    lines[32] = "second changed\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")
    first = review_diff(str(tmp_path), "sample.txt")["hunks"][0]
    apply_review_action(
        str(tmp_path), action="stage", path="sample.txt", scope="working",
        hunk_id=first["id"], expected_sha256=first["sha256"],
    )

    # 这条测的是"只提交已暂存的 hunk"，夹具仓库恰好在 main 上；受保护分支的二次确认
    # 另有 tests/unit/test_protected_branch_commit.py 覆盖，这里直接给确认。
    committed = commit_reviewed(str(tmp_path), "reviewed first hunk", confirm_protected=True)
    assert committed["ok"] is True and committed["sha"]
    head_content = _git(tmp_path, "show", "HEAD:sample.txt").stdout
    assert "first changed" in head_content and "second changed" not in head_content
    assert "second changed" in _git(tmp_path, "diff").stdout


def test_reviewed_pr_guards_and_uses_draft_flow(tmp_path, monkeypatch):
    lines = _init_repo(tmp_path)
    base = _git(tmp_path, "branch", "--show-current").stdout.strip()
    with pytest.raises(ValueError, match="受保护分支"):
        open_reviewed_pr(str(tmp_path), title="unsafe", base=base, confirm=True)

    _git(tmp_path, "checkout", "-qb", "feature/desktop-review")
    lines[2] = "feature changed\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")
    _git(tmp_path, "add", "sample.txt")
    _git(tmp_path, "commit", "-qm", "feature")
    with pytest.raises(ValueError, match="显式确认"):
        open_reviewed_pr(str(tmp_path), title="Desktop review", base=base)

    import src.agents.vcs as vcs
    import src.gateway.git_review as git_review

    seen = {}
    monkeypatch.setattr(git_review.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)

    def fake_push(root, branch, title, body, base, remote, draft):
        seen.update({"branch": branch, "base": base, "remote": remote, "draft": draft})
        return {"ok": True, "pushed": True, "url": "https://example.test/pr/1", "error": ""}

    monkeypatch.setattr(vcs, "push_and_open_pr", fake_push)
    result = open_reviewed_pr(
        str(tmp_path), title="Desktop review", body="reviewed", base=base, confirm=True,
    )
    assert result["ok"] is True and result["url"].endswith("/1")
    assert seen == {
        "branch": "feature/desktop-review", "base": base, "remote": "origin", "draft": True,
    }


def test_git_review_rest_endpoints(tmp_path, monkeypatch):
    lines = _init_repo(tmp_path)
    lines[2] = "changed\n"
    (tmp_path / "sample.txt").write_text("".join(lines), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    from fastapi.testclient import TestClient
    from src.web.server import app

    client = TestClient(app)
    snapshot = client.get("/api/git/review")
    assert snapshot.status_code == 200 and snapshot.json()["files"]
    diff = client.get("/api/git/review/diff", params={"path": "sample.txt", "scope": "working"})
    assert diff.status_code == 200 and diff.json()["hunks"]
    hunk = diff.json()["hunks"][0]
    added = next(item for item in hunk["lines"] if item["kind"] == "add")
    created = client.post("/api/git/review/comments", json={
        "path": "sample.txt", "scope": "working", "hunk_id": hunk["id"],
        "expected_sha256": hunk["sha256"], "line": added["new_line"],
        "side": "new", "body": "请补一个回归测试",
    })
    assert created.status_code == 200 and created.json()["status"] == "open"
    comment_id = created.json()["id"]
    assert client.get("/api/git/review/comments").json()["comments"][0]["id"] == comment_id
    assert client.patch(
        f"/api/git/review/comments/{comment_id}", json={"status": "sent"},
    ).json()["status"] == "sent"
    staged = client.post("/api/git/review/action", json={
        "action": "stage", "path": "sample.txt", "scope": "working",
        "hunk_id": hunk["id"], "expected_sha256": hunk["sha256"],
    })
    assert staged.status_code == 200
    stale = client.post("/api/git/review/action", json={
        "action": "stage", "path": "sample.txt", "scope": "working",
        "hunk_id": hunk["id"], "expected_sha256": hunk["sha256"],
    })
    assert stale.status_code == 409
    assert client.delete(f"/api/git/review/comments/{comment_id}").json() == {"ok": True}
