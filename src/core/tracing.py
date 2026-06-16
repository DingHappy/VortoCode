"""结构化（JSON）日志 + trace 关联 id。

- trace_id 用 contextvar 维护，一次运行（orchestrate / dev_loop / Web 执行）共享一个 id，
  写进每条日志，便于把一次运行的日志聚合/追踪。
- setup_structured_logging() 为 opt-in：调用后根 logger 输出 JSON 行（含 trace_id）。
  不调用则不改变默认日志行为（避免影响测试/库使用方）。
"""

import contextvars
import json
import logging
import time
import uuid

_trace_id: contextvars.ContextVar = contextvars.ContextVar("trace_id", default="")


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def set_trace_id(tid: str = None) -> str:
    tid = tid or new_trace_id()
    _trace_id.set(tid)
    return tid


def get_trace_id() -> str:
    return _trace_id.get()


def ensure_trace_id() -> str:
    """没有则新建一个 trace_id（外层已设则复用，使嵌套调用同 id）。"""
    return _trace_id.get() or set_trace_id()


class JsonFormatter(logging.Formatter):
    """把日志记录格式化为单行 JSON（含 trace_id）。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        tid = _trace_id.get()
        if tid:
            payload["trace_id"] = tid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_structured_logging(level: int = logging.INFO, log_file: str = None,
                             to_console: bool = True) -> None:
    """opt-in：让根 logger 以 JSON 行输出（控制台 +/或 落盘）。

    log_file 给定时，用 RotatingFileHandler 写 JSON 行——可直接被 Filebeat/ELK 摄取。
    """
    handlers = []
    if to_console:
        ch = logging.StreamHandler()
        ch.setFormatter(JsonFormatter())
        handlers.append(ch)
    if log_file:
        from logging.handlers import RotatingFileHandler
        from pathlib import Path
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(JsonFormatter())
        handlers.append(fh)
    root = logging.getLogger()
    if handlers:
        root.handlers = handlers
    root.setLevel(level)
