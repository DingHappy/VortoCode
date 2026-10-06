"""Bounded question records inside the existing delegation contract.

These helpers validate/mutate a loaded record. CollaborationService owns the
shared lock, persistence and notifications; dispatch owns rescheduling.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from src.gateway.audit import _clip_text
from src.utils.ids import typed_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _conflict(message: str):
    from src.gateway.collaboration import CollaborationConflict
    return CollaborationConflict(message)


def _text(value: object, limit: int, label: str, *, optional: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not optional and not value.strip()):
        raise _conflict(f"{label}必须为{'0' if optional else '1'} 到 {limit} 字符的文本")
    return _clip_text(value.strip(), limit)


def _expired(question: dict) -> bool:
    try:
        deadline = datetime.fromisoformat(question["expires_at"])
        return deadline.utcoffset() is None or deadline <= _now()
    except (KeyError, TypeError, ValueError):
        return True


def question_views(task) -> list[dict]:
    """Expiry is projected on read, without a timer or read-side disk writes."""
    state = task.development if task.kind in {"dev", "dev-resume"} else task.collaboration
    questions = state.get("questions", []) if isinstance(state, dict) else []
    if not isinstance(questions, list):
        return []
    return [{**q, "status": "expired" if q.get("status") == "open" and _expired(q) else q.get("status")}
            for q in questions if isinstance(q, dict) and q.get("task_id") == task.id]


def new_question(task_id: str, round_number: int, asker: str, question: str,
                 options: list[str], context: str) -> dict:
    """Shared bounded payload; the caller validates execution/ownership scope."""
    question = _text(question, 2000, "问题")
    context = _text(context, 4000, "已完成的调查与缺失信息", optional=True)
    if not isinstance(options, list) or len(options) > 3:
        raise _conflict("建议选项最多 3 项")
    options = [_text(option, 200, "建议选项") for option in options]
    now = _now()
    return {"id": "question-" + uuid.uuid4().hex[:16], "task_id": task_id, "round": round_number,
            "status": "open", "question": question, "options": options, "context": context,
            "asked_by": asker, "answerer": "owner", "created": now.isoformat(),
            "expires_at": (now + timedelta(hours=24)).isoformat(), "answer": "", "answered_at": ""}


def ask(task, question: str, options: list[str], context: str, *, tainted: bool = False) -> dict:
    state = task.collaboration
    if (task.kind != "delegation" or state.get("dispatch", {}).get("source") != "api"
            or task.status != "running" or type(state.get("round")) is not int):
        raise _conflict("只有执行中的服务研究任务可以提问")
    if not 1 <= state["round"] < 3:
        raise _conflict("剩余执行轮次不足，不能创建需要再次执行的问题")
    item = new_question(task.id, state["round"], state["assignee"], question, options, context)
    previous = state.get("questions", [])
    if (not isinstance(previous, list) or len(previous) >= 2
            or any(not isinstance(q, dict) or q.get("status") == "open" for q in previous)):
        raise _conflict("问题记录无效或已有待回答问题")
    state["questions"] = [*previous, item]
    state["tainted"] = state.get("tainted", False) or tainted
    state["review"] = "not_submitted"
    task.status, task.result, task.error = "blocked", "", ""
    return item


def validate_answer(task, round_number: int, question_id: str, answer: str) -> tuple[dict, bool]:
    _text(answer, 2000, "回答")
    if type(round_number) is not int or not 1 <= round_number < 3 or not typed_id(question_id, "question"):
        raise _conflict("必须提供问题 ID 和提问时的精确轮次")
    state = task.collaboration
    if task.kind != "delegation" or state.get("dispatch", {}).get("source") != "api":
        raise _conflict("该任务不支持服务问答")
    questions = state.get("questions", [])
    if not isinstance(questions, list):
        raise _conflict("问题记录无效")
    matches = [q for q in questions if isinstance(q, dict) and q.get("id") == question_id]
    if len(matches) != 1:
        raise _conflict("问题不存在或已变化，请刷新任务")
    item = matches[0]
    if (item.get("task_id") != task.id or type(item.get("round")) is not int
            or item["round"] != round_number or item.get("answerer") != "owner"):
        raise _conflict("问题身份或轮次已变化，请刷新任务")
    fingerprint = hashlib.sha256(answer.strip().encode()).hexdigest()
    if (item.get("status") == "answered" and item.get("answer_fingerprint") == fingerprint
            and type(item.get("resumed_round")) is int and item["resumed_round"] == round_number + 1
            and type(state.get("round")) is int and state["round"] >= item["resumed_round"]):
        return item, True  # A replay returns current state, never schedules work.
    if (task.status != "blocked" or type(state.get("round")) is not int or state["round"] != round_number
            or item.get("status") != "open" or _expired(item)):
        raise _conflict("问题已回答、取消、过期或轮次已变化，请刷新任务")
    return item, False


def answer(task, round_number: int, question_id: str, text: str) -> bool:
    item, replayed = validate_answer(task, round_number, question_id, text)
    if replayed:
        return True
    item.update(status="answered", answer=_text(text, 2000, "回答"), answered_at=_now().isoformat(),
                answer_fingerprint=hashlib.sha256(text.strip().encode()).hexdigest(), resumed_round=round_number + 1)
    task.collaboration["round"] = round_number + 1
    task.collaboration["review"] = "not_submitted"
    task.status, task.result, task.error = "queued", "", ""
    return False


def cancel_open_questions(task) -> None:
    state = task.development if task.kind in {"dev", "dev-resume"} else task.collaboration
    for question in state.get("questions", []):
        if isinstance(question, dict) and question.get("status") == "open":
            question["status"] = "expired" if _expired(question) else "cancelled"
