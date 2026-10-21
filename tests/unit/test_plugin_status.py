"""Desktop 插件页的后端：凭据状态、真去读一次的检查、移除凭据；都不吐凭据原文。"""
import os

import pytest
from fastapi.testclient import TestClient

import src.plugins as plugins
from src.agents import web_fetch
from src.plugins import calendar_ics

SECRET = "https://calendar.example.com/private-abc123/basic.ics"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    calendar_ics._cache.clear()
    return tmp_path


def put(home, name, content, mode=0o600):
    folder = home / ".vortocode" / "connectors"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.token"
    path.write_text(content + "\n")
    os.chmod(path, mode)
    return path


@pytest.fixture
def client():
    from src.web.server import app
    return TestClient(app)


def test_status_lists_every_plugin_with_local_credential_state(home, client):
    put(home, "calendar", SECRET)
    put(home, "notion", "ntn_x", mode=0o644)
    rows = {row["name"]: row for row in client.get("/api/assistant/plugins").json()["plugins"]}
    assert rows["calendar"]["status"] == "available" and rows["notion"]["status"] == "invalid"
    assert rows["calendar"]["credential_file"] == "~/.vortocode/connectors/calendar.token"
    assert "private-abc123" not in str(rows) and str(home) not in str(rows)


def test_check_reads_once_and_explains_failures_without_the_secret(home, client, monkeypatch):
    assert client.post("/api/assistant/plugins/calendar/check").json() == {
        "ok": False, "status": "missing", "detail": "还没放凭据"}
    put(home, "calendar", SECRET)
    monkeypatch.setattr(web_fetch, "fetch_bytes", lambda url, **_kw: (
        b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:a\r\nDTSTART:20200101T090000\r\nSUMMARY:x\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"))
    result = client.post("/api/assistant/plugins/calendar/check").json()
    assert result["ok"] and "共 1 条日程" in result["detail"]

    def boom(url, **_kw):
        raise web_fetch.FetchError("对方返回 HTTP 404", 404)

    calendar_ics._cache.clear()
    monkeypatch.setattr(web_fetch, "fetch_bytes", boom)
    result = client.post("/api/assistant/plugins/calendar/check").json()
    assert not result["ok"] and "HTTP 404" in result["detail"] and "private-abc123" not in result["detail"]
    assert client.post("/api/assistant/plugins/nope/check").status_code == 404


async def test_unexpected_errors_only_show_their_type(home, monkeypatch):
    put(home, "notion", "ntn_x")

    async def broken():
        raise RuntimeError("leaks ntn_x somewhere")

    real = plugins.available_plugins
    monkeypatch.setattr(plugins, "available_plugins", lambda: [
        p if p.name != "notion" else plugins.Plugin(p.name, p.title, p.hint, p.build_tools, check=broken) for p in real()])
    result = await plugins.check_plugin("notion")
    assert result == {"ok": False, "status": "available", "detail": "检查失败（RuntimeError）"}


def test_remove_deletes_only_a_known_plugin_credential(home, client):
    path = put(home, "notion", "ntn_x")
    other = put(home, "github", "ghp_x")
    assert client.delete("/api/assistant/plugins/notion").json() == {"removed": True}
    assert not path.exists() and other.exists()
    assert client.delete("/api/assistant/plugins/notion").json() == {"removed": False}
    assert client.delete("/api/assistant/plugins/github").status_code == 404


def test_desktop_can_save_a_credential_which_lands_600_and_is_never_echoed(home, client, monkeypatch):
    monkeypatch.setattr(web_fetch, "fetch_bytes", lambda url, **_kw: (
        b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:a\r\nDTSTART:20200101T090000\r\nSUMMARY:x\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"))
    response = client.put("/api/assistant/plugins/calendar", json={"credential": "  " + SECRET + "\n"})
    assert response.status_code == 200 and "private-abc123" not in response.text
    assert response.json()["saved"] and response.json()["check"]["ok"]
    path = home / ".vortocode" / "connectors" / "calendar.token"
    assert path.read_text() == SECRET + "\n"
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700
    rows = {row["name"]: row["status"] for row in client.get("/api/assistant/plugins").json()["plugins"]}
    assert rows["calendar"] == "available"


@pytest.mark.parametrize("name,value,message", [
    ("calendar", "http://calendar.example.com/private-abc123", "订阅链接"),
    ("calendar", "ftp://x/private-abc123", "订阅链接"),
    ("notion", "private-abc123-not-a-token", "ntn_"),
    ("notion", "ntn_ private-abc123", "空白"),
])
def test_bad_credentials_are_refused_without_echo_or_file(home, client, name, value, message):
    response = client.put(f"/api/assistant/plugins/{name}", json={"credential": value})
    assert response.status_code == 400 and message in response.json()["detail"]
    assert "private-abc123" not in response.text
    assert not (home / ".vortocode" / "connectors" / f"{name}.token").exists()


def test_save_rejects_unknown_plugins_and_missing_fields(home, client):
    assert client.put("/api/assistant/plugins/github", json={"credential": "ghp_x"}).status_code == 404
    assert client.put("/api/assistant/plugins/notion", json={}).status_code == 400
