"""Interactive PTY behavior used by Desktop."""
import os
import time

import pytest

from src.gateway.terminals import TerminalManager


pytestmark = pytest.mark.skipif(os.name != "posix", reason="PTY is POSIX-only")


def test_terminal_accepts_input_streams_output_and_resizes(tmp_path):
    manager = TerminalManager(str(tmp_path))
    created = manager.create(cols=90, rows=24)
    terminal_id = created["id"]
    offset = created["offset"]

    manager.write(terminal_id, "printf '__VORTOCODE_PTY_OK__\\n'\n")
    output = ""
    for _ in range(100):
        snapshot = manager.read(terminal_id, offset)
        assert snapshot is not None
        output += snapshot["output"]
        offset = snapshot["offset"]
        if "__VORTOCODE_PTY_OK__" in output:
            break
        time.sleep(0.02)

    assert "__VORTOCODE_PTY_OK__" in output
    resized = manager.resize(terminal_id, 120, 40)
    assert resized is not None and resized["cols"] == 120 and resized["rows"] == 40

    stopped = manager.stop(terminal_id)
    assert stopped is not None and stopped["status"] == "exited"
    manager.shutdown()


def test_terminal_ids_and_limits_fail_closed(tmp_path):
    manager = TerminalManager(str(tmp_path))
    assert manager.get("../../bad") is None
    assert manager.read("term-missing") is None
    created = manager.create(cols=1, rows=999)
    assert created["cols"] == 20 and created["rows"] == 200
    manager.shutdown()
