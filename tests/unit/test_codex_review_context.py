"""Visible-turn completeness and correction fences; all models/RPC are substitutes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from src.scheduling import codex_goal, codex_observer as observer
from src.scheduling import codex_review_context as context
from src.scheduling import codex_supervisor as sup, codex_watch as watch
from src.workflow.workbench.personal_goals import PersonalGoals

NOW = datetime(2026, 10, 31, 10, tzinfo=timezone.utc)


def user(text):
    return {"type": "userMessage", "content": [{"type": "text", "text": text}]}


def source(*, texts=("本轮只写文档，不修改代码。",), active=False, tid="thread-000001", turn="t1", minute=0):
    return observer.summarize_thread(
        {"id": tid, "name": "工程记录", "cwd": "/project/docs", "status": {"type": "active" if active else "idle"}},
        {"id": turn, "status": "inProgress" if active else "completed", "items": [user(t) for t in texts]},
        observed_at=(NOW + timedelta(minutes=minute)).timestamp())


class Model:
    def __init__(self, callback=None):
        self.calls, self.messages, self.callback = 0, [], callback

    async def chat(self, messages, **kwargs):
        self.calls += 1
        self.messages = messages
        if self.callback:
            self.callback()
        return {"content": json.dumps({"verdict": "continue", "assessment": "仍缺验收证据", "next": "补文档证据",
                                       "criterion_ids": ["criterion-1"], "failure_kind": "evidence"})}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(watch, "_now", lambda: NOW + timedelta(minutes=30))
    service = PersonalGoals(root)
    goal = service.create({"request_id": "docs", "objective": "本轮只写文档，记录经用户验收", "acceptance_criteria": ["记录经用户验收"],
                           "next_action": "只写工程记录"})
    watch.record_report(root, {"threads": [source(active=True)]}, now=NOW)
    watch.hand_over(root, watch.load_state(root)["threads"][0], now=NOW, goal=codex_goal.binding(root, goal["id"], 1))
    notes = []

    async def notify(text, confirm_id=""):
        notes.append((text, confirm_id))
        return True

    return root, goal, notify, notes


def store(root, thread, minute):
    watch.record_report(root, {"threads": [thread]}, now=NOW + timedelta(minutes=minute))


def test_short_display_does_not_discard_tail_constraints_or_same_turn_corrections():
    long = "背景说明。" * 120 + "本轮只写文档，不改代码，不运行测试。"
    extra = "补充限制：任何代码修改留到下一轮。\n  保留这行的缩进。"
    observed = source(texts=(long, extra))
    turn = observed["turn"]
    assert len(turn["request"]) == 401 and extra not in turn["request"]
    assert "不运行测试" not in turn["request"]
    clean = watch._clean_threads({"threads": [observed]})[0]
    assert context.read(clean["turn"]).messages == (long, extra)
    assert context.read(clean["turn"]).status == "complete"
    assert context.material(clean["turn"]) in sup._review_material(clean)
    decoded = json.loads(context.material(clean["turn"]))
    assert decoded["visible_user_messages"] == [long, extra]
    assert watch._clean_threads({"threads": [clean]}) == [clean]
    corrected = source(texts=(long, extra + " 不要自动提交。"))
    assert corrected["turn"]["request"] == turn["request"]
    assert observer.digest([corrected]) != observer.digest([observed])
    assert sup._signature(corrected) != sup._signature(observed)


@pytest.mark.parametrize("items,status,omitted", [
    ([user("x" * 4097)], "incomplete", 0),
    ([user(str(n)) for n in range(9)], "incomplete", 1),
    ([user("说明"), {"type": "userMessage", "content": [{"type": "image", "url": "private-image-bytes"}]}], "incomplete", 0),
    ([{"type": "userMessage", "content": [{"type": "future", "text": "未知来源"}]}], "incomplete", 0),
    ([None, user("说明")], "incomplete", 0),
    ([user("说明"), user("")], "incomplete", 0),
    ([user("")], "unknown", 0), ([], "unknown", 0), (None, "unknown", 0),
])
def test_bounded_capture_never_claims_unavailable_material_is_complete(items, status, omitted):
    raw = context.capture("t1", items)
    assert raw["status"] == status and raw["omitted_messages"] == omitted
    assert len(raw["messages"]) <= 8 and sum(len(m["text"]) for m in raw["messages"]) <= 4096
    assert context.decode(raw, "t1") == raw
    assert "private-image-bytes" not in json.dumps(raw)


@pytest.mark.parametrize("mutation", [
    {"schema_version": 2}, {"schema_version": True}, {"scope": "conversation"}, {"turn_id": "other"},
    {"message_count": True}, {"omitted_messages": -1}, {"omitted_messages": 1},
    {"unsupported_content": 0}, {"fingerprint": "fabricated"}, {"status": "unknown"},
    {"messages": [{"text": "替换要求", "original_chars": 4, "truncated": False}]},
    {"messages": [{"text": "说明", "original_chars": True, "truncated": False}]},
])
def test_invalid_version_identity_counts_and_changed_content_fail_closed(mutation):
    raw = {**context.capture("t1", [user("说明")]), **mutation}
    assert context.decode(raw, "t1") is None
    clean = watch._clean_threads({"threads": [{"id": "th1", "turn": {"id": "t1", "review_context": raw}}]})[0]
    assert "review_context" in clean["turn"] and clean["turn"]["review_context"] is None
    assert context.read(clean["turn"]).status == "unknown"


def test_only_user_text_is_collected_not_tool_results_diffs_or_agent_claims():
    raw = context.capture("t1", [user("检查状态"), {"type": "agentMessage", "text": "private-agent-claim"},
                                 {"type": "fileChange", "diff": "private-diff"}, {"type": "toolResult", "text": "private-tool"}])
    assert raw["status"] == "complete" and raw["message_count"] == 1
    assert all(value not in json.dumps(raw) for value in ("private-agent-claim", "private-diff", "private-tool"))


@pytest.mark.parametrize("fault", ["legacy", "long", "attachment", "empty", "future", "tampered"])
async def test_incomplete_material_skips_model_and_quota_but_can_be_refreshed(setup, fault):
    root, goal, notify, notes = setup
    model = Model()
    await sup.supervise_once(root, NOW, notify=notify, llm=model)
    thread = source(minute=5)
    if fault == "legacy":
        del thread["turn"]["review_context"]
    elif fault == "long":
        thread = source(texts=("x" * 4097,), minute=5)
    elif fault == "attachment":
        thread["turn"]["review_context"] = context.capture("t1", [user("检查附件"), {"type": "userMessage", "content": [{"type": "image"}]}])
    elif fault == "empty":
        thread = source(texts=(), minute=5)
    elif fault == "future":
        thread["turn"]["review_context"]["schema_version"] = 2
    else:
        thread["turn"]["review_context"]["messages"][0]["text"] += "变动"
    store(root, thread, 5)
    assert await sup.review_turn(watch.load_state(root)["threads"][0], llm=model) is None
    await sup.supervise_once(root, NOW + timedelta(minutes=30), notify=notify, llm=model)
    state = sup._load(root)
    assert model.calls == 0 and not watch.load_actions(root)
    assert state["goal_reviews"][goal["id"]]["used"] == 0
    assert "reviewed_turn" not in state["marks"][thread["id"]]
    assert any("暂停自动拟下一步" in text for text, _ in notes)
    view = watch.goal_supervision(root)[0]
    assert view["state"] == "paused" and view["used"] == 0
    assert watch.progress_snapshot(root)["threads"][0]["id"] == thread["id"]
    # A repaired observation before any model attempt can be reviewed once.
    store(root, source(minute=60), 60)
    await sup.supervise_once(root, NOW + timedelta(minutes=60), notify=notify, llm=model)
    assert model.calls == 1 and sup._load(root)["goal_reviews"][goal["id"]]["used"] == 1
    assert goal["objective"] in model.messages[1]["content"]


async def test_same_turn_correction_discards_inflight_result_without_refunding_or_repeating(setup):
    root, goal, notify, _ = setup
    long = "背景。" * 150 + "只写文档。"
    await sup.supervise_once(root, NOW, notify=notify)
    store(root, source(texts=(long,), minute=5), 5)
    model = Model(lambda: store(root, source(texts=(long, "补充：后续工作留到下一轮。"), minute=30), 30))
    await sup.supervise_once(root, NOW + timedelta(minutes=30), notify=notify, llm=model)
    state = sup._load(root)
    assert model.calls == 1 and not watch.load_actions(root)
    assert state["marks"]["thread-000001"]["review_status"] == "superseded"
    assert state["goal_reviews"][goal["id"]]["used"] == 1
    store(root, source(texts=(long, "补充：后续工作留到下一轮。"), minute=60), 60)
    await sup.supervise_once(root, NOW + timedelta(minutes=60), notify=notify, llm=model)
    assert model.calls == 1


@pytest.mark.parametrize("approved,busy", [(False, False), (True, False), (False, True), (True, True)])
def test_same_turn_correction_expires_draft_or_approved_action_even_when_cleanup_busy(setup, monkeypatch, approved, busy):
    root, _, _, _ = setup
    current = source(minute=5)
    store(root, current, 5)
    fingerprint = context.read(current["turn"]).fingerprint
    action = watch.propose_action(root, thread=current["id"], kind="message", text="补文档",
                                  expected_context_fingerprint=fingerprint)
    if approved:
        watch.decide_action(root, action["id"], True)
    if busy:
        def fail(*args):
            raise watch.CodexActionBusy("busy")
        monkeypatch.setattr(watch, "expire_stale_actions", fail)
    store(root, source(texts=("只写文档。", "补充：先暂停。"), minute=30), 30)
    with pytest.raises(watch.CodexActionError, match="材料|过时"):
        (watch.claim_action(root, action["id"]) if approved else watch.decide_action(root, action["id"], True))
    assert watch.load_actions(root)[0]["status"] == "expired"
    with pytest.raises(watch.CodexActionError, match="材料"):
        watch.propose_action(root, thread=current["id"], kind="message", text="旧建议", expected_context_fingerprint=fingerprint)


def test_manual_message_and_interrupt_keep_existing_human_control(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(watch, "_now", lambda: NOW)
    thread = source(active=True)
    del thread["turn"]["review_context"]
    store(root, thread, 0)
    watch.hand_over(root, watch.load_state(root)["threads"][0], now=NOW)
    manual = watch.propose_action(root, thread=thread["id"], kind="message", text="先暂停核对", session="owner-session")
    stop = watch.propose_action(root, thread=thread["id"], kind="interrupt")
    assert "context_fingerprint" not in manual and "context_fingerprint" not in stop
    watch.decide_action(root, stop["id"], True)
    assert watch.claim_action(root, stop["id"])["status"] == "sending"
    store(root, source(active=True, turn="t2", minute=1), 1)
    watch.decide_action(root, manual["id"], True)
    assert watch.claim_action(root, manual["id"])["status"] == "sending"


def test_incomplete_candidates_do_not_use_up_the_two_review_slots(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(watch, "load_handovers", lambda _: {f"thread-{n}": {"since": 1e18} for n in range(3)})
    threads = [source(tid=f"thread-{n}") for n in range(3)]
    threads[0]["turn"].pop("review_context")
    events = [{"thread_id": t["id"], "turn_id": "t1", "at": NOW.isoformat()} for t in threads]
    state = {"threads": threads, "events": events}
    assert sup._review_order(root, state, {}, NOW + timedelta(minutes=30)) == ["thread-1", "thread-2"]


def test_unknown_and_incomplete_fingerprints_are_stable_but_never_complete():
    legacy = {"id": "t1", "request": "旧观察员摘要"}
    assert context.read(legacy).fingerprint == context.read(deepcopy(legacy)).fingerprint
    invalid = {**legacy, "review_context": None}
    assert context.read(invalid).status == "unknown" and "版本" in context.read(invalid).reason


async def test_missing_context_does_not_even_construct_a_default_model(monkeypatch):
    from src.llm import client

    def forbidden():
        pytest.fail("incomplete observation constructed a model client")
    monkeypatch.setattr(client, "LLMClient", forbidden)
    thread = source()
    del thread["turn"]["review_context"]
    assert await sup.review_turn(thread) is None


@pytest.mark.parametrize("fault", ["legacy", "future", "truncated"])
def test_previously_approved_advice_cannot_use_incomplete_new_material(setup, fault):
    root, _, _, _ = setup
    thread = source(minute=5)
    store(root, thread, 5)
    action = watch.propose_action(root, thread=thread["id"], kind="message", text="补文档",
                                  expected_context_fingerprint=context.read(thread["turn"]).fingerprint)
    watch.decide_action(root, action["id"], True)
    if fault == "legacy":
        del thread["turn"]["review_context"]
    elif fault == "future":
        thread["turn"]["review_context"]["schema_version"] = 2
    else:
        thread["turn"]["review_context"] = context.capture("t1", [user("x" * 4097)])
    store(root, thread, 30)
    with pytest.raises(watch.CodexActionError):
        watch.claim_action(root, action["id"])
    assert watch.load_actions(root)[0]["status"] == "expired"


@pytest.mark.parametrize("approved", [False, True])
def test_persisted_automatic_advice_without_a_material_version_is_rejected(setup, approved):
    root, _, _, _ = setup
    thread = source(minute=5)
    store(root, thread, 5)
    action = watch.propose_action(root, thread=thread["id"], kind="message", text="旧版本建议")
    action.pop("context_fingerprint")
    action.pop("requires_complete_context")
    if approved:
        action["status"] = "approved"
    watch._save_actions(root, [action])
    with pytest.raises(watch.CodexActionError, match="材料版本"):
        (watch.claim_action(root, action["id"]) if approved else watch.decide_action(root, action["id"], True))
    assert watch.load_actions(root)[0]["status"] == "expired"
