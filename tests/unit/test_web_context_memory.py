import json

import pytest

from src.context.project_context import ProjectContext
from src.memory.session_store import SessionStore
from src.web.routers.context import add_memory
from src.web.state import state


@pytest.mark.asyncio
async def test_web_context_memory_uses_shared_policy_and_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "workdir", str(tmp_path))
    monkeypatch.setattr(state, "project_context", ProjectContext(str(tmp_path)))

    result = await add_memory("learning", "测试命令是 pytest -q")

    assert result["success"] is True
    assert "测试命令是 pytest -q" in state.project_context.memory.learnings
    row = SessionStore(str(tmp_path / ".vortocode" / "sessions.db")).get_memories(
        "__longterm__"
    )[0]
    metadata = json.loads(row["metadata"])
    assert metadata["source"] == "web_context_api"
    assert metadata["write_method"] == "http_post"
    assert metadata["confirmed_by"] == "explicit_http_request"


@pytest.mark.asyncio
async def test_web_context_memory_quarantines_secret_without_project_write(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "workdir", str(tmp_path))
    monkeypatch.setattr(state, "project_context", ProjectContext(str(tmp_path)))
    raw_secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"

    result = await add_memory("learning", f"api_key={raw_secret}")

    assert result["success"] is False
    assert result["status"] == "quarantined"
    assert not state.project_context.memory.learnings
    store = SessionStore(str(tmp_path / ".vortocode" / "sessions.db"))
    proposal = store.get_memory_proposal(result["proposal_id"])
    assert "REDACTED" in proposal["content"] and raw_secret not in proposal["content"]
    assert raw_secret.encode() not in (tmp_path / ".vortocode" / "sessions.db").read_bytes()
