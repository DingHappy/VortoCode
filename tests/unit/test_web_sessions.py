"""Web 多会话管理（Phase2 功能）：session_store 列表/删除/重命名 + /api/agent/sessions 路由。

后端支撑 agent.html 的会话侧栏。前端交互需浏览器验证；这里把后端契约测死。
"""
import pytest
from fastapi.testclient import TestClient

from src.web import session_store as ss


def _mk(root, sid, transcript, title=None):
    ss.save_session(str(root), f"sid-{sid}", transcript, [], None, title=title)


# ---- session_store 单元 ----

def test_list_sessions_derives_title_and_counts(tmp_path):
    _mk(tmp_path, "aaa", [{"role": "user", "text": "帮我实现登录功能"},
                          {"role": "assistant", "text": "好"}])
    _mk(tmp_path, "bbb", [{"role": "user", "text": "另一个任务"}], title="自定义名")
    sessions = {s["sid"]: s for s in ss.list_sessions(str(tmp_path))}
    assert sessions["aaa"]["title"] == "帮我实现登录功能"      # 无显式标题 → 取首条 user
    assert sessions["aaa"]["messages"] == 2
    assert sessions["bbb"]["title"] == "自定义名"              # 显式标题优先


def test_list_sessions_empty_when_no_dir(tmp_path):
    assert ss.list_sessions(str(tmp_path)) == []


def test_save_preserves_title_across_saves(tmp_path):
    _mk(tmp_path, "ccc", [{"role": "user", "text": "x"}], title="我的对话")
    _mk(tmp_path, "ccc", [{"role": "user", "text": "x"}, {"role": "assistant", "text": "y"}])  # 不带 title
    got = {s["sid"]: s for s in ss.list_sessions(str(tmp_path))}["ccc"]
    assert got["title"] == "我的对话"                          # 每回合存盘不冲掉已改的名


def test_delete_and_rename(tmp_path):
    _mk(tmp_path, "ddd", [{"role": "user", "text": "hi"}])
    assert ss.rename_session(str(tmp_path), "ddd", "新名字") is True
    assert ss.load_session(str(tmp_path), "sid-ddd")["title"] == "新名字"
    assert ss.delete_session(str(tmp_path), "ddd") is True
    assert ss.load_session(str(tmp_path), "sid-ddd") is None
    assert ss.delete_session(str(tmp_path), "ddd") is False    # 幂等：再删返回 False


def test_sid_sanitized_blocks_traversal(tmp_path):
    assert ss.delete_session(str(tmp_path), "../../evil") is False   # 非法 sid 不落到任意路径
    assert ss.rename_session(str(tmp_path), "../x", "t") is False


# ---- API 路由 ----

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                  # 路由用 os.getcwd() 定位会话库
    from src.web.server import app
    return TestClient(app), tmp_path


def test_api_list_delete_rename(client):
    c, root = client
    _mk(root, "s1", [{"role": "user", "text": "任务一"}])
    _mk(root, "s2", [{"role": "user", "text": "任务二"}])

    lst = c.get("/api/agent/sessions").json()["sessions"]
    assert {s["sid"] for s in lst} == {"s1", "s2"}

    assert c.patch("/api/agent/sessions/s1", json={"title": "改名了"}).json()["ok"] is True
    lst = {s["sid"]: s for s in c.get("/api/agent/sessions").json()["sessions"]}
    assert lst["s1"]["title"] == "改名了"

    assert c.delete("/api/agent/sessions/s2").json()["deleted"] is True
    remaining = {s["sid"] for s in c.get("/api/agent/sessions").json()["sessions"]}
    assert remaining == {"s1"}


def test_api_rename_requires_title(client):
    c, root = client
    _mk(root, "s3", [{"role": "user", "text": "x"}])
    assert c.patch("/api/agent/sessions/s3", json={"title": "  "}).json()["ok"] is False
