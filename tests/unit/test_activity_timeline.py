"""动态时间线（ADR-012）：Desktop 与 App 共用的一条线，只读汇总已有台账；坏一个来源不拖垮整条线。"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.scheduling import goal_advancer, timeline
from src.workflow.workbench.personal_goals import PersonalGoals
from tests.unit.test_mobile_browser import native_handoff, setup  # noqa: F401
from tests.unit.test_mobile_owner import bind, owner, signed  # noqa: F401

NOW = datetime(2026, 11, 3, 2, 0, tzinfo=timezone.utc)


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = str(tmp_path)
    from src.scheduling.notices import record_notice
    record_notice(root, "🎯 为「修登录」拟了任务单\n在动态里下发", source="assistant")
    import src.agents.tools.personal_goals  # noqa: F401
    from src.agents import undo_log
    goal = PersonalGoals(root).create({"request_id": "req-00000001", "objective": "修登录",
                                       "acceptance_criteria": ["登录 200"], "next_action": "查日志"}, origin="owner")
    undo_log.record(root, action="personal_goal.create", summary="新建目标「修登录」",
                    payload={"goal_id": goal["id"], "revision": goal["revision"]}, source="assistant")
    from src.workflow.tasks import assistant_dispatch
    monkeypatch.setattr(assistant_dispatch, "list_dispatches", lambda r: [
        {"id": "dsp-0000000001", "project": "VortoCode", "title": "修登录 500", "status": "proposed",
         "created_at": "2026-11-03T09:00:00+08:00", "goal_link": {"id": goal["id"], "objective": "修登录"}}])
    from src.scheduling import codex_watch
    monkeypatch.setattr(codex_watch, "load_actions", lambda r, **kwargs: [
        {"id": "cdx-0000000001", "kind": "message", "status": "proposed", "text": "先补测试",
         "thread_name": "vortocode", "created_at": "2026-11-03T08:30:00+08:00"}])
    from src.scheduling.auto_review import record
    record(root, {"at": "2026-11-03T09:05:00+08:00", "kind": "dispatch", "summary": "修登录 500",
                  "verdict": "escalate", "reason": "拿不准"})
    goal_advancer._save_paused(root, {goal["id"]: (NOW + timedelta(hours=10)).isoformat()})
    return root, goal


def test_one_line_from_every_ledger_newest_first_with_paused_pinned(world):
    root, goal = world
    data = timeline.build(root, now=NOW)
    kinds = [item["kind"] for item in data["items"]]
    assert kinds[0] == "paused" and set(kinds) == {"paused", "notice", "change", "dispatch", "codex", "review"}
    assert data["unavailable"] == []
    rest = data["items"][1:]
    assert [item["at"] for item in rest] == sorted((item["at"] for item in rest), reverse=True)
    by_kind = {item["kind"]: item for item in data["items"]}
    assert by_kind["paused"]["actions"] == [{"type": "resume_goal", "id": goal["id"]}]
    assert by_kind["change"]["actions"][0]["type"] == "undo"
    assert by_kind["dispatch"]["status"] == "等你下发" and by_kind["dispatch"]["actions"][0]["type"] == "decide_dispatch"
    assert by_kind["review"]["status"] == "交给你"
    assert by_kind["notice"]["title"].startswith("🎯 为「修登录」") and by_kind["notice"]["detail"] == "在动态里下发"


def test_a_broken_source_is_marked_not_fatal(world, monkeypatch):
    root, _goal = world
    from src.scheduling import codex_watch

    def boom(_root, **kwargs):
        raise RuntimeError("corrupt")
    monkeypatch.setattr(codex_watch, "load_actions", boom)
    data = timeline.build(root, now=NOW)
    assert data["unavailable"] == ["codex"] and data["items"]


def test_expired_pause_disappears_and_resume_clears_it(world):
    root, goal = world
    assert not any(i["kind"] == "paused" for i in timeline.build(root, now=NOW + timedelta(hours=11))["items"])
    assert goal_advancer.resume(root, goal["id"]) is True
    assert goal_advancer.resume(root, goal["id"]) is False
    assert not any(i["kind"] == "paused" for i in timeline.build(root, now=NOW)["items"])


def test_desktop_routes(world, monkeypatch):
    root, goal = world
    monkeypatch.chdir(root)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    from src.web.server import app
    client = TestClient(app)
    data = client.get("/api/assistant/timeline", params={"limit": 20}).json()
    assert {item["kind"] for item in data["items"]} >= {"notice", "paused"}
    assert client.get("/api/assistant/timeline", params={"limit": 999}).status_code == 400
    assert client.post("/api/assistant/goal-advance/resume", json={"goal_id": goal["id"]}).status_code == 200
    assert client.post("/api/assistant/goal-advance/resume", json={"goal_id": goal["id"]}).status_code == 404


def test_phone_reads_the_same_line_and_resumes_with_a_signature(owner):  # noqa: F811
    client, headers, device_id, root = owner
    assert client.get("/api/mobile/native/timeline", headers=headers).status_code == 403   # 钥匙还没信任
    _, device, _ = bind(client, headers)
    goal = PersonalGoals(root).create({"request_id": "req-00000002", "objective": "交周报",
                                       "acceptance_criteria": ["已提交"], "next_action": "写"}, origin="owner")
    goal_advancer._save_paused(root, {goal["id"]: (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()})
    items = client.get("/api/mobile/native/timeline", headers=headers).json()["items"]
    assert items[0]["kind"] == "paused"
    assert client.post("/api/mobile/native/goal-advance/resume", json={"goal_id": goal["id"]},
                       headers=headers).status_code == 401                              # 没签名
    ok = signed(client, headers, device_id, "/api/mobile/native/goal-advance/resume", {"goal_id": goal["id"]}, device)
    assert ok.status_code == 200 and goal_advancer.load_paused(root) == {}


def test_stable_notice_ids_and_malformed_rows_do_not_hide_valid_entries(tmp_path):
    from src.scheduling.notices import record_notice
    root = str(tmp_path)
    record_notice(root, "旧通知", source="assistant")
    before = timeline.build(root)["items"][0]
    record_notice(root, "新通知", source="assistant")
    path = tmp_path / ".vortocode/logs/notices.jsonl"
    with path.open("a") as handle:
        handle.write('null\n{"ts":1e100,"text":"bad timestamp"}\n')
    after = timeline.build(root)
    assert before in after["items"]
    assert after["unavailable"] == []
    assert len(after["items"]) == 2
    assert len(timeline.build(root, limit=1)["items"]) == 1


def test_recently_updated_old_dispatch_is_not_lost_before_sorting(tmp_path, monkeypatch):
    from src.workflow.tasks import assistant_dispatch
    monkeypatch.setattr(assistant_dispatch, "list_dispatches", lambda root: [
        {"id": "new", "title": "新建", "created_at": "2026-11-01T00:00:00Z"},
        {"id": "old", "title": "刚完成", "created_at": "2025-01-01T00:00:00Z", "updated_at": "2026-11-02T00:00:00Z"},
    ])
    assert timeline.build(str(tmp_path), limit=1)["items"][0]["id"] == "dispatch:old"


def test_legacy_pause_is_visible_and_resume_does_not_resurrect_it(world):
    import json
    root, goal = world
    path = goal_advancer._paused_path(root)
    paused = goal_advancer.load_paused(root)
    path.unlink()
    (path.parent / "watch.json").write_text(json.dumps({"goal_advance_paused": paused}))
    assert goal_advancer.load_paused(root) == paused
    assert goal_advancer.resume(root, goal["id"])
    goal_advancer._migrate_paused(root, paused)  # 巡检仍持有升级前的旧状态
    assert goal_advancer.load_paused(root) == {}
    goal_advancer.pause_goal(root, "personal-other", (NOW + timedelta(hours=1)).isoformat())
    assert goal["id"] not in goal_advancer.load_paused(root)


async def test_resume_during_a_patrol_is_not_overwritten(world, monkeypatch):
    root, goal = world
    from src.workflow.tasks import assistant_dispatch
    from src.scheduling import notices
    monkeypatch.setattr(notices, "in_quiet_hours", lambda now: False)
    PersonalGoals(root).create({"request_id": "req-00000003", "objective": "另一个目标", "acceptance_criteria": ["已核对"], "next_action": "核对"}, origin="owner")
    monkeypatch.setattr(assistant_dispatch, "list_dispatches", lambda root: [])

    async def judge(*args, **kwargs):
        assert goal_advancer.resume(root, goal["id"])
        return {"action": "wait", "reason": "等"}

    async def notify(text):
        return True

    monkeypatch.setattr(goal_advancer, "judge", judge)
    await goal_advancer.advance_once(root, NOW, state={}, notify=notify, llm=object(), projects=[])
    assert goal_advancer.load_paused(root) == {}


def test_review_bad_rows_and_unavailable_pause_storage(world):
    root, _ = world
    from src.scheduling.auto_review import _log_path
    with _log_path(root).open("a") as handle:
        handle.write('null\n[]\nnot-json\n')
    assert any(item["kind"] == "review" for item in timeline.build(root, now=NOW)["items"])
    goal_advancer._paused_path(root).write_text("broken")
    data = timeline.build(root, now=NOW)
    assert "paused" in data["unavailable"] and data["items"]


def test_new_routes_keep_admin_and_loopback_relay_boundaries(world, monkeypatch):
    root, _ = world
    monkeypatch.chdir(root)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "admin-test-token")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    monkeypatch.setenv("VORTOCODE_APP_RELAY_TOKEN", "relay-test-token")
    from src.web.server import app
    client = TestClient(app)
    assert client.get("/api/assistant/timeline").status_code == 401
    assert client.post("/api/assistant/goal-advance/resume", json={"goal_id": "personal-x"}).status_code == 401
    relay_auth = {"Authorization": "Bearer relay-test-token"}
    assert client.post("/api/assistant/app-relay/timeline", headers=relay_auth, json={}).status_code == 404
    local = TestClient(app, client=("127.0.0.1", 50001))
    assert local.post("/api/assistant/app-relay/timeline", json={}).status_code == 404
    result = local.post("/api/assistant/app-relay/timeline", headers=relay_auth, json={"limit": 20})
    assert result.status_code == 200 and result.json()["items"]
    assert local.post("/api/assistant/app-relay/timeline", headers=relay_auth, json={"limit": 999}).status_code == 400


def test_phone_timeline_and_signed_resume_use_the_resident_relay(owner, monkeypatch):  # noqa: F811
    from src.gateway.relay_envelope import open_envelope
    client, headers, device_id, root = owner
    _, device, _ = bind(client, headers)
    goal_id = "personal-test-resume"
    goal_advancer.pause_goal(root, goal_id, (NOW + timedelta(hours=1)).isoformat())
    monkeypatch.setenv("VORTOCODE_APP_RELAY_URL", "http://127.0.0.1:8081")
    monkeypatch.setenv("VORTOCODE_APP_RELAY_TOKEN", "relay-test-token")
    calls = []

    async def relay(path, body, **kwargs):
        calls.append(path)
        if path.endswith("timeline"):
            return 200, {"items": [], "unavailable": [], "generated_at": NOW.isoformat()}
        op, args, who, kind = open_envelope(root, body["envelope"])
        assert op == "resume_goal" and args["goal_id"] == goal_id
        assert who == device_id and kind == "device"
        from src.web.owner_ops import run
        return await run(root, op, args)

    from src.gateway import app_relay_client
    monkeypatch.setattr(app_relay_client, "relay", relay)
    assert client.get("/api/mobile/native/timeline", headers=headers).status_code == 200
    assert signed(client, headers, device_id, "/api/mobile/native/goal-advance/resume", {"goal_id": goal_id}, device).status_code == 200
    assert calls == ["/api/assistant/app-relay/timeline", "/api/assistant/app-relay/op"]
    assert goal_advancer.load_paused(root) == {}


def test_codex_progress_ages_out_and_a_finished_turn_is_not_task_acceptance(tmp_path, monkeypatch):
    from src.scheduling import codex_watch
    thread = {"id": "thread-one", "name": "实现时间线", "status": "idle", "turn": {"status": "completed"}}
    monkeypatch.setattr(codex_watch, "progress_snapshot", lambda root, **kwargs: {"received_at": NOW.isoformat(), "threads": [thread]})
    item = timeline.build(str(tmp_path), now=NOW)["items"][0]
    assert item["status"] == "本轮结束，任务待验收"
    stale = timeline.build(str(tmp_path), now=NOW + timedelta(hours=1))["items"][0]
    assert "待核对" in stale["status"]
    thread["status"] = "active"
    thread["turn"]["status"] = "inProgress"
    thread["flags"] = ["waitingOnApproval"]
    assert timeline.build(str(tmp_path), now=NOW)["items"][0]["status"] == "等你批准"


def test_corrupted_codex_files_are_marked_unavailable(tmp_path):
    directory = tmp_path / ".vortocode/codex"
    directory.mkdir(parents=True)
    (directory / "actions.json").write_text("broken")
    (directory / "state.json").write_text("[]")
    data = timeline.build(str(tmp_path))
    assert data["unavailable"] == ["codex", "codex_progress"]
    assert data["items"] == []
