"""A temporary choice cannot become a capability, cross-turn grant or taint bypass."""
import asyncio

import pytest

from src.agents.confirmation_context import approval_context
from src.agents.gate import make_confirm_gate
from src.agents.scoped_approval import ApprovalDecision, approval_turn, current_offer, turn_approval_scope
from src.agents.taint import mark_tainted, mark_channel_untrusted, mark_context_external, reset_taint
from src.gateway.app_turns import AppTurns, AppTurnError
from src.gateway.audit import list_audit
from tests.unit.test_app_chat_relay import _settle, shared_chat  # noqa: F401


async def test_ten_actions_then_manual_only_and_no_cross_turn_reuse():
    reset_taint(); asked, audit = [], []
    async def ask(_text, _kind):
        offer = current_offer(); asked.append(offer)
        return ApprovalDecision(True, "turn") if offer else True
    gate = make_confirm_gate(ask, can_ask_human=True, capability_profile="owner_device", enable_turn_scope=True,
        on_decision=lambda *a: audit.append(approval_context()))
    with turn_approval_scope():
        for _ in range(11):
            assert await gate("update own data", "own_data")
    assert len(asked) == 2 and asked[-1] is None
    assert audit[0]["decision_source"] == "user" and audit[0]["approval_scope"] == "turn"
    assert all(a["decision_source"] == "preauthorized" and a["approval_scope"] == "turn" for a in audit[1:10])
    assert audit[-1]["approval_scope"] == "once"
    with turn_approval_scope():
        assert await gate("new turn", "own_data")
    assert len(asked) == 3


@pytest.mark.parametrize("mark", [mark_tainted, mark_channel_untrusted, mark_context_external])
async def test_any_taint_revokes_grants_even_if_a_nested_caller_resets_taint(mark):
    reset_taint(); calls = []
    async def ask(*_args):
        calls.append(current_offer())
        return ApprovalDecision(True, "turn")
    gate = make_confirm_gate(ask, can_ask_human=True, capability_profile="external", enable_turn_scope=True)
    with turn_approval_scope():
        assert await gate("own", "own_data")
        mark(); reset_taint()
        assert not await gate("own", "own_data")
    assert calls[0] is not None and calls[-1] is None


@pytest.mark.parametrize("kind", ["read", "write", "execute", "deliver", "interact", "irreversible", None, "future"])
async def test_other_categories_cannot_request_or_reuse_the_scope(kind):
    reset_taint()
    async def ask(*_args):
        assert current_offer() is None
        return ApprovalDecision(True, "turn")
    gate = make_confirm_gate(ask, can_ask_human=True, capability_profile="local", enable_turn_scope=True)
    with turn_approval_scope():
        assert not await gate("dangerous body pretending to be draft", kind)


@pytest.mark.parametrize("profile,enabled", [("owner_device", False), ("unattended", True), ("researcher", True), ("future", True), (None, True)])
async def test_profiles_and_host_opt_in_are_required(profile, enabled):
    reset_taint()
    async def ask(*_args): return ApprovalDecision(True, "turn")
    with turn_approval_scope():
        assert not await make_confirm_gate(ask, can_ask_human=True, capability_profile=profile, enable_turn_scope=enabled)("own", "own_data")


async def test_expiry_and_inherited_background_context_cannot_extend_lifetime():
    reset_taint(); now = [0.0]
    async def ask(*_args): return ApprovalDecision(True, "turn")
    gate = make_confirm_gate(ask, can_ask_human=True, capability_profile="owner_device", enable_turn_scope=True)
    release = asyncio.Event()
    async def child():
        await release.wait()
        return await gate("late child", "own_data")
    with turn_approval_scope(clock=lambda: now[0]):
        assert await gate("own", "own_data")
        now[0] = 600
        assert not await gate("expired", "own_data")
        task = asyncio.create_task(child())
    release.set()
    assert not await task


