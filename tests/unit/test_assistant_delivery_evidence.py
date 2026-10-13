from copy import deepcopy

import pytest

from src.workflow.tasks.assistant_delivery import delivery_evidence

HEAD = "a" * 40
OLD = "b" * 40


def snapshot():
    return {"ok": True, "target": "task", "task_id": "t1", "branch": "vorto/fix", "head_oid": HEAD,
            "review": {"verification_stale": False, "verification": {"ok": True, "head": HEAD,
                "at": "2026-10-12T12:00:00Z"}, "coverage": {"known": True, "truncated": False,
                "head": HEAD, "accepted_hunks": 1, "total_hunks": 2}}}


def project(data):
    return delivery_evidence(data, task_id="t1", branch="vorto/fix")


def test_only_matching_dated_commit_can_be_reported_as_verified():
    data = snapshot()
    assert project(data)["verification"] == "passed"
    data["review"]["verification"]["ok"] = False
    assert project(data)["verification"] == "failed"
    data["review"]["verification"]["head"] = OLD
    assert project(data)["verification"] == "stale"
    data["review"]["verification"]["head"] = HEAD
    data["review"]["verification"]["at"] = "2026-10-12T12:00:00"
    assert project(data)["verification"] == "unknown"
    data["review"]["verification"] = {"ok": "true", "head": HEAD}
    assert project(data)["verification"] == "unknown"


def test_absent_or_mutated_evidence_cannot_become_a_pass():
    data = snapshot()
    data["review"].pop("verification")
    assert project(data)["verification"] == "unknown"
    data["review"]["verification_stale"] = True
    assert project(data)["verification"] == "stale"


@pytest.mark.parametrize("key,value", [("task_id", "another"), ("branch", "vorto/other"),
                                      ("head_oid", "bad"), ("head_oid", int("1" * 40)),
                                      ("ok", "true"), ("target", "workspace")])
def test_wrong_task_branch_or_commit_is_rejected(key, value):
    data = snapshot()
    data[key] = value
    with pytest.raises(ValueError, match="不匹配"):
        project(data)


@pytest.mark.parametrize("changes", [{"head": OLD}, {"truncated": True}, {"accepted_hunks": 3},
                                    {"total_hunks": True}, {"known": False}])
def test_stale_partial_or_invalid_hunk_coverage_is_unknown(changes):
    data = deepcopy(snapshot())
    data["review"]["coverage"].update(changes)
    result = project(data)
    assert result["coverage_known"] is False and result["accepted_hunks"] is None


async def test_explicit_refresh_reads_review_but_regular_poll_does_not(tmp_path, monkeypatch):
    import json
    from src.workflow.tasks import assistant_dispatch as dispatch

    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"projects": [{"name": "demo", "repo_root": "/srv/demo", "port": 8082}]}))
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(registry))
    root = str(tmp_path)
    entry = dispatch.propose(root, project="demo", title="fix", goal="repair")
    dispatch._update(root, entry["id"], lambda item: item.update(status="running", remote_task_id="t1"))
    urls = []

    async def http(method, url, payload=None):
        assert method == "GET"
        urls.append(url)
        return snapshot() if url.endswith("/review") else {"id": "t1", "execution_scope": {"repo_root": "/srv/demo"}, "status": "done", "branch": "vorto/fix"}

    item = (await dispatch.refresh(root, http=http))[0]
    assert len(urls) == 1 and not item.get("delivery_evidence")
    item = (await dispatch.refresh(root, http=http, dispatch_id=entry["id"]))[0]
    assert len(urls) == 3 and item["delivery_evidence"]["verification"] == "passed"
    assert item["delivery_evidence"]["head"] == HEAD

    async def invalid(method, url, payload=None):
        if url.endswith("/review"):
            raise OSError("review offline")
        return {"id": "t1", "execution_scope": {"repo_root": "/srv/demo"}, "status": "done", "branch": "vorto/fix"}

    item = (await dispatch.refresh(root, http=invalid, dispatch_id=entry["id"]))[0]
    assert item["status"] == "done" and item["sync_state"] == "current"
    assert item["delivery_evidence"] is None and "offline" in item["delivery_error"]
