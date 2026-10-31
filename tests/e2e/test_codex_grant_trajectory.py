"""Fixed offline authorization trajectories through the production observer and supervisor.

The model and remote execution are substitutes. These assert durable decisions
and control state, not model quality, delivery or a deployed resident host.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.scheduling import codex_goal, codex_observer as observer
from src.scheduling import codex_supervisor as supervisor, codex_watch as watch

T0 = datetime(2026, 10, 31, 10, tzinfo=timezone.utc)
TID = "thread-trajectory-1"


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    class Scenario:
        root = str(tmp_path)
        minute = 0

        def now(self):
            return T0 + timedelta(minutes=self.minute)

        def report(self, minute, *, active=False, turn="turn-1"):
            self.minute = minute
            thread = observer.summarize_thread(
                {"id": TID, "name": "离线交接回归", "cwd": "/synthetic/project",
                 "status": {"type": "active" if active else "idle"}},
                {"id": turn, "status": "inProgress" if active else "completed", "items": [
                    {"type": "userMessage", "content": [{"type": "text", "text": "本轮只核对文档，代码留待下一轮。"}]}]},
                observed_at=self.now().timestamp())
            watch.record_report(self.root, {"threads": [thread]}, now=self.now())
            return watch.load_state(self.root)["threads"][0]

        def regrant(self):
            assert watch.take_back(self.root, TID)
            # Even an identical wall-clock label cannot reuse the old grant.
            return watch.hand_over(self.root, watch.load_state(self.root)["threads"][0], now=T0)

    state = Scenario()
    monkeypatch.setattr(watch, "_now", state.now)
    return state


class Model:
    def __init__(self, *, verdict="continue", during_review=None):
        self.calls, self.verdict, self.during_review = 0, verdict, during_review

    async def chat(self, *args, **kwargs):
        self.calls += 1
        if self.during_review:
            self.during_review()
        return {"content": json.dumps({"verdict": self.verdict, "assessment": "旧交接里的判断", "next": "旧交接里的建议"})}


async def begin_review(state, model, notify):
    state.report(0, active=True)
    approvals = []

    async def confirm(text):
        approvals.append(text)
        return True

    tool = next(t for t in watch.build_codex_action_tools(state.root, confirm=confirm) if t.name == "codex_handover")
    assert "已交接" in await tool.handler({"thread": TID}) and len(approvals) == 1
    original = watch.load_handovers(state.root)[TID]
    await supervisor.supervise_once(state.root, T0, notify=notify, llm=model)
    state.report(5)
    state.minute = 30
    return original


@pytest.mark.parametrize("verdict", ["continue", "ask", "done"])
async def test_revoke_regrant_restart_does_not_adopt_or_repeat_an_old_review(scenario, verdict):
    notices = []

    async def notify(text, confirm_id=""):
        notices.append(text)
        return True

    model = Model(verdict=verdict, during_review=scenario.regrant)
    original = await begin_review(scenario, model, notify)
    await supervisor.supervise_once(scenario.root, scenario.now(), notify=notify, llm=model)
    current = watch.load_handovers(scenario.root)[TID]
    assert current["since"] == original["since"] and current["token"] != original["token"]
    assert not watch.load_actions(scenario.root) and model.calls == 1
    assert all("旧交接里" not in text for text in notices)
    mark = supervisor._load(scenario.root)["marks"][TID]
    assert mark["review_status"] == "superseded" and mark["reviewed_turn"] == "turn-1"
    # Reload persisted state, refresh the observation and run a later check.
    scenario.report(60)
    await supervisor.supervise_once(scenario.root, scenario.now(), notify=notify, llm=model)
    assert model.calls == 1 and not watch.load_actions(scenario.root)
    assert watch.load_handovers(scenario.root)[TID] == current


@pytest.mark.parametrize("kind", ["message", "interrupt"])
@pytest.mark.parametrize("phase", ["proposed", "approved", "sending", "unknown", "sent"])
@pytest.mark.parametrize("cleanup", [False, True])
def test_old_drafts_cannot_revive_and_claimed_work_is_not_replayed(scenario, kind, phase, cleanup):
    thread = scenario.report(0, active=kind == "interrupt")
    original = watch.hand_over(scenario.root, thread, now=T0)
    action = watch.propose_action(scenario.root, thread=TID, kind=kind, text="人工拟定的一句话", session="owner-session")
    if phase != "proposed":
        watch.decide_action(scenario.root, action["id"], True)
    if phase in {"sending", "unknown", "sent"}:
        watch.claim_action(scenario.root, action["id"])
    if phase in {"unknown", "sent"}:
        watch.finish_action(scenario.root, action["id"], ok=phase == "sent", uncertain=phase == "unknown")
    new_grant = scenario.regrant()
    assert new_grant["token"] != original["token"] and new_grant["since"] == original["since"]
    if cleanup:
        scenario.report(1, active=kind == "interrupt")
    persisted = watch.load_actions(scenario.root)
    expected = "expired" if cleanup and phase in {"proposed", "approved"} else phase
    assert len(persisted) == 1 and persisted[0]["status"] == expected
    if phase in {"proposed", "approved"}:
        with pytest.raises(watch.CodexActionError, match="交接|过时"):
            (watch.decide_action(scenario.root, action["id"], True) if phase == "proposed" else watch.claim_action(scenario.root, action["id"]))
    # Freshly proposed manual control still requires and can receive approval.
    fresh = watch.propose_action(scenario.root, thread=TID, kind=kind, text="按新交接重新拟定", session="owner-session")
    assert fresh["handover_fingerprint"] == codex_goal.digest(new_grant)
    assert fresh["handover_fingerprint"] != action["handover_fingerprint"]
    watch.decide_action(scenario.root, fresh["id"], True)
    assert watch.claim_action(scenario.root, fresh["id"])["status"] == "sending"


async def test_proposal_rechecks_the_grant_after_model_result_validation(scenario, monkeypatch):
    async def notify(*args, **kwargs):
        return True

    model = Model()
    await begin_review(scenario, model, notify)
    propose = watch.propose_action

    def changed(*args, **kwargs):
        scenario.regrant()
        return propose(*args, **kwargs)

    monkeypatch.setattr(watch, "propose_action", changed)
    await supervisor.supervise_once(scenario.root, scenario.now(), notify=notify, llm=model)
    assert not watch.load_actions(scenario.root) and model.calls == 1
    mark = supervisor._load(scenario.root)["marks"][TID]
    assert mark["review_status"] == "failed" and mark["review_error_code"] == "proposal_failed"
    scenario.report(60)
    await supervisor.supervise_once(scenario.root, scenario.now(), notify=notify, llm=model)
    assert model.calls == 1


async def test_done_cannot_release_a_new_grant_between_validation_and_release(scenario, monkeypatch):
    async def notify(*args, **kwargs):
        return True

    model = Model(verdict="done")
    original = await begin_review(scenario, model, notify)
    release = watch.take_back

    def changed(root, tid, **kwargs):
        # Inject the inter-process race immediately before the locked compare/remove.
        assert release(root, tid)
        watch.hand_over(root, watch.load_state(root)["threads"][0], now=T0)
        return release(root, tid, **kwargs)

    monkeypatch.setattr(watch, "take_back", changed)
    await supervisor.supervise_once(scenario.root, scenario.now(), notify=notify, llm=model)
    assert watch.load_handovers(scenario.root)[TID]["token"] != original["token"]
    assert model.calls == 1 and not watch.load_actions(scenario.root)
    assert supervisor._load(scenario.root)["marks"][TID]["review_status"] == "superseded"


async def test_stuck_interrupt_rechecks_the_grant_at_proposal_creation(scenario, monkeypatch):
    async def notify(*args, **kwargs):
        return True

    thread = scenario.report(0, active=True)
    watch.hand_over(scenario.root, thread, now=T0)
    await supervisor.supervise_once(scenario.root, T0, notify=notify)
    scenario.report(30, active=True)
    propose = watch.propose_action

    def changed(*args, **kwargs):
        scenario.regrant()
        return propose(*args, **kwargs)

    monkeypatch.setattr(watch, "propose_action", changed)
    await supervisor.supervise_once(scenario.root, scenario.now(), notify=notify)
    assert not watch.load_actions(scenario.root)
