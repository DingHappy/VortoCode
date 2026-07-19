"""项目上下文 / 记忆 路由（从 server.py 拆出；共享状态统一来自 src.web.state）。"""
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.agents.repo_memory import (MAX_REPO_MEMORY_CHARS, append_repo_memory,
                                    read_repo_memory, repo_memory_body)
from src.gateway.audit import record_event_audit
from src.memory.session_store import SessionStore
from src.memory.write_policy import (MemoryWritePolicy, MemoryWriteRequest, MemoryWriter,
                                     redact_secret_like)
from src.web.state import state

router = APIRouter()


class RepoMemoryWriteRequest(BaseModel):
    content: str
    confirm: bool = False


def _repo_memory_snapshot(repo_root: str) -> dict:
    raw = read_repo_memory(repo_root)
    safe_content, redactions = redact_secret_like(raw)
    effective, dropped = repo_memory_body(repo_root)
    safe_effective, effective_redactions = redact_secret_like(effective)
    entries = [line[2:].strip() for line in safe_content.splitlines() if line.startswith("- ")]
    total_entries = len(entries)
    injected_entries = max(0, total_entries - dropped) if dropped >= 0 else None
    return {
        "path": ".vortocode/memory/repo.md",
        "content": safe_content,
        "effective": safe_effective,
        "entries": entries,
        "total_entries": total_entries,
        "injected_entries": injected_entries,
        "dropped_entries": dropped,
        "truncated": dropped != 0,
        "max_chars": MAX_REPO_MEMORY_CHARS,
        "redacted": bool(redactions or effective_redactions),
    }


@router.get("/api/repo-memory")
async def get_repo_memory():
    """读取当前 runtime 对应仓库的安全记忆投影；疑似凭据不会返回给客户端。"""
    return _repo_memory_snapshot(os.getcwd())


@router.post("/api/repo-memory")
async def add_repo_memory(body: RepoMemoryWriteRequest):
    """显式追加仓库事实；每轮注入系统提示的内容必须经过高门槛策略。"""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="仓库记忆写入需要显式确认")
    decision = MemoryWritePolicy().evaluate(MemoryWriteRequest(
        content=body.content,
        source="desktop_repo_memory",
        session_id="desktop",
        tainted=False,
        write_method="http_post",
        memory_type="repo_fact",
    ))
    if decision.outcome != "durable":
        if decision.outcome == "quarantine":
            message = "疑似包含凭据，拒绝写入仓库记忆"
        elif "empty" in decision.reasons:
            message = "仓库记忆内容为空"
        elif "too_long" in decision.reasons:
            message = "仓库记忆内容过长"
        else:
            message = "内容不满足仓库记忆写入策略"
        raise HTTPException(status_code=422, detail={
            "status": decision.outcome,
            "message": message,
            "reasons": list(decision.reasons),
        })

    repo_root = os.getcwd()
    try:
        append_repo_memory(repo_root, decision.content)
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    record_event_audit(
        repo_root,
        session="desktop",
        mode="build",
        event="repo_memory_added",
        data={"chars": len(decision.content), "path": ".vortocode/memory/repo.md"},
    )
    return {
        **_repo_memory_snapshot(repo_root),
        "message": "已写入仓库记忆；下个新会话开始生效",
    }

# 项目上下文 API
@router.get("/api/context")
async def get_project_context():
    """获取项目上下文"""
    if state.project_context:
        return {
            "success": True,
            "instructions": state.project_context.instructions.model_dump() if state.project_context.instructions else None,
            "memory": state.project_context.memory.model_dump(),
            "prompt": state.project_context.get_context_prompt()
        }
    return {"success": False, "error": "Project context not loaded"}

@router.post("/api/context/memory")
async def add_memory(type: str, content: str):
    """显式 Web POST 写记忆；同样经过长期记忆策略，不允许旧路由绕过。"""
    if not state.project_context:
        return {"success": False, "error": "Project context not loaded"}
    writers = {
        "learning": state.project_context.add_learning,
        "pattern": state.project_context.add_pattern,
        "convention": state.project_context.add_convention,
        "debugging": state.project_context.add_debugging_tip,
    }
    project_writer = writers.get(type)
    if project_writer is None:
        return {"success": False, "error": f"Unknown memory type: {type}"}

    store = SessionStore(str(Path(state.workdir) / ".vortocode" / "sessions.db"))
    result = MemoryWriter(store).write(
        MemoryWriteRequest(
            content=content,
            source="web_context_api",
            session_id="web-context",
            write_method="http_post",
            memory_type=f"project_{type}",
        ),
        confirmed=True,  # 发出 POST 本身就是这个旧 API 的显式用户写动作
        confirmed_by="explicit_http_request",
    )
    if result.status != "stored":
        return {
            "success": False,
            "status": result.status,
            "proposal_id": result.record_id,
            "error": result.message,
        }

    # 保持旧 /api/context 返回结构；SQLite 记录提供跨端召回与 provenance 审计。
    project_writer(content)
    return {"success": True, "memory_id": result.record_id}
