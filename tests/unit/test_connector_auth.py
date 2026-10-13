"""在 GitHub 上点一下就连上（设备授权）：验证码给人看、设备码不出进程、令牌写成本人只读文件。"""
from __future__ import annotations

import asyncio
import os
import stat

import pytest

from src.agents import mcp_tools
from src.workflow.workbench import connectors

_CONFIG = ("servers:\n  - name: github\n    transport: http\n    url: https://api.githubcopilot.com/mcp/\n"
           "    credential: github\n    read_only: true\n    oauth:\n      provider: github\n"
           "      client_id: Iv23liExampleClient1\n")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(connectors.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(mcp_tools.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "mcp.yaml").write_text(_CONFIG, encoding="utf-8")
    connectors._PENDING.clear()
    yield str(tmp_path)
    connectors._PENDING.clear()


class _GitHub:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    async def __call__(self, url, data):
        self.calls.append((url, dict(data)))
        if url == connectors._DEVICE_CODE_URL:
            return {"device_code": "dev-secret", "user_code": "ABCD-1234", "verification_uri": "https://github.com/login/device",
                    "interval": 5, "expires_in": 900}
        return self.answers.pop(0)


def test_only_github_oauth_connectors_with_a_client_id(root):
    assert connectors.connector_configs(root) == [
        {"name": "github", "provider": "github", "client_id": "Iv23liExampleClient1", "read_only": True}]
    assert connectors.status(root)[0]["connected"] is False


async def test_device_flow_hands_out_code_and_stores_token_owner_only(root, monkeypatch):
    github = _GitHub([{"error": "authorization_pending"}, {"error": "slow_down"},
                      {"access_token": "ghu_token123", "token_type": "bearer"}])
    sleeps = []

    async def fast_sleep(seconds):
        sleeps.append(seconds)
    monkeypatch.setattr(connectors.asyncio, "create_task",
                        lambda coro: asyncio.get_event_loop().create_task(coro))
    shown = await connectors.start_authorization(root, "github", post=github)
    assert shown["user_code"] == "ABCD-1234" and "device_code" not in shown        # 设备码不出接口
    connectors._PENDING["github"]["task"].cancel()
    await connectors._poll("github", "Iv23liExampleClient1", post=github, sleep=fast_sleep)
    assert sleeps == [5, 5, 10]                                                    # slow_down 加间隔
    token = connectors._token_path("github")
    assert stat.S_IMODE(os.stat(token).st_mode) == 0o600
    assert mcp_tools.load_connector_secret("github") == "ghu_token123"             # 与手放令牌同一套读取
    row = connectors.status(root)[0]
    assert row["connected"] and row["authorizing"] is None and "token" not in str(row)
    assert all(call[1].get("client_id") == "Iv23liExampleClient1" and "client_secret" not in call[1]
               for call in github.calls)                                            # 不需要也不发 Client Secret


async def test_denied_or_expired_is_reported_and_disconnect_clears(root):
    github = _GitHub([{"error": "access_denied"}])

    async def fast_sleep(_):
        return None
    await connectors.start_authorization(root, "github", post=github)
    connectors._PENDING["github"]["task"].cancel()
    await connectors._poll("github", "Iv23liExampleClient1", post=github, sleep=fast_sleep)
    assert connectors.status(root)[0]["error"] == "你在 GitHub 上拒绝了授权"
    connectors._save_token("github", "ghu_x", None)
    assert connectors.disconnect(root, "github") and not connectors._token_path("github").exists()
    with pytest.raises(KeyError):
        connectors.disconnect(root, "nope")


def test_routes_never_return_the_token(root, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.chdir(root)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    connectors._save_token("github", "ghu_secretvalue", 3600)
    from src.web.server import app

    client = TestClient(app)
    body = client.get("/api/assistant/connectors").text
    assert "ghu_secretvalue" not in body and '"connected":true' in body.replace(" ", "")
    assert client.post("/api/assistant/connectors/nope/authorize").status_code == 404
    assert client.delete("/api/assistant/connectors/github").json() == {"removed": True}
