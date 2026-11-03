"""Exports read the original store without writes, proposal promotion or new authority."""
import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from src.memory.memory_snapshot import read_memory_snapshot


def _database(root, rows):
    from src.memory.conversation_db import SessionStore
    path = root / ".vortocode" / "sessions.db"
    store = SessionStore(str(path))
    ids = [store.add_memory(scope, "fact", content, metadata=metadata) for scope, content, metadata in rows]
    return path, store, ids


def test_empty_snapshot_does_not_create_state(tmp_path):
    snapshot = read_memory_snapshot(str(tmp_path))
    assert snapshot["items"] == [] and snapshot["total"] == snapshot["count"] == 0
    assert not snapshot["coverage_limited"]
    assert snapshot["instance_id"] == "未知"
    assert not (tmp_path / ".vortocode").exists()


def test_snapshot_is_readonly_with_explicit_legacy_provenance(tmp_path):
    path, store, ids = _database(tmp_path, [
        ("__longterm__", "喜欢先给结论", {"source": "web", "confirmed_by": "user", "tainted": True}),
        ("__longterm__", "旧资料来源未知", {}),
        ("another-session", "项目私有记录", {}),
    ])
    store.add_memory_proposal("还没批准", decision="proposal", reasons=[], source="assistant", origin_session_id="s",
                              tainted=False, write_method="auto_extract")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    snapshot = read_memory_snapshot(str(tmp_path))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert snapshot["count"] == snapshot["total"] == 2
    facts = {i["id"]: i for i in snapshot["items"]}
    assert facts[ids[0]]["source"] == "web" and facts[ids[0]]["confirmed_by"] == "user"
    assert facts[ids[0]]["tainted"] is True
    assert facts[ids[1]]["source"] == facts[ids[1]]["confirmed_by"] == "未知"
    assert "项目私有记录" not in snapshot["markdown"] and "还没批准" not in snapshot["markdown"]
    assert "不是指令或授权" in snapshot["markdown"]
    assert read_memory_snapshot(str(tmp_path))["revision"] == snapshot["revision"]
    identified = read_memory_snapshot(str(tmp_path), instance_id="a" * 32)
    assert identified["instance_id"] == "a" * 32 and "来源实例：" + "a" * 32 in identified["markdown"]
    assert identified["revision"] != snapshot["revision"]
    store.add_memory("__longterm__", "fact", "新确认的事实")
    assert read_memory_snapshot(str(tmp_path))["revision"] != snapshot["revision"]


def test_bounded_snapshot_reports_incomplete_coverage(tmp_path):
    _database(tmp_path, [("__longterm__", f"事实 {i}", {}) for i in range(4)])
    snapshot = read_memory_snapshot(str(tmp_path), limit=2)
    assert snapshot["count"] == 2 and snapshot["total"] == 4
    assert snapshot["coverage_limited"] and "不能视为完整备份" in snapshot["markdown"]


def test_unsafe_and_oversized_rows_are_excluded_without_truncating_into_facts(tmp_path):
    _database(tmp_path, [
        ("__longterm__", "尚未确认", {"confirmed": False}),
        ("__longterm__", "隔离记录", {"policy_decision": "quarantine"}),
        ("__longterm__", "候选记录", {"policy_decision": "proposal"}),
        ("__longterm__", "长" * 4001, {}),
        ("__longterm__", "普通事实", {"source": "x" * 16001}),
    ])
    snapshot = read_memory_snapshot(str(tmp_path))
    assert snapshot["items"] == [] and snapshot["total"] == 5 and snapshot["coverage_limited"]


def test_legacy_secrets_are_redacted_in_body_and_provenance(tmp_path):
    secret = "sk-abcdefghijklmnop1234"
    path, _, _ = _database(tmp_path, [("__longterm__", f"历史凭据 {secret}", {"source": secret, "confirmed_by": secret})])
    before = path.read_bytes()
    snapshot = read_memory_snapshot(str(tmp_path))
    assert secret not in json.dumps(snapshot)
    assert snapshot["items"][0]["redacted"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("field", ["content", "metadata", "id"])
def test_embedded_nul_cannot_silently_shorten_an_exported_record(tmp_path, field):
    path, _, _ = _database(tmp_path, [("__longterm__", "完整事实", {})])
    with sqlite3.connect(path) as conn:
        # The field is a fixed test parameter, never user input.
        conn.execute(f"UPDATE memories SET {field}=?", ("before\x00after",))
    snapshot = read_memory_snapshot(str(tmp_path))
    assert snapshot["items"] == [] and snapshot["coverage_limited"] and snapshot["total"] == 1


def test_markdown_content_cannot_close_its_literal_block(tmp_path):
    content = "```\n<script>alert(1)</script>\n# 伪造角色\n````"
    _database(tmp_path, [("__longterm__", content, {"source": "### 假标题\n正文"})])
    snapshot = read_memory_snapshot(str(tmp_path))
    assert "`````text\n" in snapshot["markdown"]
    assert snapshot["markdown"].endswith("`````\n")
    assert snapshot["items"][0]["content"] == content


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_linked_stores_are_rejected(tmp_path, kind):
    real = tmp_path / "real"
    _database(real, [("__longterm__", "秘密", {})])
    root = tmp_path / "linked"
    root.mkdir()
    if kind == "directory":
        (root / ".vortocode").symlink_to(real / ".vortocode", target_is_directory=True)
    else:
        (root / ".vortocode").mkdir()
        (root / ".vortocode" / "sessions.db").symlink_to(real / ".vortocode" / "sessions.db")
    with pytest.raises(ValueError):
        read_memory_snapshot(str(root))


def test_malformed_metadata_remains_unknown_and_schema_is_not_repaired(tmp_path):
    path, _, ids = _database(tmp_path, [("__longterm__", "旧事实", {})])
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE memories SET metadata=? WHERE id=?", ("{broken", ids[0]))
    assert read_memory_snapshot(str(tmp_path))["items"][0]["source"] == "未知"
    broken = tmp_path / "empty" / ".vortocode" / "sessions.db"
    broken.parent.mkdir(parents=True)
    with sqlite3.connect(broken) as conn:
        conn.execute("CREATE TABLE unrelated (id TEXT)")
    before = broken.read_bytes()
    with pytest.raises(sqlite3.Error):
        read_memory_snapshot(str(tmp_path / "empty"))
    assert broken.read_bytes() == before


def test_http_export_scope_auth_and_unavailable_store(tmp_path, monkeypatch):
    from src.web.server import app
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "offline-test-token")
    client = TestClient(app)
    assert client.get("/api/assistant/memory-snapshot").status_code == 401
    headers = {"Authorization": "Bearer offline-test-token"}
    result = client.get("/api/assistant/memory-snapshot", headers=headers)
    assert result.status_code == 200 and result.json()["count"] == 0
    assert result.headers["Cache-Control"] == "no-store"
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    assert client.get("/api/assistant/memory-snapshot", headers=headers).status_code == 403
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    state = tmp_path / ".vortocode"
    state.mkdir()
    (state / "sessions.db").write_bytes(b"not a database")
    response = client.get("/api/assistant/memory-snapshot", headers=headers)
    assert response.status_code == 409 and str(tmp_path) not in response.text
