"""Restart hints persist; approval/execution and original Futures never recover."""
import os
from pathlib import Path
import stat
import subprocess
import sys
import asyncio
import threading

import pytest

from src.gateway import audit
from src.gateway.app_approval_journal import card_ref, open_card
from src.gateway.app_turns import AppTurnError, AppTurns
from src.gateway.owner_access import snapshot
from fastapi.testclient import TestClient
from tests.unit.test_app_chat_relay import _turns, _settle, shared_chat  # noqa: F401
from tests.unit.test_mobile_browser import setup, native_handoff  # noqa: F401
from tests.unit.test_mobile_owner import owner, bind, signed  # noqa: F401


def waits(root, live=None):
    return [row for row in snapshot(str(root), live_cards=live)["history"]["entries"] if row["record_type"] == "card_wait"]


async def test_abrupt_process_exit_leaves_a_private_non_executable_hint(tmp_path):
    code = '''import asyncio, os, sys
from src.gateway.app_turns import AppTurn, AppTurns
async def run():
    manager = AppTurns(sys.argv[1], lambda _confirm: None)
    turn = AppTurn("app-" + "a" * 16, "req-crash-001", "private message")
    manager.turns[turn.rid] = turn
    manager._current = turn
    asyncio.create_task(manager._confirm("private operation", "own_data"))
    await asyncio.sleep(0)
    os._exit(0)
asyncio.run(run())
'''
    subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=Path(__file__).resolve().parents[2], check=True, timeout=15)
    raw = (tmp_path / ".vortoone/audit.log").read_text()
    assert "private" not in raw and "req-crash-001" not in raw and "digest" not in raw
    [row] = waits(tmp_path, set())
    assert row["approval_outcome"] == "interrupted" and row["decision"] == "unknown"
    manager = AppTurns(str(tmp_path), lambda _confirm: pytest.fail("read/retry constructed an agent"))
    assert manager.turns == {} and not manager.pending_card_refs()
    with pytest.raises(AppTurnError, match="留有审批记录"):
        await manager.start("req-crash-001", "retry after lost response")
    with pytest.raises(KeyError):
        manager.decide("app-" + "a" * 16, "old-card", True, "d" * 64, "approval")


def test_reader_without_live_evidence_does_not_assert_interruption(tmp_path):
    assert open_card(str(tmp_path), rid="r", cid="c", request_id="req-test-001", kind="deliver", tainted=True)
    [unknown] = waits(tmp_path)
    [pending] = waits(tmp_path, {card_ref("r", "c")})
    [lost] = waits(tmp_path, set())
    assert unknown["approval_outcome"] == "unknown"
    assert pending["approval_outcome"] == "pending" and pending["tainted"]
    assert lost["approval_outcome"] == "interrupted"


async def test_start_reads_off_loop_and_concurrent_retries_create_one_turn(tmp_path, monkeypatch, shared_chat):
    turns, _ = _turns(tmp_path)
    original = audit.audit_snapshot
    entered, release, lock = threading.Event(), threading.Event(), threading.Lock()
    readers = []
    def blocked(*args):
        with lock:
            readers.append(threading.get_ident())
            if len(readers) == 2:
                entered.set()
        assert release.wait(5), "test did not release the audit reader"
        return original(*args)
    monkeypatch.setattr(audit, "audit_snapshot", blocked)
    pending = [asyncio.create_task(turns.start("req-concurrent-001", "check")) for _ in range(2)]
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        assert all(not task.done() for task in pending)
        assert threading.get_ident() not in readers
    finally:
        release.set()
    first, second = await asyncio.gather(*pending)
    assert first is second and len(turns.turns) == 1
    await _settle(first, ("done",))
    assert len(shared_chat) == 1


async def test_unreadable_audit_blocks_new_turns_but_not_an_existing_request(tmp_path, monkeypatch, shared_chat):
    turns, agents = _turns(tmp_path)
    turn = await turns.start("req-existing-001", "check")
    await _settle(turn, ("done",))
    monkeypatch.setattr(audit, "audit_snapshot", lambda *_args: {"status": "unavailable", "entries": []})
    assert await turns.start("req-existing-001", "retry") is turn
    with pytest.raises(AppTurnError, match="未开始新回合"):
        await turns.start("req-new-0001", "new")
    assert len(turns.turns) == len(agents) == len(shared_chat) == 1


async def test_closed_card_does_not_come_back_as_an_interrupted_wait(tmp_path, shared_chat):
    turns, _ = _turns(tmp_path, ask="private confirmation")
    turn = await turns.start("req-journal-001", "check")
    await _settle(turn, ("waiting",))
    assert waits(tmp_path, turns.pending_card_refs())[0]["approval_outcome"] == "pending"
    card = turn.cards[0].public(turn.rid)
    turns.decide(turn.rid, card["id"], False, card["digest"], "device")
    await _settle(turn, ("done",))
    assert not waits(tmp_path, set())
    before = (tmp_path / ".vortoone/audit.log").read_bytes()
    assert not waits(tmp_path, set()) and (tmp_path / ".vortoone/audit.log").read_bytes() == before


