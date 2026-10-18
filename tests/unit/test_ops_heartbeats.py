"""运维心跳：失败与恢复立刻推、该报没报每次断档推一次、专用令牌、接进常驻助理巡检。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.scheduling import ops_heartbeats as hb

T0 = datetime(2026, 10, 18, 4, 30, tzinfo=timezone.utc)


def test_failure_and_recovery_are_reported_quiet_success_is_not(tmp_path):
    root = str(tmp_path)
    assert hb.record(root, "nas-backup", ok=True, text="同步完成", now=T0) is None
    failed = hb.record(root, "nas-backup", ok=False, text="one_api 最新备份已经 40 小时", now=T0 + timedelta(days=1))
    assert failed.startswith("🔴 nas-backup 失败") and "40 小时" in failed
    recovered = hb.record(root, "nas-backup", ok=True, text="同步完成", now=T0 + timedelta(days=2))
    assert recovered.startswith("✅ nas-backup 已恢复")
    assert hb.record(root, "nas-backup", ok=True, now=T0 + timedelta(days=3)) is None


def test_silence_is_reported_once_per_gap(tmp_path):
    root = str(tmp_path)
    hb.record(root, "nas-backup", ok=True, expect_hours=30, now=T0)
    assert hb.overdue(root, T0 + timedelta(hours=29)) == []
    [message] = hb.overdue(root, T0 + timedelta(hours=31))
    assert "nas-backup 已经 31 小时没有报到成功" in message
    assert hb.overdue(root, T0 + timedelta(hours=40)) == []                 # 同一次断档只推一次
    assert hb.record(root, "nas-backup", ok=True, now=T0 + timedelta(hours=41)).startswith("✅")   # 恢复要告诉人
    assert hb.overdue(root, T0 + timedelta(hours=60)) == []
    assert len(hb.overdue(root, T0 + timedelta(hours=72))) == 1             # 下一次断档重新提醒


def test_bad_reports_are_refused_and_secrets_are_not_stored(tmp_path):
    root = str(tmp_path)
    for kwargs in ({"source": "Bad Name"}, {"source": "../x"}, {"source": "ok", "expect_hours": 0},
                   {"source": "ok", "expect_hours": "soon"}):
        with pytest.raises(hb.HeartbeatError):
            hb.record(root, ok=True, **{"source": "x", **kwargs})
    hb.record(root, "nas-backup", ok=False, text="mysql -p sk-abcdefghijklmnopqrstuvwx123456 失败", now=T0)
    assert "sk-abcdefghijklmnopqrstuvwx123456" not in json.dumps(hb.snapshot(root), ensure_ascii=False)


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    monkeypatch.setenv("VORTOCODE_OPS_TOKEN", "ops-secret")
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "main-secret")
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


def _post(client, body, token="ops-secret"):
    return client.post("/api/ops/heartbeat", json=body, headers={"Authorization": f"Bearer {token}"} if token else {})


def test_route_uses_its_own_token_and_pushes_failures(client, monkeypatch):
    body = {"source": "nas-backup", "ok": False, "text": "NAS 未挂载"}
    assert _post(client, body, token=None).status_code == 404
    assert _post(client, body, token="main-secret").status_code == 404        # 主 API 令牌进不来
    assert client.get("/api/assistant/profile", headers={"Authorization": "Bearer ops-secret"}).status_code == 401
    assert _post(client, body).json() == {"ok": True, "notified": True}
    assert client.sent == ["🔴 nas-backup 失败：NAS 未挂载"]
    assert _post(client, {"source": "nas-backup", "ok": "yes"}).status_code == 400
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "project")
    assert _post(client, body).status_code == 404                             # 只在常驻助理上开


async def test_assistant_watch_pushes_overdue_heartbeats(tmp_path, monkeypatch):
    from src.scheduling import assistant_watch as watch
    root = str(tmp_path)
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(tmp_path / "none.json"))
    hb.record(root, "restore-drill", ok=True, expect_hours=24 * 32, now=T0)
    sent = []

    async def notify(text):
        sent.append(text)
        return True

    async def refresh(_root):
        return []

    await watch.watch_once(root, T0 + timedelta(days=33), notify=notify, refresh=refresh, times=[])
    assert len(sent) == 1 and "restore-drill" in sent[0]
