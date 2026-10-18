"""跟进建议：子 Agent 记范围外发现（不顺手改）、去重限量、Desktop 一键开始或忽略、不进「等你处理」。"""
from __future__ import annotations

import pytest

from src.workflow.tasks import followups


def test_ledger_dedups_caps_per_source_and_tracks_state(tmp_path):
    root = str(tmp_path)
    first = followups.add(root, title="给 parse_date 补时区测试", detail="utils/dates.py", source="修登录")
    again = followups.add(root, title="给 parse_date  补时区测试", source="另一个任务")      # 空白差异：同一件事
    assert again["id"] == first["id"] and len(followups.list_open(root)) == 1
    followups.add(root, title="二", source="修登录")
    followups.add(root, title="三", source="修登录")
    with pytest.raises(followups.FollowupError, match="已记了 3 条"):
        followups.add(root, title="四", source="修登录")
    with pytest.raises(followups.FollowupError):
        followups.add(root, title="  ", source="修登录")

    started = followups.mark_started(root, first["id"], "task-1")
    assert started["status"] == "started" and started["task_id"] == "task-1"
    with pytest.raises(followups.FollowupError, match="已经处理过"):
        followups.dismiss(root, first["id"])
    assert "修登录" in followups.task_prompt(first) and "utils/dates.py" in followups.task_prompt(first)
    assert [item["kind"] for item in followups.decision_items(root)] == ["followup", "followup"]
    assert all(item["severity"] == "low" and item["can_dismiss"] for item in followups.decision_items(root))


async def test_sub_agent_tool_records_instead_of_fixing(tmp_path):
    from src.agents.dev_tools import build_followup_tool, followup_note
    recorded: list = []
    tool = build_followup_tool(str(tmp_path), "修登录", recorded)
    assert not tool.read_only and "不要顺手去改" in tool.description
    reply = await tool.handler({"title": "README 的安装步骤过时了", "detail": "还写着 pip install -r"})
    assert "已记为跟进建议" in reply and recorded == ["README 的安装步骤过时了"]
    [entry] = followups.list_open(str(tmp_path))
    assert entry["source"] == "修登录"
    assert "README 的安装步骤过时了" in followup_note(recorded) and followup_note([]) == ""


def test_followups_are_not_in_needs_you(tmp_path):
    from src.workflow.tasks.attention import attention_items
    followups.add(str(tmp_path), title="顺手发现的问题", source="x")
    assert attention_items(str(tmp_path)) == []                     # 建议不挡事，不占早报和语音


def test_desktop_routes_start_and_dismiss(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import src.web.routers.tasks as task_routes

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    monkeypatch.delenv("VORTOCODE_WORKSPACE_SCOPE", raising=False)
    submitted = []

    class Runner:
        ledger = type("L", (), {"repo_root": str(tmp_path)})()

        async def submit(self, prompt, *, kind, owner_session=""):
            submitted.append((prompt, kind, owner_session))
            return type("T", (), {"id": "task-9", "status": "queued"})()

    monkeypatch.setattr(task_routes, "get_runner", lambda: Runner())
    keep = followups.add(str(tmp_path), title="补时区测试", source="修登录")
    drop = followups.add(str(tmp_path), title="不值得做的", source="修登录")
    from src.web.server import app
    client = TestClient(app)

    decisions = client.get("/api/decisions").json()["decisions"]
    assert {item["id"] for item in decisions} >= {f"followup:{keep['id']}", f"followup:{drop['id']}"}
    assert client.post(f"/api/decisions/followup:{drop['id']}/dismiss").json()["ok"] is True

    started = client.post(f"/api/followups/{keep['id']}/start", json={"session": "sid-abc"}).json()
    assert started == {"id": "task-9", "status": "queued", "followup": keep["id"]}
    assert submitted[0][1] == "dev" and "补时区测试" in submitted[0][0] and submitted[0][2] == "sid-abc"
    assert client.post(f"/api/followups/{keep['id']}/start", json={}).status_code == 404      # 不会重复启动
    assert followups.list_open(str(tmp_path)) == []
