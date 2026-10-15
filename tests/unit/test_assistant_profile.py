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


def test_general_system_keeps_profile_but_does_not_freeze_memory_contents(tmp_path):
    (tmp_path / ".vortocode").mkdir()
    profile.save_profile(str(tmp_path), name="小V", about_you="在宁波。", soul="说话简短，先给结论。")
    _remember(tmp_path, "汇报时先给结论")
    system = build_session(str(tmp_path), kind="web", workspace_scope="general")._system("plan")
    assert "私人助理「小V」" in system and "【服务对象】在宁波。" in system
    assert "【你的性格与说话方式】说话简短，先给结论。" in system
    assert "汇报时先给结论" not in system and "记忆使用" in system
    assert "用户当下的要求优先" in system


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


def test_auto_memory_candidates_only_ever_become_proposals(tmp_path):
    """聊天里提炼的候选一律进待确认（不是你让它记的）；重复跳过；疑似凭据隔离、不可批准；批准后才生效。"""
    root = str(tmp_path)
    (tmp_path / ".vortocode").mkdir()
    _remember(tmp_path, "住在宁波")
    ids = profile.propose_memories(root, ["住在宁波", "汇报喜欢先给结论", "汇报喜欢先给结论",
                                          "我的密钥是 sk-abcdefghijklmnop1234"], source="assistant", tainted=False)
    assert len(ids) == 2
    pending = profile.list_memory_proposals(root)
    assert [item["content"] for item in pending] == ["汇报喜欢先给结论"]            # 隔离的不在待确认里
    assert [m["content"] for m in profile.list_memories(root)] == ["住在宁波"]        # 还没生效
    assert profile.review_memory_proposal(root, pending[0]["id"], True)["ok"]
    assert {m["content"] for m in profile.list_memories(root)} == {"住在宁波", "汇报喜欢先给结论"}
    assert profile.list_memory_proposals(root) == []


def test_memory_proposal_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    (tmp_path / ".vortocode").mkdir(exist_ok=True)
    [pid] = profile.propose_memories(str(tmp_path), ["常用 Telegram"], source="im", tainted=True)
    from src.web.server import app

    client = TestClient(app)
    assert client.get("/api/assistant/memory-proposals").json()["proposals"][0]["tainted"] is True
    assert client.post(f"/api/assistant/memory-proposals/{pid}", json={"action": "maybe"}).status_code == 400
    assert client.post(f"/api/assistant/memory-proposals/{pid}", json={"action": "reject"}).json()["status"] == "rejected"
    assert client.post(f"/api/assistant/memory-proposals/{pid}", json={"action": "approve"}).status_code == 409


# —— 头像 ——

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def test_avatar_accepts_only_real_images_by_header(tmp_path):
    import pytest
    root = str(tmp_path)
    assert profile.load_avatar(root) is None
    for bad in (b"<svg onload=alert(1)>", b"GIF89a....", b""):
        with pytest.raises(ValueError):
            profile.save_avatar(root, bad)
    with pytest.raises(ValueError):
        profile.save_avatar(root, _PNG + b"\x00" * profile.MAX_AVATAR_BYTES)
    assert profile.save_avatar(root, _PNG)["type"] == "image/png"
    # 换成 JPEG：旧 PNG 删掉，读到的是新图
    assert profile.save_avatar(root, _JPEG)["type"] == "image/jpeg"
    assert not (tmp_path / ".vortocode" / "assistant-avatar.png").exists()
    # 被改成伪装文件（扩展名对、内容不对）：当作没有
    (tmp_path / ".vortocode" / "assistant-avatar.jpeg").write_bytes(b"<svg/>")
    assert profile.load_avatar(root) is None
    assert profile.delete_avatar(root) is True and profile.delete_avatar(root) is False


def test_avatar_never_enters_profile_or_system_prompt(tmp_path):
    profile.save_avatar(str(tmp_path), _PNG)
    assert set(profile.load_profile(str(tmp_path))) == {"name", "soul", "about_you"}


def test_avatar_routes(tmp_path, monkeypatch):
    import base64
    from fastapi.testclient import TestClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    from src.web.server import app

    client = TestClient(app)
    assert client.get("/api/assistant/avatar").json() == {"avatar": None}
    data = base64.b64encode(_PNG).decode()
    assert client.put("/api/assistant/avatar", json={"data": data}).json()["avatar"]["data"] == data
    assert client.get("/api/assistant/avatar").json()["avatar"]["type"] == "image/png"
    assert client.put("/api/assistant/avatar", json={"data": "不是base64!"}).status_code == 400
    assert client.put("/api/assistant/avatar", json={"data": base64.b64encode(b"<svg/>").decode()}).status_code == 400
    assert client.put("/api/assistant/avatar", json={}).status_code == 400
    assert client.delete("/api/assistant/avatar").json() == {"removed": True}
    assert client.get("/api/assistant/avatar").json() == {"avatar": None}
