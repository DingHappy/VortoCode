"""网页会话落盘持久化（src/web/session_store）—— 纯离线。"""

from src.web.session_store import _sid_of, load_session, save_session


def test_only_sid_sessions_persist(tmp_path):
    # ws-id 临时连接不落盘
    assert save_session(str(tmp_path), "ws-123", [{"role": "user", "text": "x"}], [], None) is False
    assert load_session(str(tmp_path), "ws-123") is None


def test_round_trip(tmp_path):
    tr = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "yo"}]
    hist = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
    plan = [{"step": "a", "status": "completed"}]
    assert save_session(str(tmp_path), "sid-abc", tr, hist, plan) is True
    got = load_session(str(tmp_path), "sid-abc")
    assert got["transcript"] == tr and got["history"] == hist and got["plan"] == plan


def test_load_missing(tmp_path):
    assert load_session(str(tmp_path), "sid-nope") is None


def test_sid_sanitized_no_traversal(tmp_path):
    sid = _sid_of("sid-../../etc/passwd")
    assert sid and "/" not in sid and ".." not in sid
    # 清洗后仍落在 web_sessions 目录内，不越界
    assert save_session(str(tmp_path), "sid-../../evil", [], [{"role": "user", "content": "x"}], None) is True
    assert not (tmp_path.parent / "evil.json").exists()
    assert (tmp_path / ".vortocode" / "web_sessions").is_dir()


def test_history_and_transcript_capped(tmp_path):
    hist = [{"role": "user", "content": str(i)} for i in range(100)]
    tr = [{"role": "user", "text": str(i)} for i in range(500)]
    save_session(str(tmp_path), "sid-cap", tr, hist, None)
    got = load_session(str(tmp_path), "sid-cap")
    assert len(got["history"]) == 40 and got["history"][-1]["content"] == "99"   # 留尾部
    assert len(got["transcript"]) == 200 and got["transcript"][-1]["text"] == "499"
