"""examples/pet_state.py（hook 胶水）—— 事件 JSON → 状态文件，纯离线子进程测。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "examples" / "pet_state.py"


def _run(event: dict, cwd: Path):
    r = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(event),
                       capture_output=True, text=True, cwd=str(cwd), timeout=15)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("event_type,state", [
    ("agent_start", "working"),
    ("pre_tool_use", "tool"),
    ("post_tool_use", "tool"),
    ("tool_error", "error"),
    ("agent_end", "done"),
    ("something_else", "idle"),
])
def test_event_maps_to_state(tmp_path, event_type, state):
    out = _run({"event_type": event_type, "data": {"tool": "edit_file"}}, tmp_path)
    assert out["state"] == state
    # 工具事件带上工具名当 detail
    if state == "tool":
        assert out["detail"] == "edit_file"


def test_writes_state_file(tmp_path):
    _run({"event_type": "agent_start", "data": {}}, tmp_path)
    f = tmp_path / ".vortocode" / "pet_state.json"
    assert f.is_file()
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["state"] == "working" and data["emoji"]


def test_bad_input_safe(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT)], input="not json",
                       capture_output=True, text=True, cwd=str(tmp_path), timeout=15)
    assert r.returncode == 0                                  # 坏输入不崩
    assert json.loads(r.stdout)["state"] == "idle"
