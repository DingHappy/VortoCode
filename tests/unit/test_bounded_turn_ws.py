"""The normal owner WebSocket transports a choice, never a client-created grant."""
import asyncio

import pytest

from src.agents.gate import make_confirm_gate
from src.agents.scoped_approval import approval_turn
from src.agents.taint import reset_taint
from src.gateway.turn_bindings import TurnBindings
from src.gateway.audit import list_audit
from src.web.routers import realtime
from tests.unit.test_gateway_decisions import _DecisionWS


@pytest.mark.parametrize("failure", [None, "scope", "expired", "tainted", "grant_race", "save_failed", "sensitive"])
async def test_scope_choice_uses_owner_future_and_durable_audit(tmp_path, monkeypatch, failure):
    monkeypatch.chdir(tmp_path)
    owner, stranger = _DecisionWS("scope-owner"), _DecisionWS("scope-stranger")
    q = asyncio.Queue(); bindings = TurnBindings()
    bindings.bind(confirm=realtime._make_ws_confirm(owner, q))
    gate = make_confirm_gate(bindings.ask, can_ask_human=True, capability_profile="local", enable_turn_scope=True)
    @approval_turn
    async def run():
        reset_taint()
        first = await gate("update", "deliver" if failure == "sensitive" else "own_data")
        return first, await gate("second update", "own_data") if first else False
    task = asyncio.create_task(run())
    event = await asyncio.wait_for(q.get(), 1)
    pending = realtime.pending_confirmations("sid-scope-owner")[0]
    assert pending["scope_options"] == event["scope_options"] == (["once"] if failure == "sensitive" else ["once", "turn"])
    assert "scope_offer" not in pending and "root" not in pending
    response = {"type": "agent_confirm_response", "id": event["id"], "ok": True, "scope": "turn"}
    await realtime.handle_websocket_message(stranger, response)
    await asyncio.sleep(0)
    assert not task.done()
    record = realtime._PENDING_CONFIRMS[event["id"]]
    if failure == "scope": response["scope"] = "always"
    elif failure == "expired": record["scope_offer"].state.clock = lambda: record["scope_offer"].state.born + 600
    elif failure == "tainted": record["scope_offer"].state.revoke()
    elif failure == "grant_race":
        monkeypatch.setattr("src.agents.scoped_approval.ScopeOffer.grant", lambda _self: False)
    elif failure == "save_failed":
        monkeypatch.setattr("src.gateway.audit.record_decision_audit", lambda *_a, **_k: False)
    await realtime.handle_websocket_message(owner, response)
    if failure in {"expired", "tainted", "grant_race"}:
        closed = await asyncio.wait_for(q.get(), 1)
        assert closed["type"] == "agent_confirm_closed" and closed["id"] == event["id"]
        followup = await asyncio.wait_for(q.get(), 1)
        if failure != "grant_race":
            assert followup["scope_options"] == ["once"]
        await realtime.handle_websocket_message(owner, {"type": "agent_confirm_response", "id": followup["id"], "ok": False})
    expected = (True, True) if failure is None else (True, False) if failure in {"expired", "tainted", "grant_race"} else (False, False)
    assert await asyncio.wait_for(task, 1) == expected
    assert event["id"] not in realtime._PENDING_CONFIRMS
    # A processed card cannot grant anything to a later turn.
    await realtime.handle_websocket_message(owner, response)
    if failure is None:
        [entry] = list_audit(str(tmp_path))
        assert entry["approval_scope"] == "turn" and entry["decision_source"] == "user"
    elif failure in {"expired", "tainted"}:
        [entry] = list_audit(str(tmp_path))
        assert entry["approval_scope"] == "once" and entry["approval_outcome"] == "approved"
