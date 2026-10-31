"""One resident goal across Codex turns, changes, approval, restart and acceptance.

All model/RPC results are substitutes; these are state/permission acceptance
tests, not evidence of real-model quality or a running Desktop deployment.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.agents.backends.execution import ExecutionRef, ExecutionSnapshot, ExecutionState
from src.scheduling import codex_goal as policy
from src.scheduling import codex_review_context as context
from src.scheduling import codex_supervisor as sup
from src.scheduling import codex_watch as watch
from src.workflow.workbench.personal_goals import PersonalGoals

T0 = datetime(2026, 10, 31, 10, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def report(root, minute, *, turn="t1", active=False, host="mac-1", tid="thread-000001", store=True):
    thread = {"id": tid, "name": "修复登录", "cwd": "/project/login", "status": "active" if active else "idle",
              "flags": [], "turn": {"id": turn, "status": "inProgress" if active else "completed",
              "request": "本轮修复登录",
              "review_context": context.capture(turn, [{"type": "userMessage", "content": [{"type": "text", "text": "本轮修复登录"}]}]), "reply": "全部做好了", "commands": 1, "files": ["login.py"], "file_count": 1}}
    thread["execution"] = ExecutionSnapshot(ExecutionRef("codex", host, turn, tid, turn, scope="runtime"),
                                           ExecutionState.RUNNING if active else ExecutionState.FINISHED,
                                           at(minute).timestamp()).to_dict()
    if not store:
        return thread
    watch.record_report(root, {"threads": [thread]}, now=at(minute))
    return watch.load_state(root)["threads"][0]


class Notes:
    def __init__(self):
        self.items = []

    async def __call__(self, text, confirm_id=""):
        self.items.append((text, confirm_id))
        return True


class Model:
    def __init__(self, *, verdict="continue", ids=None, failure="verification", callback=None):
        self.calls, self.messages = 0, []
        self.output = {"verdict": verdict, "assessment": "仍缺测试证据", "next": "补齐登录测试",
                       "criterion_ids": ids if ids is not None else ["criterion-1"], "failure_kind": failure}
        self.callback = callback

    async def chat(self, messages, **kwargs):
        self.calls += 1
        self.messages = messages
        if self.callback:
            self.callback()
        return {"content": json.dumps(self.output)}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = str(tmp_path)
    monkeypatch.setattr(watch, "_now", lambda: at(30))
    service = PersonalGoals(root)
    goal = service.create({"request_id": "login", "objective": "修复登录并验收",
                           "acceptance_criteria": ["登录测试通过", "重启后登录通过"], "next_action": "修复登录"})
    thread = report(root, 0, active=True)
    link = policy.binding(root, goal["id"], 1)
    watch.hand_over(root, thread, now=T0, goal=link)
    return root, service, goal, Notes()


async def first_review(setup, model):
    root, _, _, notes = setup
    await sup.supervise_once(root, T0, notify=notes, llm=model)
    report(root, 5)
    await sup.supervise_once(root, at(30), notify=notes, llm=model)


async def test_goal_is_visible_and_model_done_cannot_end_its_handover(setup):
    root, service, goal, _ = setup
    model = Model(verdict="done")
    await first_review(setup, model)
    assert model.calls == 1
    assert goal["objective"] in model.messages[1]["content"] and "criterion-2" in model.messages[1]["content"]
    assert watch.is_handed_over(root, "thread-000001") and not watch.load_actions(root)
    assert service.get(goal["id"])["status"] == "active"
    view = watch.progress_snapshot(root)["goal_supervision"][0]
    assert view["used"] == 1 and view["pending"] == ["criterion-1", "criterion-2"]
    assert goal["id"] in watch.overview(root, now=at(30))


@pytest.mark.parametrize("ids,failure", [([], "evidence"), (["invented"], "verification"),
    (["criterion-1", "criterion-1"], "implementation"), ([1], "evidence"),
    (["criterion-1"], "invented"), (["criterion-1"], []), ("criterion-1", "evidence")])
async def test_out_of_contract_or_malformed_findings_never_create_an_action(setup, ids, failure):
    root, _, _, _ = setup
    await first_review(setup, Model(ids=ids, failure=failure))
    assert not watch.load_actions(root)


async def test_repeated_gap_stops_even_when_model_rephrases_and_process_restarts(setup):
    root, _, goal, notes = setup
    model = Model()
    await first_review(setup, model)
    action = watch.load_actions(root)[0]
    watch.decide_action(root, action["id"], False)
    report(root, 35, turn="t2", active=True)
    report(root, 40, turn="t2")
    model.output.update(assessment="又一种措辞", next="换一种说法继续改测试")
    await sup.supervise_once(root, at(60), notify=notes, llm=model)
    assert model.calls == 2 and len(watch.load_actions(root)) == 1
    assert sup.goal_review_state(root)[goal["id"]]["repeats"] == 2
    report(root, 65, turn="t3", active=True)
    report(root, 70, turn="t3")
    # Fresh callable and disk reads model a new process; counters aren't in memory.
    restarted = Model()
    await sup.supervise_once(root, at(90), notify=Notes(), llm=restarted)
    assert restarted.calls == 0
    assert "连续两轮" in watch.goal_supervision(root, now=at(90))[0]["reason"]


async def test_allowance_is_shared_across_rehandover_and_sessions_and_not_refunded_on_failure(setup):
    root, _, goal, notes = setup

    def fail():
        raise RuntimeError("offline model failure")

    model = Model(callback=fail)
    await first_review(setup, model)
    for n in range(2, 6):
        watch.take_back(root, "thread-000001")
        thread = report(root, 30 * n - 25, turn=f"t{n}", active=True)
        watch.hand_over(root, thread, now=at(30 * n - 25), goal=policy.binding(root, goal["id"], 1))
        report(root, 30 * n - 20, turn=f"t{n}")
        await sup.supervise_once(root, at(30 * n), notify=notes, llm=model)
    assert model.calls == 4 and sup.goal_review_state(root)[goal["id"]]["used"] == 4
    assert not watch.load_actions(root)
    assert "用完" in watch.goal_supervision(root, now=at(150))[0]["reason"]


@pytest.mark.parametrize("change", ["goal", "revoke", "rebind", "host", "contract"])
async def test_inflight_changes_discard_decisions_without_replay(setup, change):
    root, service, goal, _ = setup

    def mutate():
        if change == "goal":
            service.update(goal["id"], {"revision": 1, "blocker": "用户延期", "next_action": "等待"})
        elif change == "revoke":
            watch.take_back(root, "thread-000001")
        elif change == "rebind":
            watch.hand_over(root, watch.load_state(root)["threads"][0], now=at(30),
                            goal=policy.binding(root, goal["id"], 1, 5))
        elif change == "host":
            report(root, 30, host="other-host")
        else:
            saved = service.ledger.load(goal["id"])
            saved.objective = "另一件事"
            service.ledger.save(saved)

    model = Model(callback=mutate)
    await first_review(setup, model)
    assert model.calls == 1 and not watch.load_actions(root)
    assert sup._load(root)["marks"]["thread-000001"]["review_status"] == "superseded"
    assert sup.goal_review_state(root)[goal["id"]]["used"] == 1


async def test_revision_change_invalidates_a_proposal_at_approval_and_claim(setup):
    root, service, goal, _ = setup
    await first_review(setup, Model())
    action = watch.load_actions(root)[0]
    service.update(goal["id"], {"revision": 1, "next_action": "等待用户"})
    with pytest.raises(watch.CodexActionError, match="变化"):
        watch.decide_action(root, action["id"], True)
    # An action approved before another goal change must also fail at the claim boundary.
    second = watch.propose_action(root, thread="thread-000001", kind="message", text="核对剩余验收")
    watch.decide_action(root, second["id"], True)
    service.update(goal["id"], {"revision": 2, "blocker": "暂停", "next_action": "等待"})
    with pytest.raises(watch.CodexActionError, match="变化"):
        watch.claim_action(root, second["id"])
    assert watch.load_actions(root)[1]["status"] == "expired"


async def test_explicit_scope_cannot_advance_deferred_criteria(setup):
    root, service, goal, notes = setup
    thread = watch.load_state(root)["threads"][0]
    watch.hand_over(root, thread, now=T0, goal=policy.binding(root, goal["id"], 1, criterion_ids=["criterion-1"]))
    await first_review(setup, Model(ids=["criterion-2"]))
    assert not watch.load_actions(root)
    service.evidence(goal["id"], "criterion-1", {"revision": 1, "confirmed": True, "passed": True, "summary": "用户验收测试"})
    report(root, 35, turn="t2", active=True)
    report(root, 40, turn="t2")
    model = Model(ids=["criterion-2"])
    await sup.supervise_once(root, at(60), notify=notes, llm=model)
    assert model.calls == 0 and watch.is_handed_over(root, "thread-000001")
    assert "不自动扩大范围" in watch.goal_supervision(root, now=at(60))[0]["reason"]


async def test_only_real_goal_acceptance_finishes_tracking_without_another_model(setup):
    root, service, goal, notes = setup
    model = Model(verdict="done")
    await first_review(setup, model)
    for n in (1, 2):
        service.evidence(goal["id"], f"criterion-{n}", {"revision": n, "confirmed": True, "passed": True,
                                                       "summary": f"用户核对标准{n}"})
    report(root, 45)
    await sup.supervise_once(root, at(60), notify=notes, llm=model)
    assert model.calls == 1 and not watch.is_handed_over(root, "thread-000001")
    assert service.get(goal["id"])["status"] == "achieved"


@pytest.mark.parametrize("corruption", ["used", "grant_limit", "repeats", "map", "handover", "waiting"])
async def test_corrupt_allowance_or_binding_never_resets_or_spends(setup, corruption):
    root, _, goal, notes = setup
    await first_review(setup, Model(verdict="ask"))
    if corruption == "handover":
        path = watch._handover_path(root)
        raw = json.loads(path.read_text())
        raw["thread-000001"]["goal"]["review_limit"] = True
    else:
        path = sup._path(root)
        raw = json.loads(path.read_text())
        if corruption == "waiting":
            raw["marks"]["thread-000001"]["goal_waiting"] = []
        elif corruption == "map":
            raw["goal_reviews"] = []
        else:
            raw["goal_reviews"][goal["id"]][corruption] = -1
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    model = Model()
    report(root, 40, turn="t2")
    with pytest.raises((OSError, watch.CodexActionError)):
        await sup.supervise_once(root, at(60), notify=notes, llm=model)
    assert model.calls == 0 and path.read_bytes() == before


async def test_tool_shows_goal_scope_and_bound_and_rechecks_after_confirmation(setup):
    root, service, goal, _ = setup
    watch.take_back(root, "thread-000001")
    questions = []

    async def confirm(text):
        questions.append(text)
        service.update(goal["id"], {"revision": 1, "next_action": "更改了计划"})
        return True

    tool = next(t for t in watch.build_codex_action_tools(root, confirm=confirm) if t.name == "codex_handover")
    result = await tool.handler({"thread": "thread-000001", "goal_id": goal["id"], "goal_revision": "1",
                                 "review_limit": "4", "criterion_ids": "criterion-1"})
    assert "没有交接" in result and not watch.load_handovers(root)
    assert "累计最多 4" in questions[0] and "criterion-1" in questions[0] and "重交接不清零" in questions[0]


async def test_explicit_higher_allowance_can_resume_a_stall_but_never_refunds_used_calls(setup, monkeypatch):
    root, _, goal, notes = setup
    await first_review(setup, Model())
    watch.decide_action(root, watch.load_actions(root)[0]["id"], False)
    report(root, 35, turn="t2", active=True)
    report(root, 40, turn="t2")
    await sup.supervise_once(root, at(60), notify=notes, llm=Model())
    assert sup.goal_review_state(root)[goal["id"]]["repeats"] == 2
    monkeypatch.setattr(watch, "_now", lambda: at(60))
    report(root, 60, turn="t2")
    asked = []

    async def confirm(text):
        asked.append(text)
        return True

    tool = next(t for t in watch.build_codex_action_tools(root, confirm=confirm) if t.name == "codex_handover")
    result = await tool.handler({"thread": "thread-000001", "goal_id": goal["id"], "goal_revision": "1", "review_limit": "5"})
    assert "已交接" in result and "累计最多 5" in asked[0]
    assert sup.goal_review_state(root)[goal["id"]]["used"] == 2
    report(root, 65, turn="t3", active=True)
    report(root, 70, turn="t3")
    model = Model()
    await sup.supervise_once(root, at(90), notify=notes, llm=model)
    assert model.calls == 1
    assert sup.goal_review_state(root)[goal["id"]]["used"] == 3


async def test_missing_supervisor_file_never_recreates_a_bound_goals_allowance(setup):
    root, _, goal, notes = setup
    await first_review(setup, Model(verdict="ask"))
    sup._path(root).unlink()
    report(root, 40, turn="t2")
    model = Model()
    await sup.supervise_once(root, at(60), notify=notes, llm=model)
    report(root, 80, turn="t2")
    await sup.supervise_once(root, at(90), notify=notes, llm=model)
    assert model.calls == 0 and goal["id"] not in sup.goal_review_state(root)
    assert "额度记录缺失" in watch.goal_supervision(root, now=at(90))[0]["reason"]
    with pytest.raises(OSError, match="缺失"):
        watch.hand_over(root, watch.load_state(root)["threads"][0], now=at(90),
                        goal=policy.binding(root, goal["id"], 1))


async def test_forged_achieved_status_without_user_evidence_never_ends_tracking(setup):
    root, service, goal, _ = setup
    saved = service.ledger.load(goal["id"])
    saved.status = "achieved"
    service.ledger.save(saved)
    model = Model(verdict="done")
    await first_review(setup, model)
    assert model.calls == 1 and watch.is_handed_over(root, "thread-000001")
    assert watch.goal_supervision(root, now=at(30))[0]["state"] != "confirmed"


async def test_moving_a_goal_to_another_session_retains_its_allowance(setup):
    root, _, goal, notes = setup
    await first_review(setup, Model(verdict="ask"))
    watch.take_back(root, "thread-000001")
    thread = report(root, 35, turn="u1", tid="thread-000002", active=True)
    watch.hand_over(root, thread, now=at(35), goal=policy.binding(root, goal["id"], 1))
    report(root, 40, turn="u1", tid="thread-000002")
    await sup.supervise_once(root, at(60), notify=notes, llm=Model(verdict="ask"))
    assert sup.goal_review_state(root)[goal["id"]]["used"] == 2


def test_a_goal_cannot_have_two_handover_owners_and_legacy_observations_cannot_bind(setup):
    root, _, goal, _ = setup
    thread = report(root, 5, tid="thread-000002", active=True)
    with pytest.raises(watch.CodexActionError, match="另一个会话"):
        watch.hand_over(root, thread, now=at(5), goal=policy.binding(root, goal["id"], 1))
    watch.take_back(root, "thread-000001")
    del thread["execution"]
    watch.record_report(root, {"threads": [thread]}, now=at(10))
    with pytest.raises(watch.CodexActionError, match="宿主身份"):
        watch.hand_over(root, thread, now=at(10), goal=policy.binding(root, goal["id"], 1))


@pytest.mark.parametrize("field,value", [("source", "agent_reply"), ("kind", "model_summary"),
                                         ("summary", ""), ("stale_reason", "版本已变化")])
async def test_non_user_or_stale_evidence_never_confirms_a_goal(setup, field, value):
    root, service, goal, _ = setup
    for n in (1, 2):
        service.evidence(goal["id"], f"criterion-{n}", {"revision": n, "confirmed": True, "passed": True,
                                                       "summary": f"用户核对标准{n}"})
    saved = service.ledger.load(goal["id"])
    setattr(saved.evidence[-1], field, value)
    service.ledger.save(saved)
    await first_review(setup, Model(verdict="done"))
    assert watch.is_handed_over(root, "thread-000001")
    assert not policy.snapshot(root, goal["id"])["confirmed"]


async def test_project_flow_through_failed_acceptance_new_evidence_and_final_completion(setup, monkeypatch):
    root, service, goal, notes = setup
    model = Model()
    await first_review(setup, model)
    first = watch.load_actions(root)[0]
    assert first["status"] == "proposed" and first["goal_id"] == goal["id"]
    watch.decide_action(root, first["id"], True)
    watch.claim_action(root, first["id"])
    watch.finish_action(root, first["id"], ok=True)  # accepted, not finished/verified
    assert service.get(goal["id"])["status"] == "active"
    report(root, 35, turn="t2", active=True)
    report(root, 40, turn="t2")
    service.evidence(goal["id"], "criterion-1", {"revision": 1, "confirmed": True, "passed": False,
                                                "summary": "用户复测仍然失败"})
    await sup.supervise_once(root, at(60), notify=notes, llm=model)
    assert model.calls == 1 and len(watch.load_actions(root)) == 1
    assert watch.goal_supervision(root, now=at(60))[0]["state"] == "paused"
    # User checks a repaired version in Codex, then records fresh acceptance.
    service.evidence(goal["id"], "criterion-1", {"revision": 2, "confirmed": True, "passed": True,
                                                "summary": "用户复测修复后的登录测试"})
    report(root, 65, turn="t3", active=True)
    report(root, 75, turn="t3")
    model.output["criterion_ids"] = ["criterion-2"]
    await sup.supervise_once(root, at(90), notify=notes, llm=model)
    assert model.calls == 2 and sup.goal_review_state(root)[goal["id"]]["used"] == 2
    monkeypatch.setattr(watch, "_now", lambda: at(90))
    second = watch.load_actions(root)[-1]
    watch.decide_action(root, second["id"], True)
    watch.claim_action(root, second["id"])
    watch.finish_action(root, second["id"], ok=True)
    service.evidence(goal["id"], "criterion-2", {"revision": 3, "confirmed": True, "passed": True,
                                                "summary": "用户核对重启后的登录"})
    report(root, 100, turn="t3")
    await sup.supervise_once(root, at(120), notify=notes, llm=model)
    assert model.calls == 2 and not watch.is_handed_over(root, "thread-000001")
    assert service.get(goal["id"])["status"] == "achieved"


async def test_paused_goals_do_not_starve_other_sessions_of_the_two_review_slots(tmp_path):
    root, notes, service = str(tmp_path), Notes(), PersonalGoals(str(tmp_path))
    active = [report(root, 0, tid=f"thread-{n:06d}", active=True) for n in (1, 2, 3)]
    watch.record_report(root, {"threads": active}, now=T0)
    for n, thread in enumerate(active, start=1):
        if n == 3:
            watch.hand_over(root, thread, now=T0)
            continue
        goal = service.create({"request_id": f"goal-{n}", "objective": "修复登录", "acceptance_criteria": ["通过"],
                               "next_action": "修复"})
        watch.hand_over(root, thread, now=T0, goal=policy.binding(root, goal["id"], 1))
        service.update(goal["id"], {"revision": 1, "blocker": "延期", "next_action": "等待"})
    model = Model(verdict="ask")
    await sup.supervise_once(root, T0, notify=notes, llm=model)
    done = [report(root, 5, tid=t["id"], store=False) for t in active]
    watch.record_report(root, {"threads": done}, now=at(5))
    await sup.supervise_once(root, at(30), notify=notes, llm=model)
    assert model.calls == 1
    assert sup._load(root)["marks"]["thread-000003"]["review_status"] == "succeeded"


async def test_concurrent_approval_cannot_restore_an_already_claimed_action(setup, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    root, _, _, _ = setup
    report(root, 5)
    first = watch.propose_action(root, thread="thread-000001", kind="message", text="人工确认的当前验收范围", session="owner-session")
    watch.decide_action(root, first["id"], True)
    second = watch.propose_action(root, thread="thread-000001", kind="message", text="核对当前验收范围", session="owner-session")
    entered, release = threading.Event(), threading.Event()
    save = watch._save_actions

    def pause_approval(root, items):
        if threading.current_thread().name.startswith("approval"):
            entered.set()
            assert release.wait(5), "approval was never released"
        return save(root, items)

    monkeypatch.setattr(watch, "_save_actions", pause_approval)
    rejected = False
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="approval") as pool:
        approval = pool.submit(watch.decide_action, root, second["id"], True)
        try:
            assert entered.wait(3)
            try:
                watch.claim_action(root, first["id"])
            except watch.CodexActionError:
                rejected = True
        finally:
            release.set()
            approval.result(timeout=5)
    if rejected:
        watch.claim_action(root, first["id"])
    assert watch.load_actions(root)[0]["status"] == "sending"
    with pytest.raises(watch.CodexActionError):
        watch.claim_action(root, first["id"])


async def test_model_done_reason_remains_visible_after_a_fresh_status_read(setup):
    root, _, _, _ = setup
    await first_review(setup, Model(verdict="done"))
    view = watch.progress_snapshot(root)["goal_supervision"][0]
    assert view["state"] == "awaiting_user"
    assert "验收" in view["reason"] and "本轮" in view["reason"]


@pytest.mark.parametrize("change", ["revision", "turn", "rebind", "revoke"])
async def test_waiting_reason_is_only_for_its_original_goal_turn_and_handover(setup, change):
    root, service, goal, _ = setup
    await first_review(setup, Model(verdict="done"))
    if change == "revision":
        service.update(goal["id"], {"revision": 1, "next_action": "用户已调整下一步"})
    elif change == "turn":
        report(root, 30, turn="t2")
    elif change == "rebind":
        watch.hand_over(root, watch.load_state(root)["threads"][0], now=at(30),
                        goal=policy.binding(root, goal["id"], 1))
    else:
        watch.take_back(root, "thread-000001")
    projected = watch.progress_snapshot(root)["goal_supervision"]
    assert not projected if change == "revoke" else projected[0]["state"] == "active"


@pytest.mark.parametrize("operation", ["propose", "approve", "cancel", "claim", "finish", "expire", "settle"])
async def test_all_action_writers_share_the_same_ledger_lock(setup, operation):
    root, _, _, _ = setup
    await first_review(setup, Model())
    first = watch.load_actions(root)[0]
    watch.decide_action(root, first["id"], True)
    watch.claim_action(root, first["id"])
    second = watch.propose_action(root, thread="thread-000001", kind="message", text="核对验收")
    third = watch.propose_action(root, thread="thread-000001", kind="message", text="补充验证材料")
    watch.decide_action(root, third["id"], True)
    path = watch._actions_path(root)
    before = path.read_bytes()
    operations = {
        "propose": lambda: watch.propose_action(root, thread="thread-000001", kind="message", text="不能覆盖状态"),
        "approve": lambda: watch.decide_action(root, second["id"], True),
        "cancel": lambda: watch.decide_action(root, second["id"], False),
        "claim": lambda: watch.claim_action(root, third["id"]),
        "finish": lambda: watch.finish_action(root, first["id"], ok=True),
        "expire": lambda: watch.expire_stale_actions(root),
        "settle": lambda: watch.settle_unconfirmed_actions(root, now=at(60)),
    }
    from src.workflow.pipeline.pipeline_storage import pipeline_lock
    with pipeline_lock(root, "codex-actions", kind="state"):
        if operation == "settle":
            assert operations[operation]() == []
        else:
            with pytest.raises(watch.CodexActionBusy):
                operations[operation]()
    assert path.read_bytes() == before


async def test_report_can_commit_while_action_writes_are_busy_but_claim_rechecks_the_new_turn(setup):
    root, _, _, _ = setup
    await first_review(setup, Model())
    action = watch.load_actions(root)[0]
    watch.decide_action(root, action["id"], True)
    from src.workflow.pipeline.pipeline_storage import pipeline_lock
    with pipeline_lock(root, "codex-actions", kind="state"):
        report(root, 30, turn="t2")
    assert watch.load_actions(root)[0]["status"] == "approved"
    with pytest.raises(watch.CodexActionError, match="新的一轮"):
        watch.claim_action(root, action["id"])
    assert watch.load_actions(root)[0]["status"] == "expired"
