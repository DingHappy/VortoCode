"""小智语音端点：专用令牌、只在常驻助理开放、三个工具、语音提议只记账且限流。"""
from __future__ import annotations

import json

import pytest

from src.workflow.tasks import assistant_dispatch as dispatch

VOICE = "voice-secret"
MAIN = "main-secret"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    registry = tmp_path / "resident_projects.json"
    registry.write_text(json.dumps({"projects": [{"name": "VortoCode", "repo_root": "/srv/VortoCode", "port": 8082}]}))
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(registry))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    monkeypatch.setenv("VORTOCODE_VOICE_MCP_TOKEN", VOICE)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", MAIN)
    import src.scheduling.notices as notices
    sent = []

    def fake_make_notifier(root, **_kw):
        async def notify(text, **_):
            sent.append(text)
            return True
        return notify

    monkeypatch.setattr(notices, "make_notifier", fake_make_notifier)
    from src.web.server import app
    test_client = TestClient(app)
    test_client.sent = sent
    return test_client


def _rpc(client, method, params=None, *, token=VOICE, msg_id=1):
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if msg_id is not None:
        body["id"] = msg_id
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/api/voice/mcp", json=body, headers=headers)


def _call(client, name, **arguments):
    reply = _rpc(client, "tools/call", {"name": name, "arguments": arguments}).json()["result"]
    return reply["content"][0]["text"], reply["isError"]


def test_only_the_voice_token_on_the_resident_assistant_gets_in(client, monkeypatch):
    assert _rpc(client, "tools/list").status_code == 200
    assert _rpc(client, "tools/list", token=None).status_code == 404
    assert _rpc(client, "tools/list", token="wrong").status_code == 404
    assert _rpc(client, "tools/list", token=MAIN).status_code == 404            # 主 API 令牌进不来
    other = client.get("/api/assistant/profile", headers={"Authorization": f"Bearer {VOICE}"})
    assert other.status_code == 401                                             # 语音令牌也进不了别的 API
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    assert _rpc(client, "tools/list").status_code == 404                        # 项目服务上不开
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    monkeypatch.delenv("VORTOCODE_VOICE_MCP_TOKEN")
    assert _rpc(client, "tools/list").status_code == 404                        # 不设令牌 = 不存在


def test_mcp_handshake_and_tool_list(client):
    init = _rpc(client, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                       "clientInfo": {"name": "xiaozhi", "version": "1"}}).json()
    assert init["result"]["serverInfo"]["name"] == "vortocode-assistant" and "tools" in init["result"]["capabilities"]
    assert _rpc(client, "notifications/initialized", msg_id=None).status_code == 202
    names = [tool["name"] for tool in _rpc(client, "tools/list").json()["result"]["tools"]]
    assert names == ["today_brief", "dispatch_status", "propose_task"]
    assert _rpc(client, "resources/list").json()["error"]["code"] == -32601
    assert client.get("/api/voice/mcp", headers={"Authorization": f"Bearer {VOICE}"}).status_code == 405


def test_today_brief_and_status_read_the_ledger(client, tmp_path):
    assert _call(client, "today_brief") == ("现在没有需要你处理的事，也没有正在做的任务。", False)
    dispatch.propose(str(tmp_path), project="VortoCode", title="修登录", goal="g")
    text, _ = _call(client, "today_brief")
    assert text == "有 1 件等你处理：任务单等你确认下发 · 修登录。"
    assert _call(client, "dispatch_status", keyword="登录")[0] == "「修登录」等你确认下发。"
    assert "没有找到" in _call(client, "dispatch_status", keyword="支付")[0]


def test_voice_proposal_is_only_recorded_tainted_notified_and_rate_limited(client, tmp_path):
    text, is_error = _call(client, "propose_task", request="把 README 里的安装步骤更新一下")
    assert not is_error and "确认后才会开始" in text
    [entry] = dispatch.list_dispatches(str(tmp_path))
    assert entry["status"] == "proposed" and entry["tainted"] is True and entry["session"] == "voice"
    assert "来自小智语音" in entry["goal"] and entry["project"] == "VortoCode"
    assert len(client.sent) == 1 and "小智记下一张任务提议" in client.sent[0]

    for _ in range(2):
        assert not _call(client, "propose_task", request="再做一件事")[1]
    text, is_error = _call(client, "propose_task", request="第四件")
    assert is_error and "这一小时已经记了 3 张" in text
    assert len(dispatch.list_dispatches(str(tmp_path))) == 3
    assert _call(client, "propose_task", request="  ")[1] is True


def test_today_brief_sees_more_than_dispatches(client, tmp_path):
    """语音和早报读的是同一份「等你处理」：例行作业最新一次失败也要说出来。"""
    from src.workflow.workbench.runs import CommandRun, RunLedger
    run = CommandRun.new("python -m src.scheduling.relay_duty", "cron")
    run.status, run.error = "failed", "中转站巡检失败"
    RunLedger(str(tmp_path)).save(run)
    assert _call(client, "today_brief")[0] == "有 1 件等你处理：例行作业失败。"
