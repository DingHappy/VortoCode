"""Unknown results block automatic continuation, never explicit human control."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.scheduling import assistant_watch, codex_goal, codex_observer as observer
from src.scheduling import codex_supervisor as sup, codex_watch as watch
from src.workflow.workbench.personal_goals import PersonalGoals

T0 = datetime(2026, 10, 31, 10, tzinfo=timezone.utc)
TID = "uncertain-session"


class Model:
    def __init__(self, during=None):
        self.calls, self.during = 0, during

    async def chat(self, *args, **kwargs):
        self.calls += 1
        if self.during:
            self.during()
        return {"content": json.dumps({"verdict": "continue", "assessment": "缺证据", "next": "补证据",
                                      "criterion_ids": ["criterion-1"], "failure_kind": "evidence"})}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    class State:
        root = str(tmp_path)
        minute = 0
        notes = None

        def now(self):
            return T0 + timedelta(minutes=self.minute)

        def report(self, minute, active=False, turn="t1", tid=TID):
            self.minute = minute
            raw = observer.summarize_thread({"id": tid, "name": "核对文档", "cwd": "/synthetic", "status": {"type": "active" if active else "idle"}},
                {"id": turn, "status": "inProgress" if active else "completed", "items": [{"type": "userMessage", "content": [{"type": "text", "text": "本轮只核对文档"}]}]}, observed_at=self.now().timestamp())
            watch.record_report(self.root, {"threads": [raw]}, now=self.now())
            return watch.load_state(self.root)["threads"][0]

        def unknown(self):
            action = watch.propose_action(self.root, thread=TID, kind="message", text="可能已生效的指令", session="owner")
            watch.decide_action(self.root, action["id"], True)
            watch.claim_action(self.root, action["id"])
            return watch.finish_action(self.root, action["id"], ok=False, uncertain=True)

        async def notify(self, text, confirm_id=""):
            self.notes.append((text, confirm_id))
            return True

    state = State()
    state.notes = []
    monkeypatch.setattr(watch, "_now", state.now)
    thread = state.report(0, True)
    state.goal = PersonalGoals(state.root).create({"request_id": "uncertain-goal", "objective": "核对文档", "acceptance_criteria": ["用户验收"], "next_action": "核对文档"})
    watch.hand_over(state.root, thread, now=T0, goal=codex_goal.binding(state.root, state.goal["id"], 1))
    return state


@pytest.mark.parametrize("result", ["unknown", "legacy-unconfirmed", "legacy-delivered"])
async def test_unconfirmed_results_spend_no_review_quota_and_regrant_does_not_bypass_them(setup, result):
    s = setup
    await sup.supervise_once(s.root, s.now(), notify=s.notify)
    action = s.unknown()
    if result != "unknown":
        rows = watch.load_actions(s.root)
        rows[0].update(status="failed", error="无法确认" if result == "legacy-unconfirmed" else "可能已送达")
        watch._save_actions(s.root, rows)
    s.report(5)
    s.minute = 30
    model = Model()
    await sup.supervise_once(s.root, s.now(), notify=s.notify, llm=model)
    assert model.calls == 0 and len(watch.load_actions(s.root)) == 1
    assert sup.goal_review_state(s.root)[s.goal["id"]]["used"] == 0
    mark = sup._load(s.root)["marks"][TID]
    assert "reviewed_turn" not in mark and mark.get("uncertain_notice")
    view = watch.goal_supervision(s.root)[0]
    assert view["state"] == "paused" and "结果仍无法确认" in view["reason"]
    # A new grant and a fresh heartbeat are not transport evidence.
    watch.take_back(s.root, TID)
    watch.hand_over(s.root, watch.load_state(s.root)["threads"][0], now=s.now(), goal=codex_goal.binding(s.root, s.goal["id"], 1))
    s.report(60)
    await sup.supervise_once(s.root, s.now(), notify=s.notify, llm=model)
    assert model.calls == 0 and not any(a.get("automatic") for a in watch.load_actions(s.root))
    # The real late receipt resolves the original ID, allowing the first review.
    watch.finish_action(s.root, action["id"], ok=True)
    s.report(90)
    await sup.supervise_once(s.root, s.now(), notify=s.notify, llm=model)
    assert model.calls == 1 and sup.goal_review_state(s.root)[s.goal["id"]]["used"] == 1


@pytest.mark.parametrize("phase", ["proposed", "approved"])
def test_old_automatic_draft_cannot_pass_a_new_unknown_result_but_manual_control_can(setup, phase):
    s = setup
    s.report(5)
    auto = watch.propose_action(s.root, thread=TID, kind="message", text="后台拟定的下一步", automatic=True)
    if phase == "approved":
        watch.decide_action(s.root, auto["id"], True)
    uncertain = s.unknown()
    with pytest.raises(watch.CodexActionError, match="待处理|结果不明|过时"):
        (watch.decide_action(s.root, auto["id"], True) if phase == "proposed" else watch.claim_action(s.root, auto["id"]))
    assert next(a for a in watch.load_actions(s.root) if a["id"] == auto["id"])["status"] == "expired"
    assert next(a for a in watch.load_actions(s.root) if a["id"] == uncertain["id"])["status"] == "unknown"
    manual = watch.propose_action(s.root, thread=TID, kind="message", text="主人核对后明确要求的新指令", session="owner")
    assert manual["automatic"] is False
    watch.decide_action(s.root, manual["id"], True)
    assert watch.claim_action(s.root, manual["id"])["status"] == "sending"


async def test_unknown_appearing_during_review_discards_the_result_without_refunding(setup):
    s = setup
    await sup.supervise_once(s.root, s.now(), notify=s.notify)
    s.report(5)
    s.minute = 30
    model = Model(s.unknown)
    await sup.supervise_once(s.root, s.now(), notify=s.notify, llm=model)
    assert model.calls == 1 and len(watch.load_actions(s.root)) == 1
    assert watch.load_actions(s.root)[0]["status"] == "unknown"
    assert sup._load(s.root)["marks"][TID]["review_status"] == "superseded"
    assert sup.goal_review_state(s.root)[s.goal["id"]]["used"] == 1
    action = watch.load_actions(s.root)[0]
    watch.finish_action(s.root, action["id"], ok=True)
    s.report(60)
    await sup.supervise_once(s.root, s.now(), notify=s.notify, llm=model)
    assert model.calls == 1


def test_automatic_creation_checks_other_actions_under_the_original_lock(setup):
    s = setup
    s.unknown()
    before = watch._actions_path(s.root).read_bytes()
    with pytest.raises(watch.CodexActionError, match="结果不明"):
        watch.propose_action(s.root, thread=TID, kind="message", text="不应创建", automatic=True)
    assert watch._actions_path(s.root).read_bytes() == before


@pytest.mark.parametrize("message", ["无法确认", "可能已送达"])
def test_legacy_uncertainty_is_not_evicted_reported_as_failed_or_hidden_by_age(setup, message):
    s = setup
    old = s.unknown()
    rows = watch.load_actions(s.root)
    rows[0].update(status="failed", error=message, updated_at=(T0-timedelta(days=3)).isoformat())
    rows.extend({**old, "id": f"cdx-terminal-{i}", "status": "sent", "error": ""} for i in range(99))
    watch._save_actions(s.root, rows)
    watch.propose_action(s.root, thread=TID, kind="message", text="新的人工作业", session="owner")
    assert any(a["id"] == old["id"] for a in watch.load_actions(s.root))
    text = watch.action_overview(s.root, now=s.now(), limit=4)
    assert old["id"] in text and "结果无法确认" in text and "自动推进已暂停" in text
    status, issue = assistant_watch._codex_now_status(s.root)
    assert not issue and "结果无法确认" in status(old["id"])
    assert "可能已经发出" in watch.action_result_notice(watch.load_actions(s.root)[0])
    from src.workflow.tasks.attention import attention_items
    assert any(item["target_id"] == old["id"] and item["severity"] == "high" for item in attention_items(s.root))


async def test_same_unresolved_set_notifies_once_and_failed_delivery_can_retry(setup):
    s = setup
    await sup.supervise_once(s.root, s.now(), notify=s.notify)
    action = s.unknown()
    s.report(5)
    s.minute = 30
    calls = []

    async def rejected(text, confirm_id=""):
        calls.append(text)
        return False

    await sup.supervise_once(s.root, s.now(), notify=rejected, llm=Model())
    assert "uncertain_notice" not in sup._load(s.root)["marks"][TID]
    s.report(60)
    await sup.supervise_once(s.root, s.now(), notify=s.notify, llm=Model())
    assert any("结果仍无法确认" in text for text, _ in s.notes)
    s.notes.clear()
    # Repeated uncertain receipt changes its timestamp, not the underlying unresolved set.
    watch.finish_action(s.root, action["id"], ok=False, uncertain=True)
    s.report(90)
    await sup.supervise_once(s.root, s.now(), notify=s.notify, llm=Model())
    assert s.notes == []


async def test_one_uncertain_session_does_not_starve_other_sessions_review_slots(setup):
    s = setup
    ids = [TID, "healthy-b", "healthy-c"]

    def report_all(minute, active):
        s.minute = minute
        threads = [observer.summarize_thread({"id": tid, "name": tid, "cwd": "/synthetic", "status": {"type": "active" if active else "idle"}},
            {"id": "t1", "status": "inProgress" if active else "completed", "items": [{"type": "userMessage", "content": [{"type": "text", "text": "只核对文档"}]}]}, observed_at=s.now().timestamp()) for tid in ids]
        watch.record_report(s.root, {"threads": threads}, now=s.now())

    report_all(0, True)
    for thread in watch.load_state(s.root)["threads"][1:]:
        watch.hand_over(s.root, thread, now=T0)
    await sup.supervise_once(s.root, s.now(), notify=s.notify)
    s.unknown()
    report_all(5, False)
    s.minute = 30
    model = Model()
    await sup.supervise_once(s.root, s.now(), notify=s.notify, llm=model)
    assert model.calls == 2
    assert {a["thread_id"] for a in watch.load_actions(s.root) if a["status"] == "proposed"} == {"healthy-b", "healthy-c"}
    assert sup.goal_review_state(s.root)[s.goal["id"]]["used"] == 0


@pytest.mark.parametrize("phase", ["legacy", "explicit"])
def test_automatic_message_cannot_be_approved_after_a_new_unknown_result(setup, phase):
    s = setup
    s.report(5)
    auto = watch.propose_action(s.root, thread=TID, kind="message", text="旧自动判断", automatic=True)
    if phase == "legacy":
        rows = watch.load_actions(s.root)
        rows[0].pop("automatic")
        watch._save_actions(s.root, rows)
    s.unknown()
    with pytest.raises(watch.CodexActionError):
        watch.decide_action(s.root, auto["id"], True)
    assert next(a for a in watch.load_actions(s.root) if a["id"] == auto["id"])["status"] == "expired"


def test_human_query_returns_all_bounded_records_without_enlarging_execution_polling(setup):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.web.routers import codex
    from unittest.mock import patch
    s = setup
    old = s.unknown()
    rows = watch.load_actions(s.root)
    rows.extend({**old, "id": f"cdx-approved-{i}", "status": "approved", "error": ""} for i in range(99))
    watch._save_actions(s.root, rows)
    app = FastAPI()
    app.include_router(codex.router)
    with patch.object(codex, "_general_root", return_value=s.root):
        client = TestClient(app)
        all_records = client.get("/api/codex/actions").json()["actions"]
        executable = client.get("/api/codex/actions?status=approved").json()["actions"]
    assert len(all_records) == 100 and any(a["id"] == old["id"] for a in all_records)
    assert len(executable) == 50 and all(a["status"] == "approved" for a in executable)


def test_explicit_invalid_automatic_marker_is_not_downgraded_to_manual(setup):
    s = setup
    action = s.unknown()
    path = watch._actions_path(s.root)
    data = [{**action, "automatic": "false"}]
    original = json.dumps(data)
    path.write_text(original)
    with pytest.raises(watch.CodexActionStorageError):
        watch.load_actions(s.root)
    assert path.read_text() == original


def test_material_bound_legacy_suggestion_cannot_be_weakened_by_an_explicit_false_marker(setup):
    s = setup
    current = watch.load_state(s.root)["threads"][0]
    context = current["turn"]["review_context"]["fingerprint"]
    auto = watch.propose_action(s.root, thread=TID, kind="message", text="材料判断建议", expected_context_fingerprint=context)
    rows = watch.load_actions(s.root)
    rows[0]["automatic"] = False
    watch._save_actions(s.root, rows)
    s.unknown()
    with pytest.raises(watch.CodexActionError):
        watch.decide_action(s.root, auto["id"], True)
    assert next(a for a in watch.load_actions(s.root) if a["id"] == auto["id"])["status"] == "expired"


async def test_daily_unknown_status_is_reported_without_repeated_model_rewriting(setup):
    s = setup
    s.unknown()

    class NoModel:
        async def chat(self, *args, **kwargs):
            pytest.fail("unknown transport outcome requested a report model")

    text = await assistant_watch.compose_report(s.root, s.now(), "晚报", llm=NoModel())
    assert "不确定发没发出去" in text and "没有等你处理的事" not in text
