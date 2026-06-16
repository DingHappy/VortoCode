"""结构化日志 / trace 测试。"""

import json
import logging
import sys

from src.core.tracing import (
    JsonFormatter, set_trace_id, get_trace_id, ensure_trace_id,
)


def test_json_formatter_includes_trace_id_and_message():
    set_trace_id("abc123")
    rec = logging.LogRecord("mylogger", logging.INFO, "f.py", 10, "hello %s", ("world",), None)
    d = json.loads(JsonFormatter().format(rec))
    assert d["msg"] == "hello world"
    assert d["level"] == "INFO"
    assert d["logger"] == "mylogger"
    assert d["trace_id"] == "abc123"


def test_set_and_get_trace_id():
    tid = set_trace_id()
    assert get_trace_id() == tid
    assert len(tid) >= 8


def test_ensure_trace_id_reuses_existing():
    set_trace_id("fixed-id")
    assert ensure_trace_id() == "fixed-id"


def test_json_formatter_captures_exception():
    set_trace_id("e1")
    try:
        raise ValueError("boom")
    except ValueError:
        rec = logging.LogRecord("l", logging.ERROR, "f", 1, "failed", (), sys.exc_info())
    d = json.loads(JsonFormatter().format(rec))
    assert "exc" in d and "ValueError" in d["exc"]
