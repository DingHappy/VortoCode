"""Session ownership and recovery without a WebSocket or model runtime."""
import asyncio

import pytest

from src.gateway.session_actor import ActorContext, SessionActors, new_prompt_item
from src.utils.async_ops import cancel_requested


def prompt(name):
    return new_prompt_item(text=name, mode="plan", images=[], audio=[], rid=name,
                           want_reasoning=False, context_items=[])


@pytest.mark.asyncio
async def test_observers_share_actor_while_other_sessions_run_independently():
    actors = SessionActors(clean_item=lambda raw: raw)
    sessions = {key: {} for key in ("one", "two")}
    entered = []
    gate = asyncio.Event()
    publish_gate = asyncio.Event()

    async def execute(item):
        entered.append(item["id"])
        await gate.wait()

    async def publish():
        await publish_gate.wait()

    def context(key):
        return ActorContext(key, lambda: sessions[key], lambda: None, lambda: None,
                            publish, execute)

    first = asyncio.create_task(actors.start(context("one"), prompt("a")))
    await asyncio.sleep(0)
    assert not await actors.start(context("one"), prompt("duplicate-window"))
    second = asyncio.create_task(actors.start(context("two"), prompt("b")))
    await asyncio.sleep(0)
    assert actors.busy("one") and actors.busy("two")
    publish_gate.set()
    assert await first and await second
    gate.set()
    await asyncio.gather(*actors.tasks.values())
    assert entered == ["a", "b"]


@pytest.mark.asyncio
async def test_hydrated_queue_runs_once_and_stop_preserves_remaining_input():
    actors = SessionActors(clean_item=lambda raw: raw if isinstance(raw, dict) else None)
    session = {"prompt_queue": [None, prompt("a"), prompt("a"), prompt("b")]}
    entered = asyncio.Event()
    seen = []
    persisted = []

    async def publish():
        pass

    async def execute(item):
        seen.append(item["id"])
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            pass
        finally:
            reason = actors.release("owner", asyncio.current_task())
            await actors.advance(context, reason=reason)

    context = ActorContext("owner", lambda: session, lambda: None,
                           lambda: persisted.append([p["id"] for p in session["prompt_queue"]]),
                           publish, execute)
    await actors.resume(context)
    await entered.wait()
    task = actors.tasks["owner"]
    await actors.resume(context)
    assert actors.tasks["owner"] is task
    assert actors.cancel("owner", reason="stop")
    await task
    assert seen == ["a"]
    assert actors.snapshot(context)["running"] is None
    assert persisted[-1] == ["b"]
    assert not actors.busy("owner")
    await actors.remove(context, "b")
    assert actors.snapshot(context)["items"] == []


def immediate_context(actors, *, execute=None, cancel_unstarted=None):
    session = {}
    persisted = []

    async def publish():
        pass

    async def unexpected_execution(item):
        raise AssertionError("cancelled input must not execute")

    context = ActorContext(
        "owner", lambda: session, lambda: None,
        lambda: persisted.append([p["id"] for p in session.get("prompt_queue", [])]),
        publish, execute or unexpected_execution, cancel_unstarted,
    )
    return context, session, persisted


async def drain(actors):
    for _ in range(30):
        if not actors.tasks:
            return
        await asyncio.gather(*actors.tasks.values(), return_exceptions=True)
        await asyncio.sleep(0)
    raise AssertionError("actor lifecycle did not finish")


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["stop", "disconnect", "direct"])
async def test_cancel_before_first_timeslice_preserves_input_and_releases_slot(reason):
    actors = SessionActors(clean_item=lambda raw: raw)
    cancelled = []

    async def notify(item, why):
        cancelled.append((item["id"], why))

    context, session, persisted = immediate_context(actors, cancel_unstarted=notify)
    assert await actors.start(context, prompt("first"))
    assert await actors.enqueue(context, prompt("second")) == "queued"
    if reason == "direct":
        actors.tasks["owner"].cancel()
    else:
        assert actors.cancel("owner", reason=reason)
    await drain(actors)
    assert not actors.busy("owner") and not actors.running and not actors.stop_reasons
    assert [p["id"] for p in session["prompt_queue"]] == ["first", "second"]
    assert persisted[-1] == ["first", "second"]
    assert cancelled == [("first", "stop" if reason == "direct" else reason)]
    assert "owner" in actors.auto_stopped


@pytest.mark.asyncio
async def test_unstarted_turn_restores_once_and_send_now_keeps_fifo():
    actors = SessionActors(clean_item=lambda raw: raw)
    seen = []

    async def execute(item):
        seen.append(item["id"])

    context, _, _ = immediate_context(actors, execute=execute)
    await actors.start(context, prompt("first"))
    await actors.enqueue(context, prompt("second"))
    await actors.enqueue(context, prompt("third"))
    await actors.send_now(context, "third")
    await drain(actors)
    assert seen == ["third", "first", "second"]
    assert actors.snapshot(context) == {"items": [], "running": None}


