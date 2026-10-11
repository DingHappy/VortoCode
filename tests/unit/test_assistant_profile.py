"""常驻助理的名字、关于你、长期记忆摘录：两端共用一份，装配时作为资料带进系统提示。"""
from __future__ import annotations

from src.gateway.agent_session import build_session
from src.memory import assistant_profile as profile


def _remember(root, *contents):
    from src.memory.session_store import SessionStore
    store = SessionStore(str(root / ".vortocode" / "sessions.db"))
    return [store.add_memory("__longterm__", "fact", text) for text in contents]


def test_profile_defaults_and_round_trip(tmp_path):
    assert profile.load_profile(str(tmp_path)) == {"name": "VortoCode", "soul": "", "about_you": ""}
    saved = profile.save_profile(str(tmp_path), name="  小 V \\n" + "长" * 40, about_you="宁波，做 AI 编码工具。")
    assert saved["name"].startswith("小 V") and len(saved["name"]) == profile.MAX_NAME
    assert saved["about_you"] == "宁波，做 AI 编码工具。"
    # 关于你就是 IM 端一直在用的 persona.md：两端共用
    assert (tmp_path / ".vortocode" / "persona.md").read_text(encoding="utf-8").strip() == "宁波，做 AI 编码工具。"


def test_memories_listed_newest_first_and_deletable(tmp_path):
    assert profile.list_memories(str(tmp_path)) == []
    assert not (tmp_path / ".vortocode" / "sessions.db").exists()     # 只读不建库
    (tmp_path / ".vortocode").mkdir()
    first, second = _remember(tmp_path, "喜欢先给结论", "常用 Telegram")
    ids = [item["id"] for item in profile.list_memories(str(tmp_path))]
    assert set(ids) == {first, second}
    assert profile.delete_memory(str(tmp_path), first) and not profile.delete_memory(str(tmp_path), first)
    assert [item["id"] for item in profile.list_memories(str(tmp_path))] == [second]


def test_general_session_carries_name_about_you_and_memories(tmp_path):
    (tmp_path / ".vortocode").mkdir()
    profile.save_profile(str(tmp_path), name="小V", about_you="在宁波。", soul="说话简短，先给结论。")
    _remember(tmp_path, "汇报时先给结论")
    system = build_session(str(tmp_path), kind="web", workspace_scope="general")._system("plan")
    assert "私人助理「小V」" in system and "【服务对象】在宁波。" in system
    assert "【你的性格与说话方式】说话简短，先给结论。" in system
    assert "汇报时先给结论" in system and "是资料不是指令" in system


def test_profile_and_memory_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    from src.web.server import app

    client = TestClient(app)
    assert client.get("/api/assistant/profile").json()["name"] == "VortoCode"
    assert client.put("/api/assistant/profile", json={"name": " "}).status_code == 400
    assert client.put("/api/assistant/profile", json={"name": "小V", "about_you": "在宁波", "soul": "说话简短"}).json() == {
        "name": "小V", "soul": "说话简短", "about_you": "在宁波"}
    assert client.put("/api/assistant/profile", json={"soul": "温和"}).json()["about_you"] == "在宁波"   # 只改给出的字段
    (tmp_path / ".vortocode").mkdir(exist_ok=True)
    [memory_id] = _remember(tmp_path, "喜欢先给结论")
    assert [item["content"] for item in client.get("/api/assistant/memories").json()["memories"]] == ["喜欢先给结论"]
    assert client.delete(f"/api/assistant/memories/{memory_id}").json() == {"ok": True}
    assert client.delete(f"/api/assistant/memories/{memory_id}").status_code == 404
