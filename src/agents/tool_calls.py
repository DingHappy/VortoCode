"""Decode tool-call envelopes without repairing malformed arguments into actions.

This module knows no handlers, permissions, model client or transport. Batch
validation completes before the caller may dispatch any member of that batch.
"""
from __future__ import annotations

import json
import re
from typing import Any


class ToolCallError(ValueError):
    """Safe diagnostic: fixed codes/messages, never raw model arguments."""

    def __init__(self, code: str, detail: str, index: int | None = None):
        self.code = code
        self.index = index
        location = "调用批次" if index is None else f"第 {index} 项"
        super().__init__(f"{location} [{code}]：{detail}")


def argument_object(value: Any, index: int = 1) -> dict:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ToolCallError("invalid_arguments", "参数必须是 JSON 对象，不能替换为空参数执行。", index)
    return value


def _json_value(text: str, index: int | None = None) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ToolCallError("duplicate_field", "JSON 对象存在重复字段，请使用唯一字段。", index)
            result[key] = value
        return result

    def constant(_value):
        raise ToolCallError("invalid_json", "参数包含 JSON 不支持的数值。", index)

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except ToolCallError:
        raise
    except (ValueError, TypeError, RecursionError):
        raise ToolCallError("invalid_json", "调用必须是完整有效的 JSON。", index) from None


def _strip_fences(text: str) -> str:
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", body)
        body = re.sub(r"\s*```$", "", body)
    return body.strip()


def _first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def _named_call(value: Any, index: int, *, native: bool = False) -> tuple[str, dict]:
    if not isinstance(value, dict):
        raise ToolCallError("invalid_call", "批次中的每一项都必须是工具调用对象。", index)
    name = value.get("name" if native else "tool")
    if not isinstance(name, str) or not name.strip():
        raise ToolCallError("invalid_name", "工具名必须是非空字符串。", index)
    if native:
        args = value.get("arguments", {})
        if isinstance(args, str):
            args = _json_value(args, index)
    else:
        args = value.get("args", {})
    return name, argument_object(args, index)


def parse_tool_call(text: str, *, strict: bool = False) -> tuple[str, dict] | None:
    """Compatible single-call extraction; invalid calls never become empty args."""
    body = _strip_fences(text)
    if body.startswith("["):
        return None  # Never extract a valid-looking member from an array.
    candidate = body if body.startswith("{") and body.endswith("}") else _first_json_object(body)
    if not candidate:
        if strict and body.startswith("{"):
            raise ToolCallError("invalid_json", "调用必须是完整有效的 JSON。")
        return None
    try:
        value = _json_value(candidate)
        return _named_call(value, 1) if isinstance(value, dict) and "tool" in value else None
    except ToolCallError:
        if strict:
            raise
        return None


def parse_tool_calls(text: str, *, strict: bool = False) -> list[tuple[str, dict]] | None:
    """Decode an entire batch or reject it; ordinary prose/data returns None."""
    body = _strip_fences(text)
    if body.startswith("["):
        try:
            values = _json_value(body)
            if not isinstance(values, list) or not any(isinstance(value, dict) and "tool" in value for value in values):
                return None
            return [_named_call(value, index) for index, value in enumerate(values, 1)]
        except ToolCallError:
            if strict:
                raise
            return None
    call = parse_tool_call(text, strict=strict)
    return [call] if call is not None else None


def parse_native_tool_calls(values: Any) -> list[tuple[str, dict]]:
    """Accept the normalized LLM envelope, preserving omitted zero-arg calls."""
    if values is None:
        return []
    if not isinstance(values, list):
        raise ToolCallError("invalid_batch", "原生工具调用必须是列表。")
    return [_named_call(value, index, native=True) for index, value in enumerate(values, 1)]
