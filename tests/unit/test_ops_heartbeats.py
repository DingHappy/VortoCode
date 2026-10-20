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


def test_brief_line_only_exists_where_heartbeats_are_configured(tmp_path):
    root = str(tmp_path)
    assert hb.brief_line(root, T0) == ""                                      # 没配运维心跳：早报里完全没有
    hb.record(root, "nas-backup", ok=True, expect_hours=30, now=T0)
    hb.record(root, "restore-drill", ok=True, expect_hours=24 * 32, now=T0)
    assert hb.brief_line(root, T0 + timedelta(hours=1)) == "运维：2 项正常（nas-backup、restore-drill）"
    hb.record(root, "account-service", ok=False, text="连不上中转站", expect_hours=3, now=T0)
    line = hb.brief_line(root, T0 + timedelta(hours=31))                       # nas-backup 也超时了
    assert line.splitlines() == ["运维：2 项异常，1 项正常", "· account-service 失败（连不上中转站）",
                                 "· nas-backup 超时没报到"]


async def test_morning_brief_carries_the_ops_line_outside_the_model(tmp_path):
    from src.scheduling import assistant_watch as watch
    root = str(tmp_path)

    class _NoLLM:
        async def chat(self, messages, **_kw):
            raise AssertionError("安静的日子不该调模型")

    now = T0 + timedelta(hours=1)
    assert await watch.compose_report(root, now, "早报", llm=_NoLLM()) == "🗒 早报：今天没有需要你处理的事。"
    hb.record(root, "nas-backup", ok=True, now=T0)
    morning = await watch.compose_report(root, now, "早报", llm=_NoLLM())
    assert morning == "🗒 早报：今天没有需要你处理的事。\n\n运维：1 项正常（nas-backup）"
    assert await watch.compose_report(root, now, "晚报", llm=_NoLLM()) == ""


def test_notice_route_pushes_a_line_with_the_ops_token_only(client, monkeypatch):
    monkeypatch.setattr(hb, "_notice_times", [])
    post = lambda body, token="ops-secret": client.post(  # noqa: E731
        "/api/ops/notice", json=body, headers={"Authorization": f"Bearer {token}"} if token else {})
    body = {"source": "account-service", "text": "新付款：sq***@icloud.com 买了「体验」¥9.9"}
    assert post(body, token=None).status_code == 404
    assert post(body, token="main-secret").status_code == 404
    assert post(body).json() == {"ok": True, "notified": True}
    assert client.sent == ["📣 account-service：新付款：sq***@icloud.com 买了「体验」¥9.9"]
    assert post({"source": "Bad Name", "text": "x"}).status_code == 400
    assert post({"source": "account-service", "text": "  "}).status_code == 400


def test_notices_are_capped_per_day_and_scrubbed(monkeypatch):
    monkeypatch.setattr(hb, "_notice_times", [])
    for i in range(hb._NOTICE_DAILY_LIMIT):
        hb.notice_text("account-service", f"第 {i} 条", now=T0)
    with pytest.raises(hb.HeartbeatError):
        hb.notice_text("account-service", "多了", now=T0 + timedelta(hours=1))
    assert hb.notice_text("account-service", "隔天又能发", now=T0 + timedelta(days=1, minutes=1))
    assert "sk-abcdefghijklmnopqrstuvwx123456" not in hb.notice_text(
        "account-service", "key sk-abcdefghijklmnopqrstuvwx123456", now=T0 + timedelta(days=1, minutes=2))


def test_route_accepts_event_reports(client, monkeypatch):
    monkeypatch.setenv("VORTOCODE_OPS_TOKEN", "ops-token")
    response = client.post("/api/ops/heartbeat", json={"source": "relay-deploy", "ok": True, "text": "发布", "event": True},
                           headers={"Authorization": "Bearer ops-token"})
    assert response.status_code == 200 and response.json()["notified"] is False