@pytest.mark.asyncio
async def test_done_pending_task_still_owns_slot_until_cleanup_applies_latest_stop():
    actors = SessionActors(clean_item=lambda raw: raw)
    context, session, _ = immediate_context(actors)
    await actors.start(context, prompt("first"))
    task = actors.tasks["owner"]
    task.cancel()
    await asyncio.sleep(0)  # task is done; its done callback has not run yet.
    assert task.done() and actors.busy("owner")
    assert actors.cancel("owner", reason="disconnect")
    assert not await actors.start(context, prompt("must-not-start"))
    await drain(actors)
    assert [p["id"] for p in session["prompt_queue"]] == ["first"]
    assert not actors.busy("owner")


@pytest.mark.asyncio
async def test_cleanup_is_reserved_and_repeated_stop_does_not_cancel_notification():
    actors = SessionActors(clean_item=lambda raw: raw)
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def execute(item):
        seen.append(item["id"])

    async def notify(item, reason):
        entered.set()
        await release.wait()

    context, session, _ = immediate_context(actors, execute=execute, cancel_unstarted=notify)
    await actors.start(context, prompt("first"))
    await actors.enqueue(context, prompt("priority"))
    await actors.send_now(context, "priority")
    await entered.wait()
    cleanup = actors.tasks["owner"]
    assert actors.busy("owner")
    await actors.advance(context, reason=None)  # stale completion cannot clear current state.
    assert actors.running["owner"]["id"] == "first" and "owner" in actors.priority
    assert actors.cancel("owner", reason="stop")
    assert not cancel_requested(cleanup)
    release.set()
    await drain(actors)
    assert not seen and session["prompt_queue"][0]["id"] == "first"
    assert actors.priority["owner"]["id"] == "priority"
    await actors.resume(context)
    await drain(actors)
    assert seen == ["priority", "first"]


@pytest.mark.asyncio
async def test_cleanup_notification_failure_does_not_leak_actor():
    actors = SessionActors(clean_item=lambda raw: raw)

    async def notify(item, reason):
        raise OSError("observer is closed")

    context, session, _ = immediate_context(actors, cancel_unstarted=notify)
    await actors.start(context, prompt("first"))
    actors.cancel("owner")
    await drain(actors)
    assert not actors.busy("owner") and not actors.running
    assert session["prompt_queue"][0]["id"] == "first"


@pytest.mark.asyncio
async def test_entered_turn_is_not_replayed_after_stop():
    actors = SessionActors(clean_item=lambda raw: raw)
    entered = asyncio.Event()

    async def execute(item):
        entered.set()
        await asyncio.Future()

    context, session, _ = immediate_context(actors, execute=execute)
    await actors.start(context, prompt("first"))
    await entered.wait()
    await actors.enqueue(context, prompt("second"))
    actors.cancel("owner")
    await drain(actors)
    assert [p["id"] for p in session["prompt_queue"]] == ["second"]
    assert not actors.busy("owner")


@pytest.mark.asyncio
async def test_queue_capacity_reserves_unstarted_input_and_duplicates_are_idempotent():
    actors = SessionActors(clean_item=lambda raw: raw, max_queue=3)
    context, session, _ = immediate_context(actors)
    await actors.start(context, prompt("first"))
    assert await actors.enqueue(context, prompt("second")) == "queued"
    assert await actors.enqueue(context, prompt("third")) == "queued"
    assert await actors.enqueue(context, prompt("third")) == "duplicate"
    assert await actors.enqueue(context, prompt("overflow")) == "full"
    actors.cancel("owner")
    await drain(actors)
    assert [p["id"] for p in session["prompt_queue"]] == ["first", "second", "third"]
    assert await actors.enqueue(context, prompt("first")) == "duplicate"
    assert await actors.enqueue(context, prompt("overflow")) == "full"


@pytest.mark.asyncio
async def test_explicit_deletion_drops_paused_priority_before_reusing_session_identity():
    actors = SessionActors(clean_item=lambda raw: raw)
    seen = []

    async def execute(item):
        seen.append(item["id"])

    async def check():
        seen.append("new-check")

    context, _, _ = immediate_context(actors, execute=execute)
    actors.priority["owner"] = prompt("deleted-input")
    actors.auto_stopped.add("owner")
    assert not await actors.start_background(context, check)
    assert actors.forget("owner")
    await actors.resume(context)
    assert not seen and actors.snapshot(context)["running"] is None
    assert await actors.start_background(context, check)
    await drain(actors)
    assert seen == ["new-check"]


@pytest.mark.asyncio
async def test_explicit_deletion_cannot_forget_done_task_awaiting_cleanup():
    actors = SessionActors(clean_item=lambda raw: raw)
    context, session, _ = immediate_context(actors)
    await actors.start(context, prompt("first"))
    task = actors.tasks["owner"]
    assert not actors.forget("owner")
    actors.cancel("owner")
    await asyncio.sleep(0)
    assert task.done() and not actors.forget("owner")
    await drain(actors)
    assert session["prompt_queue"][0]["id"] == "first"
    assert actors.forget("owner") and "owner" not in actors.auto_stopped
