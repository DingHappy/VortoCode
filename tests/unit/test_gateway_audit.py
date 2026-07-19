import json

from src.gateway.audit import (
    list_audit,
    record_decision_audit,
    record_event_audit,
    record_tool_audit,
)


def test_audit_redacts_secrets_and_large_content(tmp_path):
    assert record_tool_audit(
        str(tmp_path),
        session="sid-desktop",
        mode="build",
        name="write_file",
        args={
            "path": "src/app.py",
            "content": "very private source",
            "api_key": "sk-raw-secret",
            "command": "OPENAI_API_KEY=sk-another python app.py --authorization Bearer abc.def",
        },
        result="full tool output must not be persisted",
    )
    raw = (tmp_path / ".vortocode" / "audit.log").read_text(encoding="utf-8")
    assert "sk-raw-secret" not in raw
    assert "sk-another" not in raw
    assert "abc.def" not in raw
    assert "very private source" not in raw
    assert "full tool output" not in raw

    entry = list_audit(str(tmp_path))[0]
    assert entry["category"] == "tool"
    assert entry["args"]["api_key"] == "[REDACTED]"
    assert entry["args"]["content"] == "<19 chars>"
    assert entry["result_len"] == len("full tool output must not be persisted")


def test_audit_tolerates_bad_and_resanitizes_legacy_lines(tmp_path):
    path = tmp_path / ".vortocode" / "audit.log"
    path.parent.mkdir(parents=True)
    legacy = {
        "ts": "2026-07-15T00:00:00+00:00",
        "session": "legacy",
        "mode": "build",
        "tool": "shell",
        "args": {"password": "raw", "command": "TOKEN=unsafe pytest"},
        "result_len": 12,
    }
    path.write_text("not-json\n" + json.dumps(legacy) + "\n", encoding="utf-8")
    entries = list_audit(str(tmp_path), 10)
    assert len(entries) == 1
    assert entries[0]["id"].startswith("legacy-")
    assert entries[0]["args"]["password"] == "[REDACTED]"
    assert "unsafe" not in entries[0]["args"]["command"]


def test_decision_and_event_share_the_audit_timeline(tmp_path):
    record_event_audit(
        str(tmp_path), session="tui-1", mode="build", event="verify",
        data={"ok": False, "body": "long evidence"},
    )
    record_decision_audit(
        str(tmp_path), session="sid-1", mode="build",
        operation="执行 git push", decision=False, tainted=True,
    )
    entries = list_audit(str(tmp_path), 10)
    assert [entry["category"] for entry in entries] == ["decision", "event"]
    assert entries[0]["decision"] == "denied" and entries[0]["tainted"] is True
    assert entries[1]["data"]["body"] == "<13 chars>"