async def test_real_main_agent_wrapper_ends_the_scope_on_return_and_error(tmp_path, monkeypatch):
    from src.agents.main_agent import MainAgent
    monkeypatch.chdir(tmp_path)
    captured = []
    async def ask(*_args):
        captured.append(current_offer().state)
        return ApprovalDecision(True, "turn")
    gate = make_confirm_gate(ask, can_ask_human=True, capability_profile="external", enable_turn_scope=True)
    agent = MainAgent([], llm=object())
    async def body(*_a, **_k):
        assert await gate("own", "own_data")
        assert await gate("again", "own_data")
        return "done"
    monkeypatch.setattr(agent, "_run_turn_body", body)
    assert await agent.run_turn("request", mode="build") == "done"
    assert len(captured) == 1 and not captured[0].active
    async def broken(*_a, **_k):
        assert await gate("new", "own_data")
        raise RuntimeError("stop")
    monkeypatch.setattr(agent, "_run_turn_body", broken)
    with pytest.raises(RuntimeError): await agent.run_turn("new", mode="build")
    assert len(captured) == 2 and not captured[1].active


async def test_concurrent_confirmations_share_a_cap_without_replenishing_it():
    reset_taint()
    arrived, release = asyncio.Event(), asyncio.Event()
    asks = []
    async def ask(*_args):
        asks.append(current_offer())
        if len(asks) == 12: arrived.set()
        await release.wait()
        return ApprovalDecision(True, "turn") if current_offer() else True
    gate = make_confirm_gate(ask, can_ask_human=True, capability_profile="local", enable_turn_scope=True)
    with turn_approval_scope() as state:
        tasks = [asyncio.create_task(gate("own", "own_data")) for _ in range(12)]
        await asyncio.wait_for(arrived.wait(), 1)
        release.set()
        assert await asyncio.gather(*tasks) == [True] * 12
        assert state.grants["own_data"] == 0
        assert await gate("explicit once after exhaustion", "own_data")
        assert asks[-1] is None and state.grants["own_data"] == 0
        # A distinct low-risk category needs its own explicit approval.
        assert await gate("draft", "draft")


