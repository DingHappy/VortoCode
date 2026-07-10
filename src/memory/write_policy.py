"""长期记忆写入策略：确认、污点过滤、凭据脱敏、提案审阅与来源审计。

正常召回只读取 SessionStore.memories；不可信指令与疑似凭据进入独立
memory_proposals 表，因此不会在后续回合自动变成系统事实。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from src.memory.session_store import SessionStore

POLICY_VERSION = 1
MAX_MEMORY_CONTENT = 4_000


@dataclass(frozen=True)
class MemoryWriteRequest:
    content: str
    source: str
    session_id: Optional[str] = None
    tainted: bool = False
    write_method: str = "tool"
    memory_type: str = "fact"
    importance: float = 0.6
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryWriteDecision:
    outcome: str  # durable | proposal | quarantine | reject
    content: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryWriteResult:
    status: str
    decision: MemoryWriteDecision
    record_id: Optional[str] = None
    message: str = ""


_SECRET_RULES: tuple[tuple[str, re.Pattern[str], str | Callable[[re.Match[str]], str]], ...] = (
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        "bearer_token",
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
        "Bearer [REDACTED]",
    ),
    (
        "openai_style_key",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b"),
        "[REDACTED_API_KEY]",
    ),
    (
        "github_token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    (
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
        "[REDACTED_AWS_ACCESS_KEY]",
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "[REDACTED_JWT]",
    ),
    (
        "named_secret",
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd)"
            r"(\s*[:=]\s*)([^\s,;]{8,})"
        ),
        lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]",
    ),
)

_INSTRUCTION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "override_instructions",
        re.compile(
            r"(?is)\b(ignore|disregard|override|forget|bypass)\b.{0,60}"
            r"\b(previous|prior|system|developer|user|instruction|rule|policy)\b"
        ),
    ),
    ("role_override", re.compile(r"(?i)\b(you are now|act as|new system message)\b")),
    (
        "hidden_directive",
        re.compile(r"(?is)\b(do not|never)\b.{0,30}\b(tell|inform|show|reveal)\b.{0,30}\b(user|operator)\b"),
    ),
    ("prompt_boundary", re.compile(r"(?i)(<\/?system>|<\/?assistant>|tool_call|begin (system )?instructions?)")),
    ("system_prompt", re.compile(r"(?i)\b(system prompt|developer message)\b")),
    ("zh_override", re.compile(r"(忽略|无视|覆盖|绕过).{0,24}(之前|以上|系统|开发者|用户|指令|规则|策略)")),
    ("zh_role_override", re.compile(r"(你现在是|新的系统消息|切换角色|系统提示词)")),
    ("zh_hidden_directive", re.compile(r"(不要|不得).{0,20}(告诉|通知|展示|透露).{0,20}(用户|操作者)")),
)


def redact_secret_like(text: str) -> tuple[str, list[str]]:
    """返回脱敏文本和命中原因；原始凭据不会进入返回 metadata。"""
    redacted = str(text or "")
    reasons: list[str] = []
    for label, pattern, replacement in _SECRET_RULES:
        if pattern.search(redacted):
            reasons.append(label)
            redacted = pattern.sub(replacement, redacted)
    return redacted, list(dict.fromkeys(reasons))


def instruction_like_reasons(text: str) -> list[str]:
    reasons = [label for label, pattern in _INSTRUCTION_RULES if pattern.search(str(text or ""))]
    return list(dict.fromkeys(reasons))


def sanitize_persistent_summary(text: str, limit: int = 2_000) -> tuple[str, list[str]]:
    """持久化摘要的最后一道防线：脱敏并逐行移除显式提示注入指令。"""
    redacted, reasons = redact_secret_like(text)
    kept: list[str] = []
    instruction_reasons: list[str] = []
    marker_written = False
    for line in redacted.splitlines() or [redacted]:
        line_reasons = instruction_like_reasons(line)
        if line_reasons:
            instruction_reasons.extend(line_reasons)
            if not marker_written:
                kept.append("[指令性外部文本已由记忆策略过滤]")
                marker_written = True
            continue
        kept.append(line)
    reasons.extend(instruction_reasons)
    return "\n".join(kept).strip()[:limit], list(dict.fromkeys(reasons))


class MemoryWritePolicy:
    def evaluate(self, request: MemoryWriteRequest) -> MemoryWriteDecision:
        content = str(request.content or "").strip()
        if not content:
            return MemoryWriteDecision("reject", "", ("empty",))
        if len(content) > MAX_MEMORY_CONTENT:
            return MemoryWriteDecision("reject", "", ("too_long",))

        redacted, secret_reasons = redact_secret_like(content)
        if secret_reasons:
            return MemoryWriteDecision("quarantine", redacted, tuple(secret_reasons))

        instruction_reasons = instruction_like_reasons(content) if request.tainted else []
        if instruction_reasons:
            return MemoryWriteDecision("proposal", content, tuple(instruction_reasons))
        return MemoryWriteDecision("durable", content)


class MemoryWriter:
    """所有入口共享的写入服务；未确认时绝不落 durable/proposal 任一表。"""

    def __init__(self, store: SessionStore):
        self.store = store
        self.policy = MemoryWritePolicy()

    def write(self, request: MemoryWriteRequest, *, confirmed: bool,
              confirmed_by: str = "user") -> MemoryWriteResult:
        decision = self.policy.evaluate(request)
        if decision.outcome == "reject":
            reason = "内容为空" if "empty" in decision.reasons else f"内容超过 {MAX_MEMORY_CONTENT} 字符"
            return MemoryWriteResult("rejected", decision, message=reason)
        if not confirmed:
            return MemoryWriteResult("needs_confirmation", decision, message="记忆写入尚未确认")

        session_id = str(request.session_id or "unknown")[:160]
        source = str(request.source or "unknown")[:80]
        write_method = str(request.write_method or "unknown")[:80]
        metadata = dict(request.metadata or {})
        metadata.update({
            "source": source,
            "session_id": session_id,
            "tainted": bool(request.tainted),
            "write_method": write_method,
            "confirmed": True,
            "confirmed_by": str(confirmed_by or "user")[:80],
            "policy_version": POLICY_VERSION,
            "policy_decision": decision.outcome,
            "policy_reasons": list(decision.reasons),
        })

        if decision.outcome == "durable":
            memory_id = self.store.add_memory(
                "__longterm__", request.memory_type, decision.content,
                importance=request.importance, metadata=metadata,
            )
            return MemoryWriteResult("stored", decision, memory_id, "已保存长期记忆")

        status = "quarantined" if decision.outcome == "quarantine" else "pending"
        if decision.outcome == "quarantine":
            metadata["raw_content_persisted"] = False
            metadata["original_length"] = len(str(request.content or ""))
        proposal_id = self.store.add_memory_proposal(
            decision.content,
            decision=decision.outcome,
            reasons=list(decision.reasons),
            source=source,
            origin_session_id=session_id,
            tainted=request.tainted,
            write_method=write_method,
            memory_type=request.memory_type,
            importance=request.importance,
            status=status,
            metadata=metadata,
        )
        message = ("疑似凭据已脱敏并隔离，不能批准为长期记忆"
                   if status == "quarantined"
                   else "外部指令性内容已保存为待审提案，尚未进入长期记忆")
        return MemoryWriteResult(status, decision, proposal_id, message)

    def review(self, proposal_id: str, action: str, *, confirmed: bool,
               reviewer: str = "user", session_id: Optional[str] = None) -> dict[str, Any]:
        if not confirmed:
            return {"ok": False, "status": "needs_confirmation", "error": "提案审阅尚未确认"}
        return self.store.review_memory_proposal(
            proposal_id,
            action,
            reviewer=str(reviewer or "user")[:80],
            review_metadata={"session_id": str(session_id or "unknown")[:160], "confirmed": True},
        )


def new_origin_session(source: str) -> str:
    return f"{str(source or 'agent')[:40]}-{uuid.uuid4().hex[:8]}"


def confirmation_message(decision: MemoryWriteDecision, content: str) -> str:
    preview = " ".join(str(content or "").split())[:240]
    if decision.outcome == "proposal":
        return ("⚠ 本回合摄入过外部内容，候选记忆含指令性文本。只保存为待审提案、"
                f"不进入正常召回？\n  {preview}")
    if decision.outcome == "quarantine":
        return ("⚠ 候选记忆含疑似凭据。只保存脱敏隔离记录（原文不落盘、且不能批准）？\n"
                f"  {preview}")
    return f"把下面内容保存为跨会话长期记忆？\n  {preview}"
