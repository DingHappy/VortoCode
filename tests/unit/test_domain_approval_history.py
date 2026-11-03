"""Domain decisions remain distinct from mutable submission/execution state."""
import json

import pytest

from src.agents.approval_records import approval_events, record_approval
from src.gateway.owner_access import snapshot
from src.scheduling import codex_watch as watch
from src.workflow.tasks import assistant_dispatch as dispatch
from tests.unit.test_assistant_dispatch import registry  # noqa: F401
from tests.unit.test_codex_watch import _every_session_handed_over, _seed  # noqa: F401


def _rows(root, kind):
    return [row for row in snapshot(root)["history"]["entries"] if row["record_type"] == kind]


def test_codex_approval_survives_unknown_and_late_receipt_without_inventing_completion(tmp_path):
    root = str(tmp_path); _seed(root)
    action = watch.propose_action(root, thread="登录", kind="message", text="private-body")
    approved = watch.decide_action(root, action["id"], True, origin="app", decision_source="user")
    [event] = approval_events(approved)
    watch.claim_action(root, action["id"])
    watch.finish_action(root, action["id"], ok=False, uncertain=True)
    [row] = _rows(root, "codex_action")
    assert row["approval_outcome"] == "approved" and row["source_status"] == "unknown"
    assert row["ts"] == event["at"] and row["origin"] == "app" and row["approval_scope"] == "once"
    watch.finish_action(root, action["id"], ok=True)
    [updated] = _rows(root, "codex_action")
    assert updated["source_status"] == "sent" and updated["id"] == row["id"] and updated["ts"] == row["ts"]
    assert "private-body" not in json.dumps(snapshot(root))
    with pytest.raises(watch.CodexActionError):
        watch.decide_action(root, action["id"], True, origin="runtime", decision_source="user")
    assert len(approval_events(watch.load_actions(root)[0])) == 1


async def test_dispatch_approved_but_preflight_failed_then_withdrawn_keeps_both_decisions(tmp_path):
    root = str(tmp_path)
    item = dispatch.propose(root, project="VortoCode", title="private-title", goal="private-goal")

    async def unavailable(*_args, **_kwargs):
        raise OSError("private-host-error")

    with pytest.raises(dispatch.DispatchError):
        await dispatch.approve(root, item["id"], http=unavailable, origin="app", decision_source="user")
    [row] = _rows(root, "dispatch")
    assert row["approval_outcome"] == "approved" and row["source_status"] == "send_failed"
    cancelled = dispatch.cancel(root, item["id"], origin="runtime", decision_source="user")
    assert [e["outcome"] for e in approval_events(cancelled)] == ["approved", "cancelled"]
    rows = _rows(root, "dispatch")
    assert {row["approval_outcome"] for row in rows} == {"approved", "cancelled"}
    assert all(row["source_status"] == "cancelled" for row in rows)
    assert all(secret not in json.dumps(snapshot(root)) for secret in ["private-title", "private-goal", "private-host-error", "/srv/VortoCode"])


def test_legacy_state_is_not_rewritten_as_a_known_approval(tmp_path):
    folder = tmp_path / ".vortocode/codex"; folder.mkdir(parents=True)
    path = folder / "actions.json"
    path.write_text(json.dumps([{"id": "private-id", "status": "sent", "text": "private", "updated_at": "2026-11-03T10:00:00Z"},
                                {"id": "pending", "status": "proposed"}]))
    before = path.read_bytes()
    [row] = _rows(str(tmp_path), "codex_action")
    assert row["approval_outcome"] == row["decision"] == row["origin"] == "unknown"
    assert row["ts"] == "" and row["source_status"] == "sent" and row["decision_metadata_missing"]
    assert path.read_bytes() == before and "private" not in json.dumps(row)


def test_bounded_retention_and_automated_sources_are_explicit(tmp_path):
    item = {"tainted": True}
    for _ in range(21):
        record_approval(item, "approved", at="2026-11-03T10:00:00+00:00", origin="runtime", source="automatic_review")
    assert len(approval_events(item)) == 20 and item["approval_events_truncated"]
    assert all(e["source"] == "automatic_review" and e["scope"] == "none" for e in approval_events(item))
    item.update(id="cdx-x", status="approved", kind="message")
    folder = tmp_path / ".vortocode/codex"; folder.mkdir(parents=True)
    (folder / "actions.json").write_text(json.dumps([item]))
    result = snapshot(str(tmp_path))
    assert result["history"]["sources"]["codex"] == "partial" and result["history"]["status"] == "partial"
    assert all(row["decision_source"] == "automatic_review" for row in result["history"]["entries"])


@pytest.mark.parametrize("raw", ["not-json", "{}", "[null]"])
def test_bad_domain_source_stays_unavailable_without_touching_file(tmp_path, raw):
    folder = tmp_path / ".vortocode/codex"; folder.mkdir(parents=True)
    path = folder / "actions.json"; path.write_text(raw)
    result = snapshot(str(tmp_path))
    assert result["history"]["sources"]["codex"] == "unavailable"
    assert result["history"]["status"] != "current" and path.read_text() == raw


def test_domain_reads_are_bounded_and_do_not_call_submit_or_reconciliation(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("read attempted a mutation")
    monkeypatch.setattr(watch, "expire_stale_actions", forbidden)
    monkeypatch.setattr(watch, "settle_unconfirmed_actions", forbidden)
    monkeypatch.setattr(dispatch, "approve", forbidden)
    folder = tmp_path / ".vortocode/dispatches"; folder.mkdir(parents=True)
    path = folder / "ledger.json"; path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    result = snapshot(str(tmp_path))
    assert result["history"]["sources"]["dispatches"] == "unavailable"
    assert path.stat().st_size == 2 * 1024 * 1024 + 1


def test_bounded_reader_preserves_the_existing_utf8_contract(tmp_path):
    folder = tmp_path / ".vortocode/codex"; folder.mkdir(parents=True)
    (folder / "actions.json").write_bytes(json.dumps([{"id": "a", "status": "sent"}]).encode("utf-16"))
    assert snapshot(str(tmp_path))["history"]["sources"]["codex"] == "unavailable"


def test_broken_existing_events_block_a_new_decision_without_erasing_them(tmp_path):
    root = str(tmp_path)
    item = dispatch.propose(root, project="VortoCode", title="test", goal="test")
    path = tmp_path / ".vortocode/dispatches/ledger.json"
    data = json.loads(path.read_text()); data["dispatches"][0]["approval_events"] = [{"v": 2, "outcome": []}]
    path.write_text(json.dumps(data)); before = path.read_bytes()
    with pytest.raises(dispatch.DispatchError, match="审批记录待核对"):
        dispatch.cancel(root, item["id"], origin="app", decision_source="user")
    assert path.read_bytes() == before
    history = snapshot(root)["history"]
    assert history["sources"]["dispatches"] == "partial" and history["entries"][0]["approval_outcome"] == "unknown"
