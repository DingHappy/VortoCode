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
    assert await review.follow_up(root, done, boom) is done
    unsure = review.parse_outcome(json.dumps({"criteria": [{"text": "x", "verdict": "probably"}]}), done)
    assert unsure is None                                                   # 读不懂的判定不当真