async def test_opening_persistence_failure_never_exposes_a_card(tmp_path, monkeypatch, shared_chat):
    monkeypatch.setattr("src.gateway.app_approval_journal.open_card", lambda *a, **k: False)
    turns, _ = _turns(tmp_path, ask="check")
    turn = await turns.start("req-journal-002", "check")
    await _settle(turn, ("failed",))
    assert turn.state == "failed" and "未开放" in turn.error and not turn.cards


async def test_failed_approval_save_keeps_future_pending_until_an_explicit_retry(tmp_path, monkeypatch, shared_chat):
    turns, _ = _turns(tmp_path, ask="check")
    turn = await turns.start("req-journal-003", "check")
    await _settle(turn, ("waiting",))
    card = turn.cards[0].public(turn.rid)
    with monkeypatch.context() as patch:
        patch.setattr(audit, "record_decision_audit", lambda *a, **k: False)
        with pytest.raises(AppTurnError, match="未执行这次批准"):
            turns.decide(turn.rid, card["id"], True, card["digest"], "device")
    assert turn.cards[0].status == "pending" and not turn.cards[0].future.done()
    turns.decide(turn.rid, card["id"], True, card["digest"], "device")
    await _settle(turn, ("done",))
    assert turn.reply == "已记下" and not waits(tmp_path, set())


def test_durable_append_flushes_file_and_directory_and_reports_failure(tmp_path, monkeypatch):
    synced = []
    real = os.fsync

    def track(fd):
        synced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        real(fd)

    monkeypatch.setattr(os, "fsync", track)
    assert audit.append_audit(str(tmp_path), {"category": "event", "event": "test"}, durable=True)
    assert False in synced and (os.name == "nt" or True in synced)

    def failed(_fd):
        raise OSError("unconfirmed")

    monkeypatch.setattr(os, "fsync", failed)
    assert not audit.append_audit(str(tmp_path), {"category": "event", "event": "test"}, durable=True)


def test_malformed_opening_is_a_warning_and_never_a_restorable_card(tmp_path):
    audit.append_audit(str(tmp_path), {"category": "event", "event": "app_card_opened", "data": {"card_ref": "bad"}})
    result = snapshot(str(tmp_path), live_cards=set())
    assert result["history"]["sources"]["app_cards"] == "partial" and not waits(tmp_path, set())


def test_resident_read_identifies_lost_wait_without_constructing_an_agent(tmp_path, monkeypatch):
    root = str(tmp_path)
    assert open_card(root, rid="r", cid="c", request_id="req-api-001", kind="own_data", tainted=False)
    before = (tmp_path / ".vortoone/audit.log").read_bytes()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOONE_API_TOKEN", "admin-token")
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    from src.web.routers import app_relay
    monkeypatch.setattr(app_relay, "_turns", None)
    from src.web.server import app
    data = TestClient(app).get("/api/assistant/access", headers={"Authorization": "Bearer admin-token"}).json()
    [row] = [r for r in data["history"]["entries"] if r["record_type"] == "card_wait"]
    assert row["approval_outcome"] == "interrupted" and app_relay._turns is None
    assert (tmp_path / ".vortoone/audit.log").read_bytes() == before


def test_known_request_replay_returns_a_review_code_without_starting_a_turn(tmp_path, monkeypatch):
    from tests.unit.test_relay_envelope import envelope, phone
    root = str(tmp_path)
    assert open_card(root, rid="r", cid="c", request_id="req-api-002", kind="own_data", tainted=False)
    device_id, device, _ = phone(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOONE_API_TOKEN", "admin-token")
    monkeypatch.setenv("VORTOONE_APP_RELAY_TOKEN", "relay-token")
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    from src.web.routers import app_relay
    manager = AppTurns(root, lambda _: pytest.fail("replay built an Agent"))
    monkeypatch.setattr(app_relay, "_turns", manager)
    from src.web.server import app
    proof = envelope(device_id, device, "/api/mobile/native/chat/send", {"request_id": "req-api-002", "text": "private text"})
    response = TestClient(app, client=("127.0.0.1", 5000)).post(app_relay.TURN_PATH, json={"envelope": proof}, headers={"Authorization": "Bearer relay-token"})
    assert response.status_code == 409 and response.json()["code"] == "app_request_review_required"
    assert not manager.turns and manager._agent is None


def test_native_gateway_forwards_only_the_known_review_code(owner, monkeypatch):  # noqa: F811
    client, headers, device_id, _root = owner
    _, device, _ = bind(client, headers)

    async def relay(_path, _body):
        return 409, {"detail": "请先核对旧请求", "code": "app_request_review_required", "untrusted_extra": "not-forwarded"}

    monkeypatch.setattr("src.gateway.app_relay_client.relay", relay)
    response = signed(client, headers, device_id, "/api/mobile/native/chat/send",
                      {"request_id": "req-mobile-review-001", "text": "check"}, device)
    assert response.status_code == 409 and response.json()["code"] == "app_request_review_required"
    assert "untrusted_extra" not in response.json()
