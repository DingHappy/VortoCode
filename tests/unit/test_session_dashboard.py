"""会话 Dashboard 快照在注入的实时状态上排序：要人处理的在前，别的项目的会话不混进来。"""
import time

from src.gateway.session_dashboard import session_dashboard_snapshot


def _live(root, text):
    return {"repo_root": str(root), "transcript": [{"role": "user", "text": text}],
            "updated": time.time(), "agent": None}


def test_live_sessions_sorted_by_attention_and_scoped_to_repo(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    live = {"sid-busy": _live(tmp_path, "跑测试"), "sid-ask": _live(tmp_path, "要删吗"),
            "sid-idle": _live(tmp_path, "你好"), "sid-elsewhere": _live(other, "别的项目"),
            "im-chat": _live(tmp_path, "非 Web 会话")}
    rows = session_dashboard_snapshot(
        str(tmp_path), live_sessions=live,
        is_busy=lambda key: key == "sid-busy",
        pending_for=lambda key: ["确认？"] if key == "sid-ask" else [],
        running_for=lambda key: None)
    assert [(r["sid"], r["status"]) for r in rows] == [
        ("ask", "needs_input"), ("busy", "working"), ("idle", "idle")]
    assert rows[0]["pending_input_count"] == 1
