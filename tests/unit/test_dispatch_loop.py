"""任务单下发之后的闭环：任务能问你、你在手机上答；做完带验证结论，按钮开 draft PR。全部离线。"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.im.bridge import IMBridge
from src.im.channel import ChannelEvent
from src.scheduling import assistant_watch as watch
from src.workflow.tasks import assistant_dispatch as dispatch
from tests.unit.test_im_bridge import OWNER, FakeAdapter, _drive_no_turn

NOW = datetime(2026, 10, 20, 9, 0)
QUESTION = {"id": "question-abc", "round": 1, "status": "open", "question": "超时改成 30 秒还是 60 秒？",
            "options": ["30 秒", "60 秒"], "context": "在 src/login.py 找到了超时配置", "expires_at": "2026-10-21T09:00:00"}


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    path = tmp_path / "resident_projects.json"
    path.write_text(json.dumps({"projects": [{"name": "VortoCode", "repo_root": "/srv/VortoCode", "port": 8082}]}))
    monkeypatch.setenv("VORTOCODE_RESIDENT_PROJECTS", str(path))


class Remote:
    """假的项目运行时：记下请求，按状态回任务视图。"""

    def __init__(self, task):
        self.task, self.calls = task, []

    async def __call__(self, method, url, payload=None):
        self.calls.append((method, url, payload))
        if method == "GET" and url.endswith("/api/tasks"):
            return {"submission_idempotency": 1, "submission_scope": {"repo_root": "/srv/VortoCode"}}
        if method == "POST" and url.endswith("/api/tasks"):
            return {"id": self.task["id"], "status": "queued"}
        if url.endswith("/review"):
            return {}
        if url.endswith("/answer"):
            return {"status": "queued"}
        if url.endswith("/open_pr"):
            return {"ok": True, "url": "https://github.com/x/y/pull/9"}
        return {**self.task, "execution_scope": {"repo_root": "/srv/VortoCode"}}


async def _sent(root, remote_status="running", **task):
    entry = dispatch.propose(root, project="VortoCode", title="改登录超时", goal="把超时改短")
    remote = Remote({"id": dispatch.submission_task_id(entry["request_id"]), "status": remote_status, **task})
    await dispatch.approve(root, entry["id"], http=remote)
    return entry, remote


async def test_dispatched_tasks_belong_to_their_own_session_so_they_can_ask(tmp_path):
    entry, remote = await _sent(str(tmp_path))
    post = next(payload for method, url, payload in remote.calls if method == "POST")
    assert post["session"] == "assistant-" + entry["id"][4:]


async def test_question_reaches_the_phone_and_the_answer_goes_back(tmp_path):
    root = str(tmp_path)
    entry, remote = await _sent(root)
    remote.task.update(status="blocked", questions=[QUESTION])
    item = (await dispatch.refresh(root, http=remote))[0]
    assert item["status"] == "waiting" and item["question"]["id"] == "question-abc"
    text = watch.status_message(item)
    assert "超时改成 30 秒还是 60 秒" in text and "1. 30 秒" in text and "直接回我答案" in text

    tools = {tool.name: tool for tool in dispatch.build_dispatch_tools(root)}
    import src.workflow.tasks.assistant_dispatch as module
    original = module._http
    module._http = remote
    try:
        reply = await tools["answer_dispatch_question"].handler({"dispatch_id": entry["id"], "answer": "30 秒"})
    finally:
        module._http = original
    assert "已把回答交给" in reply
    method, url, payload = remote.calls[-1]
    assert url.endswith(f"/api/tasks/{item['remote_task_id']}/answer")
    assert payload == {"session": "assistant-" + entry["id"][4:], "round": 1, "question_id": "question-abc", "answer": "30 秒"}
    after = dispatch.list_dispatches(root)[0]
    assert after["status"] == "running" and after["question"] is None and after["answers"][0]["answer"] == "30 秒"
    with pytest.raises(dispatch.DispatchError, match="没有在等回答"):
        await dispatch.answer_question(root, entry["id"], "60 秒", http=remote)


async def test_answers_are_not_relayed_from_a_tainted_turn_or_for_legacy_tasks(tmp_path):
    root = str(tmp_path)
    entry, remote = await _sent(root, remote_status="blocked", questions=[QUESTION])
    await dispatch.refresh(root, http=remote)
    tainted = {tool.name: tool for tool in dispatch.build_dispatch_tools(root, is_tainted=lambda: True)}
    assert "外部内容" in await tainted["answer_dispatch_question"].handler({"dispatch_id": entry["id"], "answer": "30 秒"})
    dispatch._update(root, entry["id"], lambda item: item.pop("remote_session"))
    with pytest.raises(dispatch.DispatchError, match="旧方式下发"):
        await dispatch.answer_question(root, entry["id"], "30 秒", http=remote)
    assert not any(url.endswith("/answer") for _, url, _ in remote.calls)


async def test_done_notice_offers_a_draft_pr_button(tmp_path):
    root = str(tmp_path)
    items = [{"id": "dsp-0123456789", "project": "VortoCode", "title": "改登录超时", "status": "running"}]

    async def refresh(_root):
        return items

    sent = []

    async def notify(text, confirm_id=""):
        sent.append((text, confirm_id))
        return True
    notify.accepts_confirm = True
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    items[0] = {**items[0], "status": "done", "branch": "vorto/auto-1", "result": "改好了",
                "delivery_evidence": {"verification": "passed"}}
    await watch.watch_once(root, NOW, notify=notify, refresh=refresh, times=[])
    text, confirm_id = sent[-1]
    assert confirm_id == "dpr-0123456789" and "自动验证通过" in text and "draft PR" in text


async def test_pr_button_opens_once_and_reports_errors(tmp_path, monkeypatch):
    root = str(tmp_path)
    entry, remote = await _sent(root, remote_status="done", branch="vorto/auto-1", result="ok")
    await dispatch.refresh(root, http=remote)
    opened = await dispatch.open_pr(root, entry["id"], http=remote)
    assert opened["pr_url"] == "https://github.com/x/y/pull/9"
    await dispatch.open_pr(root, entry["id"], http=remote)
    assert sum(url.endswith("/open_pr") for _, url, _ in remote.calls) == 1        # 已开过不再推

    other, _ = await _sent(root, remote_status="running")
    with pytest.raises(dispatch.DispatchError, match="成功做完"):
        await dispatch.open_pr(root, other["id"], http=remote)


async def test_bridge_pr_button(tmp_path, monkeypatch):
    seen = []
    import src.gateway.audit as audit
    monkeypatch.setattr(audit, "record_decision_audit", lambda root, **kw: seen.append(kw))

    async def fake_open(root, dispatch_id):
        seen.append(dispatch_id)
        return {"title": "改登录超时", "pr_url": "https://github.com/x/y/pull/9"}
    monkeypatch.setattr(dispatch, "open_pr", fake_open)
    adapter = FakeAdapter()
    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="test")
    click = lambda cid, ok, sender=OWNER: ChannelEvent(kind="callback", sender_id=sender, callback_id=cid,  # noqa: E731
                                                       approved=ok, ack="q")
    await _drive_no_turn(bridge, adapter, click("dpr-0123456789", True, "stranger"), click("dpr-0123456789", False),
                         click("dpr-0123456789", True))
    assert seen[0] == "dsp-0123456789" and len(seen) == 2                       # 陌生人点无效；拒绝不开
    assert any("pull/9" in text for text in adapter.texts()) and bridge._turn_task is None


async def test_retry_carries_the_last_failure_and_stops_after_three(tmp_path):
    root = str(tmp_path)
    first = dispatch.propose(root, project="VortoCode", title="补单测", goal="给 safe_id 补边界单测")
    dispatch._update(root, first["id"], lambda item: item.update(status="failed", error="自测未过：ImportError starlette",
                                                                 result="...\n集成红"))
    tools = {tool.name: tool for tool in dispatch.build_dispatch_tools(root)}
    reply = await tools["propose_task"].handler({"project": "VortoCode", "title": "补单测", "goal": "先装好测试依赖再补",
                                                  "retry_of": first["id"]})
    assert "已写好任务单" in reply
    second = dispatch.list_dispatches(root)[0]
    assert second["attempt"] == 2 and second["retry_of"] == first["id"]
    prompt = dispatch.task_prompt(second)
    assert "先装好测试依赖再补" in prompt and "第 2 次尝试" in prompt and "ImportError starlette" in prompt and "集成红" in prompt
    with pytest.raises(dispatch.DispatchError, match="失败或没做完"):
        dispatch.propose(root, project="VortoCode", title="x", goal="y", retry_of=second["id"])   # 还没失败
    dispatch._update(root, second["id"], lambda item: item.update(status="failed"))
    third = dispatch.propose(root, project="VortoCode", title="补单测", goal="拆小", retry_of=second["id"])
    dispatch._update(root, third["id"], lambda item: item.update(status="failed"))
    assert "建议换个做法" in watch.status_message(dispatch.list_dispatches(root)[0])
    with pytest.raises(dispatch.DispatchError, match="试了 3 次"):
        dispatch.propose(root, project="VortoCode", title="补单测", goal="再来", retry_of=third["id"])


async def test_forgotten_dispatches_get_one_nudge(tmp_path):
    from datetime import timedelta, timezone
    root = str(tmp_path)
    now = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
    stale = dispatch.propose(root, project="VortoCode", title="写好没确认", goal="g")
    old = dispatch.propose(root, project="VortoCode", title="一周前的", goal="g")
    dispatch._update(root, stale["id"], lambda item: item.update(created_at=(now - timedelta(hours=30)).isoformat()))
    dispatch._update(root, old["id"], lambda item: item.update(created_at=(now - timedelta(days=8)).isoformat()))
    long_run = {"id": "dsp-run", "project": "VortoCode", "title": "跑很久", "status": "running",
                "sent_at": (now - timedelta(hours=4)).isoformat()}

    async def refresh(_root):
        return [*dispatch.list_dispatches(root), long_run]

    sent = []

    async def notify(text, confirm_id=""):
        sent.append((text, confirm_id))
        return True
    notify.accepts_confirm = True
    await watch.watch_once(root, now, notify=notify, refresh=refresh, times=[])        # 首轮只记现状
    for minutes in (1, 2):
        await watch.watch_once(root, now + timedelta(minutes=minutes), notify=notify, refresh=refresh, times=[])
    texts = [text for text, _ in sent]
    assert sum("写好一天了" in text for text in texts) == 1
    assert [cid for text, cid in sent if "写好一天了" in text] == [stale["id"]]
    assert sum("已自动撤回" in text for text in texts) == 1
    assert {item["id"]: item["status"] for item in dispatch.list_dispatches(root)}[old["id"]] == "cancelled"
    assert sum("做了 4 小时还没结束" in text for text in texts) == 1
