"""Bounded visible-turn text for supervision, never a source of authorization.

Display summaries remain short. This separate, versioned observation preserves
the available user messages and says explicitly when they are incomplete. It
does not claim to contain prior turns, images, or the whole Codex conversation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

MAX_MESSAGES = 8
MAX_CHARS = 4096
SCOPE = "turn_user_messages"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _status(messages: list, omitted: int, unsupported: bool) -> str:
    if omitted or unsupported or any(m["truncated"] for m in messages):
        return "incomplete"
    if not any(m["text"].strip() for m in messages):
        return "unknown"
    return "incomplete" if any(not m["text"].strip() for m in messages) else "complete"


def capture(turn_id: str, items: object) -> dict:
    """Capture only text in visible userMessage items, retaining order and whitespace."""
    source = items if isinstance(items, list) else []
    users = [item for item in source if isinstance(item, dict) and item.get("type") == "userMessage"]
    unsupported = any(not isinstance(item, dict) for item in source)
    messages, remaining = [], MAX_CHARS
    for item in users[:MAX_MESSAGES]:
        content = item.get("content")
        parts = content if isinstance(content, list) else []
        unsupported |= not isinstance(content, list)
        texts = []
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            else:
                unsupported = True
        original = "\n".join(texts)
        text = original[:remaining]
        remaining -= len(text)
        messages.append({"text": text, "original_chars": len(original), "truncated": len(text) < len(original)})
    omitted = len(users) - len(messages)
    result = {"schema_version": 1, "scope": SCOPE, "turn_id": turn_id,
              "status": _status(messages, omitted, unsupported), "messages": messages,
              "message_count": len(users), "omitted_messages": omitted, "unsupported_content": unsupported}
    return {**result, "fingerprint": _digest(result)}


def decode(raw: object, turn_id: str) -> dict | None:
    """Recheck claims and digest; invalid new observations never become legacy-complete."""
    if not isinstance(raw, dict):
        return None
    keys = {"schema_version", "scope", "turn_id", "status", "messages", "message_count",
            "omitted_messages", "unsupported_content", "fingerprint"}
    if not keys <= raw.keys():
        return None
    if (type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or raw["scope"] != SCOPE
            or not isinstance(turn_id, str) or not turn_id or len(turn_id) > 80 or raw["turn_id"] != turn_id
            or not isinstance(raw["messages"], list) or len(raw["messages"]) > MAX_MESSAGES
            or any(type(raw[k]) is not int or not 0 <= raw[k] <= 2**53 - 1 for k in ("message_count", "omitted_messages"))
            or type(raw["unsupported_content"]) is not bool
            or raw["omitted_messages"] != raw["message_count"] - len(raw["messages"])):
        return None
    messages = []
    for m in raw["messages"]:
        if (not isinstance(m, dict) or set(m) != {"text", "original_chars", "truncated"}
                or not isinstance(m["text"], str) or type(m["original_chars"]) is not int
                or not len(m["text"]) <= m["original_chars"] <= 2**53 - 1
                or type(m["truncated"]) is not bool or m["truncated"] != (len(m["text"]) < m["original_chars"])):
            return None
        messages.append(dict(m))
    if sum(len(m["text"]) for m in messages) > MAX_CHARS:
        return None
    if raw["status"] != _status(messages, raw["omitted_messages"], raw["unsupported_content"]):
        return None
    result = {key: raw[key] for key in keys - {"fingerprint"}}
    if raw["fingerprint"] != _digest(result):
        return None
    return {**result, "messages": messages, "fingerprint": raw["fingerprint"]}


@dataclass(frozen=True)
class ReviewContext:
    status: str
    fingerprint: str
    messages: tuple[str, ...] = ()
    reason: str = ""


def read(turn: dict) -> ReviewContext:
    context = decode(turn.get("review_context"), turn.get("id", ""))
    if context is None:
        reason = ("本轮报告缺少材料完整度信息，请更新观察员或核对原会话。" if "review_context" not in turn
                  else "本轮推进材料版本或记录无法核对，请核对原会话。")
        display = [str(turn.get(k) or "")[:limit] for k, limit in (("request", 500), ("reply", 1000), ("plan", 800))]
        return ReviewContext("unknown", _digest([turn.get("id"), reason, display]), reason=reason)
    reason = ("本轮推进材料有截断、遗漏或未支持的附件，请核对原会话和本次范围。" if context["status"] == "incomplete"
              else "本轮尚无可核对的用户要求，请核对原会话。" if context["status"] == "unknown" else "")
    return ReviewContext(context["status"], context["fingerprint"], tuple(m["text"] for m in context["messages"]), reason)


def blocked_reason(turn: dict) -> str:
    reason = read(turn).reason
    return f"已暂停自动拟下一步：{reason}" if reason else ""


def material(turn: dict) -> str:
    context = read(turn)
    return json.dumps({"scope": SCOPE, "status": context.status, "fingerprint": context.fingerprint,
                       "visible_user_messages": context.messages, "reason": context.reason}, ensure_ascii=False)
