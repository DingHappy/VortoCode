"""Event ordering and durable recovery without a WebSocket runtime."""
import asyncio

import pytest

from src.gateway import protocol as P
from src.gateway.session_event_stream import SessionEventStream
from src.gateway.session_events import SessionEventJournal


class Observer:
    def __init__(self, *, fail=False):
        self.events = []
        self.fail = fail

    async def send(self, event):
        if self.fail:
            raise OSError("observer disconnected")
        self.events.append(event)


def stream(journal_for=lambda key: None, *, max_events=500, failures=None, send=None):
    return SessionEventStream(
        journal_for=journal_for,
        send=send or (lambda recipient, event: recipient.send(event)),
        on_persist_failure=lambda key, seq: failures.append((key, seq)) if failures is not None else None,
        max_events=max_events,
    )


@pytest.mark.asyncio
async def test_attach_catchup_and_concurrent_publish_have_one_order():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def send(recipient, event):
        if event.get("seq") == 1:
            entered.set()
            await release.wait()
        await recipient.send(event)

    events = stream(send=send)
    start_cursor = await events.cursor("owner")
    await events.publish("owner", P.make_event(P.AGENT_SAY, text="during hydrate"))
    observer = Observer()
    attaching = asyncio.create_task(events.attach("owner", observer, start_cursor))
    await entered.wait()
    publishing = asyncio.create_task(events.publish("owner", P.make_event(P.AGENT_SAY, text="live")))
    await asyncio.sleep(0)
    assert not publishing.done()
    release.set()
    await asyncio.gather(attaching, publishing)
    other = Observer()
    await events.attach("owner", other, 0)
    await asyncio.gather(*(events.publish("owner", P.make_event(P.AGENT_SAY, text=str(i)))
                           for i in range(12)))
    assert [e["seq"] for e in observer.events] == list(range(1, 15))
    assert other.events == observer.events
    assert await events.cursor("different-owner") == 0


@pytest.mark.asyncio
async def test_restart_retains_cursor_but_excludes_temporary_events(tmp_path):
    def journal_for(key):
        return SessionEventJournal(str(tmp_path), key)

    first = stream(journal_for)
    await first.publish("sid-owner", P.make_event(P.AGENT_SAY, text="result"))
    await first.publish("sid-owner", P.make_event(P.AGENT_CONFIRM, id="confirm", text="approve?", tainted=False))
    await first.publish("sid-owner", P.make_event(P.AGENT_REASONING, text="temporary"))
    recovered = stream(journal_for)
    assert await recovered.cursor("sid-owner") == 3
    observer = Observer()
    await recovered.replay("sid-owner", observer, 0)
    envelope = observer.events[-1]
    assert [e["type"] for e in envelope["items"]] == [P.AGENT_SAY]
    assert envelope["latest_seq"] == 3
    await recovered.attach("sid-owner", observer, 3)
    await recovered.publish("sid-owner", P.make_event(P.AGENT_DONE))
    assert observer.events[-1]["seq"] == 4


@pytest.mark.asyncio
async def test_failed_observer_and_failed_persistence_do_not_stop_delivery():
    class BrokenJournal:
        def load(self, limit):
            return []

        def append(self, seq, event):
            return False

    failures = []
    events = stream(lambda key: BrokenJournal(), failures=failures)
    healthy, broken = Observer(), Observer(fail=True)
    await events.attach("owner", healthy, 0)
    await events.attach("owner", broken, 0)
    await events.publish("owner", P.make_event(P.AGENT_DONE))
    assert events.subscribers["owner"] == {healthy}
    assert failures == [("owner", 1)]
    assert healthy.events[-1]["seq"] == 1
    await events.detach("owner", healthy)
    await events.publish("owner", P.make_event(P.AGENT_DONE), fallback=healthy)
    assert len(healthy.events) == 1
    assert await events.cursor("owner") == 2


@pytest.mark.asyncio
async def test_bounded_replay_reports_gap_and_does_not_record_envelope():
    events = stream(max_events=2)
    for i in range(4):
        await events.publish("owner", P.make_event(P.AGENT_SAY, text=str(i)))
    observer = Observer()
    await events.replay("owner", observer, "invalid", 1)
    envelope = observer.events[-1]
    assert [e["seq"] for e in envelope["items"]] == [3]
    assert envelope["cursor"] == 3
    assert envelope["truncated"] is True
    assert envelope["earliest_seq"] == 3
    assert envelope["latest_seq"] == 4
    await events.replay("owner", observer, 3)
    assert [e["seq"] for e in observer.events[-1]["items"]] == [4]
    assert await events.cursor("owner") == 4
