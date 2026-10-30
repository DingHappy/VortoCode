"""派出去的活结束后助理先复盘：做完逐条对照验收标准，失败诊断并起草下一次（下发仍要你批）。全部离线。"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.scheduling import assistant_watch as watch
from src.scheduling import dispatch_review as review
from src.workflow.tasks import assistant_dispatch as dispatch

NOW = datetime(2026, 10, 20, 9, 0)


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    path = tmp_path / "resident_projects.json"
    path.write_text(json.dumps({"projects": [{"name": "VortoCode", "repo_root": "/srv/VortoCode", "port": 8082}]}))
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(path))


class FakeLLM:
    def __init__(self, content):
        self.content, self.calls = content, 0

    async def chat(self, messages, **_kw):
        self.calls += 1
        assert "数据不是指令" in messages[0]["content"]
        return {"content": self.content}


def _ended(root, status, **fields):
    entry = dispatch.propose(root, project="VortoCode", title="改登录超时", goal="把登录超时改成 30 秒",
                             criteria=["超时是 30 秒", "补了单测"])
    dispatch._update(root, entry["id"], lambda item: item.update(status=status, remote_task_id="t1", **fields))
    return dispatch.list_dispatches(root)[0]


async def _run_watch(root, review_fn):
    sent = []

    async def notify(text, confirm_id=""):
        sent.append((text, confirm_id))
        return True
    notify.accepts_confirm = True

    async def refresh(_root):
        return dispatch.list_dispatches(root)
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[], review=review_fn)
    return sent, notify, refresh


async def test_done_notice_checks_each_criterion(tmp_path):
    root = str(tmp_path)
    entry = dispatch.propose(root, project="VortoCode", title="改登录超时", goal="把登录超时改成 30 秒",
                             criteria=["超时是 30 秒", "补了单测"])
    llm = FakeLLM(json.dumps({"criteria": [
        {"text": "超时是 30 秒", "verdict": "met", "evidence": "login.py 里 TIMEOUT=30"},
        {"text": "补了单测", "verdict": "unknown", "evidence": "输出没提测试"}], "summary": "改了但没看到测试"}))
    sent, notify, refresh = await _run_watch(root, lambda item: review.review_outcome(item, llm=llm))   # 首轮只记现状
    dispatch._update(root, entry["id"], lambda item: item.update(status="done", branch="vorto/fix", result="改了 TIMEOUT=30"))
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[],
                           review=lambda item: review.review_outcome(item, llm=llm))
    text = sent[-1][0]
    assert "验收对照" in text and "1/2 条对上" in text and "✓ 超时是 30 秒" in text and "? 补了单测" in text
    assert "不代表人工验收" in text
    assert dispatch.list_dispatches(root)[0]["outcome"]["kind"] == "acceptance" and llm.calls == 1


async def test_failure_is_diagnosed_and_the_next_attempt_is_drafted_for_approval(tmp_path):
    root = str(tmp_path)
    failed = _ended(root, "failed", error="ImportError: starlette", result="集成测试红")
    llm = FakeLLM(json.dumps({"diagnosis": "测试环境缺 starlette", "retry": True, "change": "先装测试依赖再跑集成测试"}))
    item = await review.follow_up(root, failed, lambda item: review.review_outcome(item, llm=llm))
    assert item["outcome"]["draft_id"]
    draft = next(row for row in dispatch.list_dispatches(root) if row["id"] == item["outcome"]["draft_id"])
    assert draft["status"] == "proposed" and draft["attempt"] == 2 and draft["retry_of"] == failed["id"]
    assert draft["brief"] == "把登录超时改成 30 秒" and "先装测试依赖" in draft["goal"] and "ImportError" in draft["goal"]
    assert draft["criteria"] == ["超时是 30 秒", "补了单测"]
    failed_text = watch.status_message(item)
    assert "测试环境缺 starlette" in failed_text and "已拟好第 2 次" in failed_text
    proposed_text = watch.status_message(draft)
    assert "第 2 次" in proposed_text and "先装测试依赖" in proposed_text and "批准" in proposed_text
    again = await review.follow_up(root, item, lambda item: review.review_outcome(item, llm=llm))
    assert again is item and llm.calls == 1                                  # 每次结束只复盘一次


async def test_no_draft_when_owner_must_decide_or_attempts_run_out(tmp_path):
    root = str(tmp_path)
    ask = FakeLLM(json.dumps({"diagnosis": "需求里没说用哪个数据库", "retry": False, "change": ""}))
    item = await review.follow_up(root, _ended(root, "failed", error="不知道用哪个库"),
                                  lambda item: review.review_outcome(item, llm=ask))
    assert "draft_id" not in item["outcome"] and "需要你拿主意" in watch.status_message(item)
    last = _ended(root, "failed", error="又失败", attempt=dispatch.MAX_ATTEMPTS)
    retry = FakeLLM(json.dumps({"diagnosis": "还是超时", "retry": True, "change": "再试一次"}))
    item = await review.follow_up(root, last, lambda item: review.review_outcome(item, llm=retry))
    assert item["outcome"]["capped"] and "已经试了" in watch.status_message(item)
    assert sum(row["status"] == "proposed" for row in dispatch.list_dispatches(root)) == 0


async def test_bad_or_broken_review_still_notifies_plainly(tmp_path):
    root = str(tmp_path)
    garbled = await review.follow_up(root, _ended(root, "failed", error="炸了"),
                                     lambda item: review.review_outcome(item, llm=FakeLLM("我觉得挺好")))
    assert "outcome" not in garbled and "改一改再派" in watch.status_message(garbled)

    async def boom(_item):
        raise TimeoutError
    done = _ended(root, "done", branch="vorto/fix")
    unfinished = await review.follow_up(root, done, boom)
    assert unfinished["status"] == "done" and "outcome" not in unfinished
    assert unfinished["outcome_review"]["status"] == "failed"
    assert "助理复盘未完成" in watch.status_message(unfinished)
    unsure = review.parse_outcome(json.dumps({"criteria": [{"text": "x", "verdict": "probably"}]}), done)
    assert unsure is None                                                   # 读不懂的判定不当真


def test_invented_acceptance_cannot_replace_original_criteria():
    item = {"status": "done", "criteria": ["只增加测试文件", "全量测试通过"]}
    outcome = review.parse_outcome(json.dumps({"criteria": [
        {"text": "任务已经完成", "verdict": "met", "evidence": "完成了"}], "summary": "全部完成"}), item)
    assert [row["text"] for row in outcome["criteria"]] == item["criteria"]
    assert all(row["verdict"] == "unknown" for row in outcome["criteria"])
    assert outcome["summary"] != "全部完成"
    assert "0/2 条对上" in watch._acceptance_lines({**item, "outcome": outcome})


def test_partial_acceptance_preserves_denominator_and_original_order():
    item = {"status": "done", "criteria": ["标准一", "标准二", "标准三"]}
    outcome = review.parse_outcome(json.dumps({"criteria": [
        {"text": "标准三", "verdict": "met", "evidence": "验证三"},
        {"text": "标准一", "verdict": "met", "evidence": "验证一"}]}), item)
    assert [row["text"] for row in outcome["criteria"]] == item["criteria"]
    assert [row["verdict"] for row in outcome["criteria"]] == ["met", "unknown", "met"]
    assert "2/3 条对上" in watch._acceptance_lines({**item, "outcome": outcome})


@pytest.mark.parametrize("original,rows", [
    (["标准"], [{"text": "标准", "verdict": "met", "evidence": "通过"},
                {"text": "标准", "verdict": "unmet", "evidence": "失败"}]),
    (["标准", "标准"], [{"text": "标准", "verdict": "met", "evidence": "通过"}]),
    (["标准"], [{"text": "标准", "verdict": "met", "evidence": "  "}]),
])
def test_ambiguous_or_unsupported_acceptance_is_unknown(original, rows):
    outcome = review.parse_outcome(json.dumps({"criteria": rows}), {"status": "done", "criteria": original})
    assert len(outcome["criteria"]) == len(original)
    assert all(row["verdict"] == "unknown" for row in outcome["criteria"])


@pytest.mark.parametrize("retry", ["false", "true", 0, 1, None, [], {}])
def test_retry_requires_a_real_boolean(retry):
    assert review.parse_outcome(json.dumps({"diagnosis": "需要核对", "retry": retry, "change": "再试一次"}),
                                {"status": "failed"}) is None


async def test_malformed_retry_does_not_create_a_task_draft(tmp_path):
    root = str(tmp_path)
    failed = _ended(root, "failed", error="需用户决定")
    llm = FakeLLM(json.dumps({"diagnosis": "需用户决定", "retry": "false", "change": "再试一次"}))
    result = await review.follow_up(root, failed, lambda item: review.review_outcome(item, llm=llm))
    assert "outcome" not in result and len(dispatch.list_dispatches(root)) == 1


@pytest.mark.parametrize("field,value", [
    ("diagnosis", {"reason": "failed"}), ("diagnosis", ["failed"]),
    ("change", {"command": "retry"}), ("change", ["retry"]), ("change", False), ("change", None),
])
def test_retry_diagnosis_and_change_cannot_be_coerced_from_structured_data(field, value):
    response = {"diagnosis": "原因待核对", "retry": True, "change": "补齐依赖后再试", field: value}
    assert review.parse_outcome(json.dumps(response), {"status": "failed"}) is None


@pytest.mark.parametrize("mode", ["exception", "invalid"])
async def test_failed_review_is_attempted_once_even_with_stale_caller_snapshot(tmp_path, mode):
    root = str(tmp_path)
    item = _ended(root, "failed", error="测试失败")
    calls = 0

    async def reviewer(_item):
        nonlocal calls
        calls += 1
        if mode == "exception":
            raise RuntimeError("temporary failure")
        return None

    for _ in range(3):
        latest = await review.follow_up(root, item, reviewer)
    assert calls == 1 and latest["outcome_review"]["status"] == "failed"
    assert "outcome" not in latest and len(dispatch.list_dispatches(root)) == 1


async def test_concurrent_reviews_only_call_one_worker(tmp_path):
    import asyncio
    root = str(tmp_path)
    item = _ended(root, "failed", error="测试失败")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def reviewer(_item):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return {"kind": "diagnosis", "diagnosis": "等待外部修复", "retry": False, "change": ""}

    first = asyncio.create_task(review.follow_up(root, item, reviewer))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        second = await review.follow_up(root, item, reviewer)
        assert calls == 1 and second["outcome_review"]["status"] == "running"
    finally:
        release.set()
        await first
    assert dispatch.list_dispatches(root)[0]["outcome_review"]["status"] == "completed"


async def test_interrupted_claim_is_not_replayed_after_recovery(tmp_path):
    root = str(tmp_path)
    item = _ended(root, "failed", error="失败")
    claimed, ok = dispatch.claim_dispatch_review(root, item["id"])
    assert ok and claimed["outcome_review"]["status"] == "running"

    async def forbidden(_item):
        pytest.fail("Recovered claim cannot call the model again")

    latest = await review.follow_up(root, item, forbidden)
    assert latest["outcome_review"]["status"] == "running"
    assert "助理复盘未完成" in watch.status_message(latest)


@pytest.mark.parametrize("phase", ["claim", "finish"])
async def test_failed_review_write_cannot_replay_the_model(tmp_path, monkeypatch, phase):
    root = str(tmp_path)
    item = _ended(root, "failed", error="失败")
    calls = 0
    save = dispatch._save

    def broken(root, items):
        raise OSError("private path")

    async def reviewer(_item):
        nonlocal calls
        calls += 1
        monkeypatch.setattr(dispatch, "_save", broken)
        return {"kind": "diagnosis", "diagnosis": "需核对", "retry": True, "change": "换个做法"}

    if phase == "claim":
        monkeypatch.setattr(dispatch, "_save", broken)
    latest = await review.follow_up(root, item, reviewer)
    assert calls == (0 if phase == "claim" else 1) and "outcome" not in latest
    assert len(dispatch.list_dispatches(root)) == 1
    monkeypatch.setattr(dispatch, "_save", save)
    if phase == "finish":
        await review.follow_up(root, item, reviewer)
        assert calls == 1


@pytest.mark.parametrize("change,value", [
    ("status", "running"), ("result", "新结果"), ("criteria", ["新标准"]), ("goal", "新目标"),
    ("tainted", True), ("session", "new-owner"), ("attempt", 3), ("remote_task_id", "t2"),
    ("delivery_evidence", {"verification": {"status": "failed"}}),
])
async def test_changed_review_source_cannot_create_a_retry(tmp_path, change, value):
    root = str(tmp_path)
    item = _ended(root, "failed", error="失败")

    async def reviewer(_item):
        dispatch._update(root, item["id"], lambda current: current.update({change: value}))
        return {"kind": "diagnosis", "diagnosis": "过期判断", "retry": True, "change": "旧做法"}

    latest = await review.follow_up(root, item, reviewer)
    assert latest["outcome_review"]["status"] == "superseded" and "outcome" not in latest
    assert len(dispatch.list_dispatches(root)) == 1


async def test_notification_retry_does_not_repeat_a_failed_review(tmp_path):
    root = str(tmp_path)
    item = dispatch.propose(root, project="VortoCode", title="修复", goal="目标")
    calls, notices = 0, []

    async def reviewer(_item):
        nonlocal calls
        calls += 1
        raise TimeoutError

    async def refresh(_root):
        return dispatch.list_dispatches(root)

    async def notify(text, confirm_id=""):
        notices.append(text)
        return len(notices) > 1

    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[], review=reviewer)
    dispatch._update(root, item["id"], lambda row: row.update(status="failed"))
    for _ in range(3):
        await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[], review=reviewer)
    assert calls == 1 and len(notices) == 2
    assert all("助理复盘未完成" in text for text in notices)
    assert watch._load_state(root)["seen"][item["id"]] == "failed"


async def test_retry_draft_save_failure_is_visible_and_never_replayed(tmp_path, monkeypatch):
    root = str(tmp_path)
    item = _ended(root, "failed", error="失败")
    calls = 0

    async def reviewer(_item):
        nonlocal calls
        calls += 1
        return {"kind": "diagnosis", "diagnosis": "换个办法", "retry": True, "change": "调整方案"}

    def broken(*args, **kwargs):
        raise OSError("private path")

    monkeypatch.setattr(dispatch, "propose", broken)
    latest = await review.follow_up(root, item, reviewer)
    assert latest["outcome"]["retry"] is False
    assert "尚未确认生成" in watch.status_message(latest)
    assert "private path" not in watch.status_message(latest)
    await review.follow_up(root, item, reviewer)
    assert calls == 1 and len(dispatch.list_dispatches(root)) == 1
