"""常驻助理巡检：状态变化推一次、首次只记现状、早晚报到点发一次、没配转发地址不启动。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.scheduling import assistant_watch as watch
from src.workflow.tasks import assistant_dispatch as dispatch

TZ = timezone(timedelta(hours=8))


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    path = tmp_path / "resident_projects.json"
    path.write_text(json.dumps({"projects": [{"name": "VortoCode", "repo_root": "/srv/VortoCode", "port": 8082}]}))
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(path))


class _Inbox:
    def __init__(self):
        self.messages = []

    async def __call__(self, text):
        self.messages.append(text)
        return True


def _items(root):
    async def refresh(_root):
        return dispatch.list_dispatches(root)
    return refresh


async def test_first_run_records_without_replaying_then_notifies_changes(tmp_path):
    root, inbox = str(tmp_path), _Inbox()
    old = dispatch.propose(root, project="VortoCode", title="旧任务", goal="g")
    noon = datetime(2026, 10, 11, 12, 0, tzinfo=TZ)
    await watch.watch_once(root, noon, notify=inbox, refresh=_items(root), times=[])
    assert inbox.messages == []                                    # 首次：旧账不推

    new = dispatch.propose(root, project="VortoCode", title="修登录", goal="g")
    await watch.watch_once(root, noon, notify=inbox, refresh=_items(root), times=[])
    assert len(inbox.messages) == 1 and "等你确认下发" in inbox.messages[0] and "修登录" in inbox.messages[0]
    await watch.watch_once(root, noon, notify=inbox, refresh=_items(root), times=[])
    assert len(inbox.messages) == 1                                # 没变化不重复推

    dispatch._update(root, new["id"], lambda item: item.update(status="done", branch="vorto/fix", result="已修好"))
    dispatch._update(root, old["id"], lambda item: item.update(status="running"))   # 在做：不推
    await watch.watch_once(root, noon, notify=inbox, refresh=_items(root), times=[])
    assert len(inbox.messages) == 2 and "✅" in inbox.messages[1] and "vorto/fix" in inbox.messages[1]


async def test_reports_once_per_slot_within_grace(tmp_path):
    root, inbox = str(tmp_path), _Inbox()
    dispatch.propose(root, project="VortoCode", title="等确认的", goal="g")
    before = datetime(2026, 10, 11, 8, 59, tzinfo=TZ)
    await watch.watch_once(root, before, notify=inbox, refresh=_items(root), times=["09:00"])
    assert inbox.messages == []
    at = datetime(2026, 10, 11, 9, 1, tzinfo=TZ)
    await watch.watch_once(root, at, notify=inbox, refresh=_items(root), times=["09:00"])
    await watch.watch_once(root, at + timedelta(minutes=1), notify=inbox, refresh=_items(root), times=["09:00"])
    reports = [text for text in inbox.messages if text.startswith("🗒")]
    assert len(reports) == 1 and "早报" in reports[0] and "等你处理：1 件" in reports[0] and "等确认的" in reports[0]
    late = datetime(2026, 10, 12, 12, 0, tzinfo=TZ)                # 超过补发窗口：不补
    await watch.watch_once(root, late, notify=inbox, refresh=_items(root), times=["09:00"])
    assert len([text for text in inbox.messages if text.startswith("🗒")]) == 1


def test_watch_needs_explicit_notify_url(monkeypatch):
    monkeypatch.delenv("VORTOCODE_OWNER_NOTIFY_URL", raising=False)
    assert not watch.watch_enabled()
    monkeypatch.setenv("VORTOCODE_OWNER_NOTIFY_URL", "http://127.0.0.1:8080/")
    assert watch.watch_enabled() and watch.notify_url() == "http://127.0.0.1:8080"
    monkeypatch.setenv("VORTOCODE_ASSISTANT_REPORT_TIMES", "08:30, bad ,21:00")
    assert watch.report_times() == ["08:30", "21:00"]


def test_relay_route_records_notice(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    from src.web.server import app
    from src.scheduling.notices import load_notices

    client = TestClient(app)
    assert client.post("/api/im/notify", json={"text": ""}).status_code == 400
    assert client.post("/api/im/notify", json={"text": "✅ 完成了", "source": "assistant"}).json() == {"ok": True}
    assert any("完成了" in item.get("text", "") and item.get("source") == "assistant"
               for item in load_notices(str(tmp_path), 10))


def test_schedule_overview_lists_reports_heartbeat_and_cron(tmp_path, monkeypatch):
    monkeypatch.delenv("VORTOCODE_OWNER_NOTIFY_URL", raising=False)
    monkeypatch.delenv("VORTOCODE_HEARTBEAT", raising=False)
    assert watch.schedule_overview(str(tmp_path)) == []
    monkeypatch.setenv("VORTOCODE_OWNER_NOTIFY_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("VORTOCODE_ASSISTANT_REPORT_TIMES", "09:00,21:00")
    monkeypatch.setenv("VORTOCODE_HEARTBEAT", "1")
    (tmp_path / ".vortocode").mkdir()
    (tmp_path / ".vortocode" / "cron.yaml").write_text(
        "jobs:\n  - name: 看邮箱\n    schedule: at 08:30\n    prompt: 有新询盘就告诉我\n", encoding="utf-8")
    items = {item["id"]: item for item in watch.schedule_overview(str(tmp_path))}
    assert items["report-09:00"]["title"] == "早报" and items["report-21:00"]["title"] == "晚报"
    assert items["heartbeat"]["schedule"] == "每 30m" and "follow-up" in items
    assert items["cron-看邮箱"]["detail"] == "有新询盘就告诉我"
