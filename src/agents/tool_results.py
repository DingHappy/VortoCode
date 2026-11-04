"""Versioned text framing for tool results; no execution or transport ownership.

Bodies remain verbatim. Character lengths, rather than body text, locate the
next record. The visible tool headings remain for prompted models and history.
"""
from __future__ import annotations

import json
import re

RESULTS_PREFIX = "[工具 批次结果 v1]\n"
UNKNOWN_RESULT = "【工具结果缺失或无法逐项对应】本项结果未知，不能推断成功或未执行；涉及副作用时先核对，避免重复执行。"
_LEGACY_HEADER = re.compile(r"(?m)^\[工具 ([^\n]+?) 结果\]\n")


def _unique_fields(pairs):
    header = {}
    for key, value in pairs:
        if key in header:
            raise ValueError("duplicate result metadata")
        header[key] = value
    return header


def _heading(name: str) -> str:
    escaped = json.dumps(name, ensure_ascii=False)[1:-1]
    return f"[工具 {escaped} 结果]\n"


def encode_tool_results(results: list[tuple[str, str]]) -> str:
    records = [RESULTS_PREFIX]
    for name, value in results:
        text = str(value)
        header = json.dumps({"name": name, "chars": len(text)}, ensure_ascii=False, separators=(",", ":"))
        records.extend((header, "\n", _heading(name), text, "\n"))
    return "".join(records)


def _framed_results(text: str) -> list[tuple[str, str]] | None:
    parts = []
    position = len(RESULTS_PREFIX)
    while position < len(text):
        newline = text.find("\n", position)
        if newline < 0:
            return None
        try:
            header = json.loads(text[position:newline], object_pairs_hook=_unique_fields)
        except (ValueError, RecursionError):
            return None
        if not isinstance(header, dict) or set(header) != {"name", "chars"}:
            return None
        name, size = header["name"], header["chars"]
        if not isinstance(name, str) or type(size) is not int or size < 0:
            return None
        position = newline + 1
        heading = _heading(name)
        if not text.startswith(heading, position):
            return None
        position += len(heading)
        end = position + size
        if end >= len(text) or text[end] != "\n":
            return None
        parts.append((name, text[position:end]))
        position = end + 1
    return parts


def _legacy_results(text: str) -> list[tuple[str, str]] | None:
    headers = list(_LEGACY_HEADER.finditer(text))
    if not headers or headers[0].start() != 0:
        return None
    parts = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        body = text[header.end():end]
        if index + 1 < len(headers):
            if not body.endswith("\n\n"):
                return None
            body = body[:-2]  # Remove exactly the join separator, not the body's whitespace.
        parts.append((header.group(1), body))
    return parts


def decode_tool_results(text: str, *, names: list[str] | None = None,
                        count: int | None = None) -> list[tuple[str, str]] | None:
    """Read framed or legacy results, verifying ordered call identity if supplied.

    A malformed versioned record must never fall back to scanning its body for
    legacy headings. This framing detects mismatches; it is not authentication.
    """
    parts = _framed_results(text) if text.startswith(RESULTS_PREFIX) else _legacy_results(text)
    if parts is None or (count is not None and len(parts) != count):
        return None
    if names is not None and [name for name, _ in parts] != names:
        return None
    return parts
