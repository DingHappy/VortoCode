"""频道是原 sid 会话的展示元数据，不创建 Agent 或另一个消息库。"""
import pytest

from src.gateway.session_store import create_channel, load_session, save_session, list_sessions
from src.gateway.session_dashboard import session_dashboard_snapshot
from src.gateway.sessions import SessionTable


def test_channel_survives_empty_creation_restart_and_normal_session_persistence(tmp_path):
    root = str(tmp_path)
    created = create_channel(root, "资料研究", "持续跟进公开资料")
    key = f"sid-{created['sid']}"
    saved = load_session(root, key)
    assert saved["title"] == "资料研究"
    assert saved["channel"] == {"version": 1, "topic": "持续跟进公开资料"}
    assert saved["transcript"] == saved["history"] == []

    class Agent:
        history = []
        plan = []

    table = SessionTable()
    live = table.get(key, repo_root=root, factory=Agent)
    live["transcript"].append({"role": "user", "text": "只属于这个频道"})
    table.persist(key, root)
    assert load_session(root, key)["channel"] == saved["channel"]
    rows = session_dashboard_snapshot(root, live_sessions=table.data, is_busy=lambda _: False,
                                      pending_for=lambda _: [], running_for=lambda _: None)
    assert rows[0]["channel"] == saved["channel"]
    assert rows[0]["messages"] == 1
    assert SessionTable().get(key, repo_root=root, factory=Agent)["transcript"] == live["transcript"]


def test_channel_names_are_unique_without_promoting_ordinary_titles(tmp_path):
    root = str(tmp_path)
    assert save_session(root, "sid-normal", [], [], None, title="#资料研究")
    created = create_channel(root, "  #资料研究  ", "")
    with pytest.raises(ValueError, match="同名"):
        create_channel(root, "资料研究", "another")
    rows = list_sessions(root)
    assert len(rows) == 2
    assert "channel" not in next(row for row in rows if row["sid"] == "normal")
    assert next(row for row in rows if row["sid"] == created["sid"])["messages"] == 0


def test_channel_metadata_does_not_cross_roots_or_accept_permission_fields(tmp_path):
    one, two = str(tmp_path / "one"), str(tmp_path / "two")
    channel = create_channel(one, "资料研究", "公开资料")
    assert list_sessions(two) == []
    assert not save_session(one, "sid-forged", [], [], None, title="伪造", channel={
        "version": 1, "topic": "", "permissions": ["deliver"]})
    assert load_session(one, "sid-forged") is None
    assert load_session(one, f"sid-{channel['sid']}")["history"] == []


def test_legacy_unknown_channel_metadata_remains_readable_as_an_ordinary_chat(tmp_path):
    import json
    from src.utils.state_dir import state_dir
    root = str(tmp_path)
    assert save_session(root, "sid-legacy", [{"role": "user", "text": "保留内容"}], [], None)
    path = state_dir(root) / "web_sessions" / "legacy.json"
    data = json.loads(path.read_text())
    data["channel"] = {"version": 2, "topic": "未知版本", "permissions": ["deliver"]}
    path.write_text(json.dumps(data))
    loaded = load_session(root, "sid-legacy")
    assert loaded["transcript"] == data["transcript"]
    assert "channel" not in loaded
    assert "channel" not in list_sessions(root)[0]


@pytest.mark.parametrize("name,topic", [("", ""), ("#", ""), ("长" * 31, ""),
                                        ("name\nother", ""), ("name", "长" * 301), (42, "")])
def test_invalid_channel_is_not_saved(tmp_path, name, topic):
    with pytest.raises(ValueError):
        create_channel(str(tmp_path), name, topic)
    assert list_sessions(str(tmp_path)) == []
