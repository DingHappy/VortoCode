
import pytest

from fastapi import HTTPException

from src.web.routers.context import RepoMemoryWriteRequest, add_repo_memory, get_repo_memory


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
