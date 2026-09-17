import json

import pytest

from src.context.project_context import ProjectContext
from src.memory.session_store import SessionStore
from fastapi import HTTPException

from src.web.routers.context import (RepoMemoryWriteRequest, add_memory, add_repo_memory,
                                     get_repo_memory)
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
    raw_secret = "sk-" + "proj-" + "abcdefghijklmnopqrstuvwxyz123456"

    result = await add_memory("learning", f"api_key={raw_secret}")

    assert result["success"] is False
    assert result["status"] == "quarantined"
    assert not state.project_context.memory.learnings
    store = SessionStore(str(tmp_path / ".vortocode" / "sessions.db"))
    proposal = store.get_memory_proposal(result["proposal_id"])
    assert "REDACTED" in proposal["content"] and raw_secret not in proposal["content"]
    assert raw_secret.encode() not in (tmp_path / ".vortocode" / "sessions.db").read_bytes()


@pytest.mark.asyncio
async def test_desktop_repo_memory_requires_confirmation_and_uses_policy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    raw_secret = "sk-" + "proj-" + "abcdefghijklmnopqrstuvwxyz123456"

    with pytest.raises(HTTPException) as missing_confirmation:
        await add_repo_memory(RepoMemoryWriteRequest(content="测试命令是 pytest -q"))
    assert missing_confirmation.value.status_code == 400

    with pytest.raises(HTTPException) as secret:
        await add_repo_memory(RepoMemoryWriteRequest(
            content=f"api_key={raw_secret}",
            confirm=True,
        ))
    assert secret.value.status_code == 422
    assert not (tmp_path / ".vortocode" / "memory" / "repo.md").exists()


@pytest.mark.asyncio
async def test_desktop_repo_memory_returns_effective_redacted_projection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    raw_secret = "sk-" + "proj-" + "abcdefghijklmnopqrstuvwxyz123456"

    saved = await add_repo_memory(RepoMemoryWriteRequest(
        content="构建前必须运行 npm run codegen",
        confirm=True,
    ))
    assert saved["total_entries"] == 1
    assert saved["entries"] == ["构建前必须运行 npm run codegen"]
    assert saved["message"].endswith("下个新会话开始生效")
    assert "npm run codegen" in saved["effective"]

    path = tmp_path / ".vortocode" / "memory" / "repo.md"
    path.write_text(
        path.read_text(encoding="utf-8") + f"- api_key={raw_secret}\n",
        encoding="utf-8",
    )
    snapshot = await get_repo_memory()
    assert snapshot["redacted"] is True
    assert raw_secret not in snapshot["content"]
    assert "[REDACTED" in snapshot["content"]
    assert b"repo_memory_added" in (tmp_path / ".vortocode" / "audit.log").read_bytes()