async def test_cancellation_closes_the_real_turn_scope(tmp_path, monkeypatch):
    from src.agents.main_agent import MainAgent
    monkeypatch.chdir(tmp_path)
    arrived = asyncio.Event(); states = []
    async def ask(*_args):
        states.append(current_offer().state)
        arrived.set()
        await asyncio.Event().wait()
    gate = make_confirm_gate(ask, can_ask_human=True, capability_profile="local", enable_turn_scope=True)
    agent = MainAgent([], llm=object())
    async def body(*_a, **_k): return await gate("own", "own_data")
    monkeypatch.setattr(agent, "_run_turn_body", body)
    task = asyncio.create_task(agent.run_turn("request", mode="build"))
    await asyncio.wait_for(arrived.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert states[0].active is False


async def test_taint_during_confirmation_keeps_only_the_explicit_single_approval():
    reset_taint()
    async def ask(*_args):
        assert current_offer() is not None
        mark_tainted()
        return ApprovalDecision(True, "turn")
    seen = []
    with turn_approval_scope() as state:
        assert await make_confirm_gate(ask, can_ask_human=True, capability_profile="local", enable_turn_scope=True,
                                      on_decision=lambda *_args: seen.append(approval_context()))("own", "own_data")
        assert state.tainted and not state.grants
    assert seen[0]["approval_scope"] == "once" and seen[0]["approval_outcome"] == "approved"
    assert seen[0]["decision_source"] == "user"


@pytest.mark.parametrize("change", ["expired", "exhausted", "revoked"])
async def test_a_failed_range_grant_never_turns_an_explicit_approval_into_a_rejection(change):
    reset_taint(); now = [0.0]; asked, seen = [], []
    async def ask(*_args):
        offer = current_offer(); asked.append(offer)
        if offer is None:
            return True
        if change == "expired": now[0] = 600
        elif change == "exhausted": offer.state.grants[offer.kind] = 0
        else: offer.state.revoke()
        return ApprovalDecision(True, "turn")
    gate = make_confirm_gate(ask, can_ask_human=True, capability_profile="local", enable_turn_scope=True,
                             on_decision=lambda *_args: seen.append(approval_context()))
    with turn_approval_scope(clock=lambda: now[0]):
        assert await gate("first", "own_data")
        assert await gate("another explicit approval", "own_data")
    assert len(asked) == 2 and asked[0] is not None and asked[1] is None
    assert all(row["approval_scope"] == "once" and row["decision_source"] == "user"
               and row["approval_outcome"] == "approved" for row in seen)


async def test_app_range_grant_race_preserves_the_approved_card_and_single_action(tmp_path, shared_chat, monkeypatch):
    from src.agents.scoped_approval import ScopeOffer
    seen, results = [], []
    class Agent:
        history = []
        @approval_turn
        async def run_turn(self, *_args, **_kwargs):
            reset_taint()
            gate = make_confirm_gate(manager._confirm, can_ask_human=True, capability_profile="owner_device",
                                     enable_turn_scope=True, on_decision=lambda *_args: seen.append(approval_context()))
            results.append(await gate("own", "own_data"))
            return "done"
    manager = AppTurns(str(tmp_path), lambda _confirm: Agent())
    turn = manager.start("req-scope-race-001", "own")
    await _settle(turn, ("waiting",))
    card = turn.cards[0]
    manager.decide(turn.rid, card.id, True, card.digest(turn.rid), "approval", scope="turn")
    monkeypatch.setattr(ScopeOffer, "grant", lambda _self: False)
    await _settle(turn, ("done",))
    assert card.status == "approved" and results == [True]
    assert seen[0]["approval_scope"] == "once" and seen[0]["approval_outcome"] == "approved"


@pytest.mark.parametrize("failure", ["expired", "tainted", "save_failed", "changed_limits"])
async def test_app_pending_range_is_rechecked_before_execution(tmp_path, shared_chat, monkeypatch, failure):
    from src.agents import scoped_approval
    from src.gateway import audit
    now = [0.0]; results = []
    class Agent:
        history = []
        async def run_turn(self, *_a, **_k):
            reset_taint()
            with turn_approval_scope(clock=lambda: now[0]):
                gate = make_confirm_gate(manager._confirm, can_ask_human=True, capability_profile="owner_device", enable_turn_scope=True)
                results.append(await gate("own", "own_data"))
            return "done"
    manager = AppTurns(str(tmp_path), lambda _confirm: Agent())
    turn = manager.start("req-scope-recheck", "own")
    await _settle(turn, ("waiting",))
    card = turn.cards[0]; digest = card.digest(turn.rid)
    if failure == "expired": now[0] = 600
    elif failure == "tainted": card.scope_offer.state.revoke()
    elif failure == "save_failed": monkeypatch.setattr(audit, "record_decision_audit", lambda *_a, **_k: False)
    else: monkeypatch.setattr(scoped_approval, "MAX_ACTIONS", 9)
    if failure in {"expired", "tainted"}:
        manager.decide(turn.rid, card.id, True, digest, "approval", scope="turn")
        await _settle(turn, ("done",))
        assert results == [True] and card.status == "approved"
        assert not card.scope_offer.state.grants
        return
    with pytest.raises(AppTurnError): manager.decide(turn.rid, card.id, True, digest, "approval", scope="turn")
    assert not results and card.status == "pending"
    manager.decide(turn.rid, card.id, False, card.digest(turn.rid), "device")
    await _settle(turn, ("done",))
    assert results == [False]


async def test_app_scope_is_bound_to_card_and_requires_approval_key(tmp_path, shared_chat):
    results = []
    class Agent:
        history = []
        def __init__(self, confirm):
            self.gate = make_confirm_gate(confirm, can_ask_human=True, capability_profile="owner_device", enable_turn_scope=True,
                                          on_decision=manager.audit_decision)
        @approval_turn
        async def run_turn(self, *_args, **_kwargs):
            reset_taint()
            for _ in range(3):
                ok = await self.gate("private own data", "own_data"); results.append(ok)
                if not ok: return "denied"
            return "done"
    manager = AppTurns(str(tmp_path), lambda confirm: Agent(confirm))
    turn = manager.start("req-scope-0001", "do three updates")
    await _settle(turn, ("waiting",))
    card = turn.cards[0].public(turn.rid)
    assert card["scope_options"] == ["once", "turn"] and card["scope_keys"]["turn"] == "approval"
    assert card["scope_limits"] == {"max_actions": 10, "ttl_seconds": 600}
    with pytest.raises(AppTurnError): manager.decide(turn.rid, card["id"], True, card["digest"], "device", scope="turn")
    with pytest.raises(AppTurnError): manager.decide(turn.rid, card["id"], True, card["digest"], "approval", scope="always")
    manager.decide(turn.rid, card["id"], True, card["digest"], "approval", scope="turn")
    await _settle(turn, ("done",))
    assert turn.reply == "done" and results == [True, True, True] and len(turn.cards) == 1
    decisions = [r for r in list_audit(str(tmp_path)) if r["category"] == "decision"]
    assert all(r["approval_scope"] == "turn" for r in decisions)
