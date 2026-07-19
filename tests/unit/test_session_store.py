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
    activities = [{"type": "agent_phase", "id": "r1:thinking", "status": "completed"}]
    prompt_queue = [{"id": "q1", "text": "下一步", "mode": "plan"}]
    context_usage = {"used_tokens": 1200, "max_tokens": 8000, "pct": 15}
    assert save_session(str(tmp_path), "sid-abc", tr, hist, plan, activities=activities,
                        prompt_queue=prompt_queue, context_usage=context_usage) is True
    got = load_session(str(tmp_path), "sid-abc")
    assert got["transcript"] == tr and got["history"] == hist and got["plan"] == plan
    assert got["activities"] == activities
    assert got["prompt_queue"] == prompt_queue
    assert got["context_usage"] == context_usage


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


def test_session_table_restores_prompt_queue(tmp_path):
    from src.gateway.sessions import SessionTable

    class Agent:
        def __init__(self):
            self.history = []
            self.plan = []

    queued = [{"id": "q1", "text": "重启后继续", "mode": "build", "version": 0}]
    assert save_session(str(tmp_path), "sid-resume", [], [], None, prompt_queue=queued)
    session = SessionTable().get("sid-resume", repo_root=str(tmp_path), factory=Agent)
    assert session["prompt_queue"] == queued


def test_session_table_persists_dashboard_context_snapshot(tmp_path):
    from src.gateway.sessions import SessionTable

    class Agent:
        def __init__(self):
            self.history = []
            self.plan = []
            self._context_mode = "build"

        def context_usage(self, mode):
            assert mode == "build"
            return {"used_tokens": 3000, "max_context_tokens": 12000, "pct": 25,
                    "history_messages": 8, "policy": "preserve", "will_compact": False}

    table = SessionTable()
    table.get("sid-context", repo_root=str(tmp_path), factory=Agent)
    table.persist("sid-context", str(tmp_path))

    saved = load_session(str(tmp_path), "sid-context")
    assert saved["context_usage"]["pct"] == 25
    assert saved["context_usage"]["max_tokens"] == 12000
