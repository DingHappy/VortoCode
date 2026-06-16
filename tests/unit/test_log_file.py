"""结构化日志落盘测试。"""

import json
import logging

from src.core.tracing import setup_structured_logging, set_trace_id


def test_setup_logging_writes_json_to_file(tmp_path):
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        f = tmp_path / "logs" / "app.log"
        setup_structured_logging(log_file=str(f), to_console=False)
        set_trace_id("tid42")
        logging.getLogger("test.logger").info("hello file %d", 7)
        for h in root.handlers:
            h.flush()

        assert f.exists()
        line = f.read_text(encoding="utf-8").strip().splitlines()[-1]
        d = json.loads(line)
        assert d["msg"] == "hello file 7"
        assert d["trace_id"] == "tid42"
        assert d["logger"] == "test.logger"
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)
