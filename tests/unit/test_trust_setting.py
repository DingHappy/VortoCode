"""授权档位的落盘与接口（trust_setting + /api/trust）——读不到就按最严，上限不可越过。"""

import json

import pytest

from src.agents.trust import ASK, FULL, READS
from src.gateway.trust_setting import get_trust_level, set_trust_level


def test_missing_file_reads_as_strictest(tmp_path):
    assert get_trust_level(str(tmp_path)) == ASK


def test_round_trip(tmp_path):
    assert set_trust_level(str(tmp_path), FULL) == FULL
    assert get_trust_level(str(tmp_path)) == FULL


def test_state_file_is_gitignored(tmp_path):
    set_trust_level(str(tmp_path), READS)
    ignore = (tmp_path / ".vortocode" / ".gitignore").read_text(encoding="utf-8")
    assert "trust.json" in ignore


@pytest.mark.parametrize("payload", ["not json", '"a string"', '{"level": "banana"}', "{}"])
def test_broken_or_unknown_content_falls_back_to_ask(tmp_path, payload):
    state = tmp_path / ".vortocode"
    state.mkdir(parents=True, exist_ok=True)
    (state / "trust.json").write_text(payload, encoding="utf-8")
    assert get_trust_level(str(tmp_path)) == ASK


def test_unknown_level_is_stored_as_ask(tmp_path):
    assert set_trust_level(str(tmp_path), "root-me") == ASK
    stored = json.loads((tmp_path / ".vortocode" / "trust.json").read_text(encoding="utf-8"))
    assert stored == {"level": ASK}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_DESKTOP_SIDECAR", raising=False)
    from src.web.server import app

    return TestClient(app)


def test_api_reports_level_and_ceiling(client):
    body = client.get("/api/trust").json()
    assert body["level"] == ASK
    assert body["levels"] == [ASK, READS, FULL]
    assert body["ceiling"] == READS          # 非 Desktop sidecar → external 档案


def test_api_rejects_unknown_level(client):
    assert client.put("/api/trust", json={"level": "banana"}).status_code == 400
    assert client.put("/api/trust", json={}).status_code == 400


def test_api_stores_the_choice_but_reports_the_clamped_effect(client):
    body = client.put("/api/trust", json={"level": FULL}).json()
    assert body["level"] == FULL              # 用户选的原样记下来
    assert body["effective"] == READS         # 但这个会话真正生效的是被上限夹过的那档
    assert client.get("/api/trust").json()["level"] == FULL


def test_desktop_project_sidecar_may_reach_full(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_DESKTOP_SIDECAR", "1")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    from src.web.server import app

    client = TestClient(app)
    body = client.put("/api/trust", json={"level": FULL}).json()
    assert body["ceiling"] == FULL and body["effective"] == FULL


def test_change_is_audited(client, tmp_path):
    client.put("/api/trust", json={"level": READS})
    audit = (tmp_path / ".vortocode" / "audit.log").read_text(encoding="utf-8")
    assert "trust_level_changed" in audit


async def test_gate_follows_a_live_level_change(tmp_path):
    """档位改了，正在跑的会话下一次判定就按新档走（gate 接受读取函数）。"""
    from src.agents import trust
    from src.agents.gate import make_confirm_gate

    asked = []

    async def ask(message):
        asked.append(message)
        return True

    gate = make_confirm_gate(ask, can_ask_human=True, capability_profile="local",
                             trust_level=lambda: get_trust_level(str(tmp_path)))

    assert await gate("写文件？", trust.WRITE) is True
    assert len(asked) == 1                    # 默认 ASK：问了

    set_trust_level(str(tmp_path), FULL)
    assert await gate("再写一次？", trust.WRITE) is True
    assert len(asked) == 1                    # 改成完全信任后不再问，且没有重建会话
