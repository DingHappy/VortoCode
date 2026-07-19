import json

from src.gateway.session_events import SessionEventJournal


def test_session_event_journal_round_trip_and_sequence_order(tmp_path):
    journal = SessionEventJournal(str(tmp_path), "sid-desktop-1")
    assert journal.append(2, {"type": "agent_done", "seq": 2})
    assert journal.append(1, {"type": "agent_say", "text": "hello", "seq": 1})
    assert journal.load() == [
        (1, {"type": "agent_say", "text": "hello", "seq": 1}),
        (2, {"type": "agent_done", "seq": 2}),
    ]
    ignore = (tmp_path / ".vortocode" / ".gitignore").read_text(encoding="utf-8")
    assert "session_events/" in ignore


def test_session_event_journal_ignores_torn_and_invalid_lines(tmp_path):
    journal = SessionEventJournal(str(tmp_path), "sid-safe")
    assert journal.append(1, {"type": "agent_say", "text": "ok", "seq": 1})
    segment = next((tmp_path / ".vortocode" / "session_events" / "safe").glob("*.jsonl"))
    with segment.open("a", encoding="utf-8") as handle:
        handle.write('{"seq":2,"event":')
        handle.write("\n" + json.dumps({"seq": "bad", "event": {"type": "agent_done"}}) + "\n")
    assert journal.load() == [(1, {"type": "agent_say", "text": "ok", "seq": 1})]


def test_session_event_journal_is_sid_only_bounded_and_clearable(tmp_path):
    assert SessionEventJournal(str(tmp_path), "ws-123").append(1, {"type": "agent_done"}) is False
    journal = SessionEventJournal(str(tmp_path), "sid-bounded")
    for seq in range(1, 8):
        assert journal.append(seq, {"type": "agent_say", "text": str(seq), "seq": seq})
    assert [seq for seq, _event in journal.load(limit=3)] == [5, 6, 7]
    assert journal.clear() is True
    assert journal.load() == []
