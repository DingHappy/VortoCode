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


async def test_unanswered_questions_get_one_reminder_and_answering_restarts_the_clock(tmp_path):
    from datetime import timedelta, timezone
    root = str(tmp_path)
    entry, remote = await _sent(root, remote_status="blocked", questions=[QUESTION])
    await dispatch.refresh(root, http=remote)
    item = dispatch.list_dispatches(root)[0]
    seen_at = item["question"]["seen_at"]
    await dispatch.refresh(root, http=remote)
    assert dispatch.list_dispatches(root)[0]["question"]["seen_at"] == seen_at       # 同一个问题不重新计时

    now = datetime.fromisoformat(seen_at)
    now = (now if now.tzinfo else now.astimezone()).astimezone(timezone.utc)
    current = {"items": dispatch.list_dispatches(root)}

    async def refresh(_root):
        return current["items"]

    sent = []

    async def notify(text, confirm_id=""):
        sent.append(text)
        return True
    await watch.watch_once(root, now, notify=notify, refresh=refresh, times=[])         # 首轮只记现状
    for hours in (1, 7, 8):
        await watch.watch_once(root, now + timedelta(hours=hours), notify=notify, refresh=refresh, times=[])
    assert sum("还在等你回答" in text and "30 秒还是 60 秒" in text for text in sent) == 1

    resumed = {**current["items"][0], "status": "running", "question": None,
               "sent_at": (now - timedelta(hours=10)).isoformat(), "resumed_at": (now + timedelta(hours=8)).isoformat()}
    current["items"] = [resumed]
    await watch.watch_once(root, now + timedelta(hours=9), notify=notify, refresh=refresh, times=[])
    assert not any("还没结束" in text for text in sent)                                 # 从接着做的那刻算
    await watch.watch_once(root, now + timedelta(hours=12), notify=notify, refresh=refresh, times=[])
    assert sum("还没结束" in text for text in sent) == 1


async def test_pr_button_runs_in_the_background_and_double_clicks_open_once(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr("src.gateway.audit.record_decision_audit", lambda root, **kw: None)
    gate, calls = asyncio.Event(), []

    async def slow_open(root, dispatch_id):
        calls.append(dispatch_id)
        await gate.wait()
        return {"title": "改登录超时", "pr_url": "https://github.com/x/y/pull/9"}
    monkeypatch.setattr(dispatch, "open_pr", slow_open)
    adapter = FakeAdapter()
    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="test")
    click = ChannelEvent(kind="callback", sender_id=OWNER, callback_id="dpr-0123456789", approved=True, ack="q")
    await bridge._on_event(click)
    await bridge._on_event(click)                       # 推分支期间桥没被卡住，连点只开一次
    assert any("正在开" in text for text in adapter.texts())
    gate.set()
    await asyncio.gather(*bridge._pr_jobs.values())
    assert calls == ["dsp-0123456789"] and any("pull/9" in text for text in adapter.texts())
    assert not bridge._pr_jobs


async def test_desktop_opens_the_pr_in_the_background_once(tmp_path):
    import asyncio
    root = str(tmp_path)
    entry, remote = await _sent(root, remote_status="done", branch="vorto/fix-timeout", result="改好了")
    await dispatch.refresh(root, http=remote)
    gate = asyncio.Event()

    async def slow(method, url, payload=None):
        await gate.wait()
        return await remote(method, url, payload)
    first = dispatch.begin_open_pr(root, entry["id"], http=slow)
    again = dispatch.begin_open_pr(root, entry["id"], http=slow)              # 连点：只开一次
    assert first["pr_state"] == again["pr_state"] == "opening" and len(dispatch._PR_JOBS) == 1
    gate.set()
    await asyncio.gather(*dispatch._PR_JOBS)
    done = dispatch.list_dispatches(root)[0]
    assert done["pr_state"] == "opened" and done["pr_url"].endswith("/pull/9")
    assert sum(url.endswith("/open_pr") for _, url, _ in remote.calls) == 1


async def test_background_pr_failure_lands_on_the_card(tmp_path):
    import asyncio
    root = str(tmp_path)
    entry, remote = await _sent(root, remote_status="done", branch="vorto/fix-timeout", result="改好了")
    await dispatch.refresh(root, http=remote)

    async def refusing(method, url, payload=None):
        return {"ok": False, "error": "分支已被删"}
    dispatch.begin_open_pr(root, entry["id"], http=refusing)
    await asyncio.gather(*dispatch._PR_JOBS)
    item = dispatch.list_dispatches(root)[0]
    assert item["pr_state"] == "failed" and "分支已被删" in item["pr_error"]
    with pytest.raises(dispatch.DispatchError, match="成功做完"):
        dispatch.begin_open_pr(root, (await _sent(root))[0]["id"])


def test_card_routes_answer_and_open_pr_are_audited(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from src.gateway.audit import list_audit
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    from src.web.server import app
    seen = []

    async def fake_answer(root, dispatch_id, answer, **_kw):
        seen.append((dispatch_id, answer))
        return {"id": dispatch_id, "title": "改登录超时", "project": "VortoCode", "status": "running"}
    monkeypatch.setattr(dispatch, "answer_question", fake_answer)
    monkeypatch.setattr(dispatch, "begin_open_pr", lambda root, dispatch_id: {
        "id": dispatch_id, "title": "改登录超时", "project": "VortoCode", "pr_state": "opening"})
    client = TestClient(app)
    assert client.post("/api/dispatches/dsp-1/answer", json={"answer": "30 秒"}).json()["status"] == "running"
    assert client.post("/api/dispatches/dsp-1/answer", json={"answer": ""}).status_code == 422
    assert client.post("/api/dispatches/dsp-1/open_pr").json()["pr_state"] == "opening"
    assert seen == [("dsp-1", "30 秒")]
    operations = [item["operation"] for item in list_audit(str(tmp_path), 10) if item["category"] == "decision"]
    assert any("回答任务单的问题" in op for op in operations) and any("开 draft PR" in op for op in operations)


async def test_answers_become_project_experience_for_later_dispatches(tmp_path):
    root = str(tmp_path)
    entry, remote = await _sent(root, remote_status="blocked", questions=[QUESTION])
    await dispatch.refresh(root, http=remote)
    await dispatch.answer_question(root, entry["id"], "30 秒", http=remote)
    known = dispatch.project_answers(root, "vortocode")
    assert known[-1]["question"] == "超时改成 30 秒还是 60 秒？" and known[-1]["answer"] == "30 秒"

    later, later_remote = await _sent(root)
    post = next(payload for method, url, payload in later_remote.calls if method == "POST" and url.endswith("/api/tasks"))
    assert "以前的任务里问过、用户答过" in post["prompt"] and "答：30 秒" in post["prompt"]
    assert dispatch.list_dispatches(root)[0]["known_answers"][-1]["answer"] == "30 秒"     # 定在单子上，重试同一份 prompt

    tools = {tool.name: tool for tool in dispatch.build_dispatch_tools(root)}
    assert "1. 问：超时改成 30 秒还是 60 秒？ 答：30 秒" in await tools["dispatch_answer_memory"].handler({"project": "VortoCode"})
    tainted = {tool.name: tool for tool in dispatch.build_dispatch_tools(root, is_tainted=lambda: True)}
    assert "没有删" in await tainted["dispatch_answer_memory"].handler({"project": "VortoCode", "action": "forget"})
    assert "忘掉 VortoCode 记下的 1 条" in await tools["dispatch_answer_memory"].handler(
        {"project": "VortoCode", "action": "forget", "index": "1"})
    third, third_remote = await _sent(root)
    post = next(payload for method, url, payload in third_remote.calls if method == "POST" and url.endswith("/api/tasks"))
    assert "用户答过" not in post["prompt"]


def test_same_question_keeps_only_the_latest_answer_and_the_list_is_bounded(tmp_path):
    root = str(tmp_path)
    dispatch._remember_answer(root, "VortoCode", "超时多少？", "30 秒")
    dispatch._remember_answer(root, "VortoCode", "超时多少？", "60 秒")
    assert [row["answer"] for row in dispatch.project_answers(root, "VortoCode")] == ["60 秒"]
    for index in range(20):
        dispatch._remember_answer(root, "VortoCode", f"问题 {index}", "是")
    assert len(dispatch.project_answers(root, "VortoCode")) == dispatch.MAX_KNOWN_ANSWERS


async def test_refresh_records_how_far_the_pipeline_got(tmp_path):
    root = str(tmp_path)
    plan = {"status": "running", "branch": "vorto/x", "progress": {"landed": 1, "failed": 0, "running": 1, "pending": 1, "total": 3},
            "blocks": [{"title": "改超时", "status": "landed"}, {"title": "补单测" * 50, "status": "running"},
                       {"title": "更新文档", "status": "weird"}]}
    entry, remote = await _sent(root, remote_status="running", plan=plan, log=["拆出 3 块", "块 2 在跑"])
    await dispatch.refresh(root, http=remote)
    item = dispatch.list_dispatches(root)[0]
    progress = item["progress"]
    assert progress["total"] == 3 and progress["landed"] == 1 and progress["latest"] == "块 2 在跑"
    assert [step["status"] for step in progress["steps"]] == ["landed", "running", "pending"]   # 认不得的状态不照抄
    assert len(progress["steps"][1]["title"]) == 120
    assert dispatch.progress_line(item).startswith("1/3 步已落地，正在做：补单测")
    tools = {tool.name: tool for tool in dispatch.build_dispatch_tools(root)}
    assert "进度：1/3 步已落地" in await tools["list_dispatches"].handler({})

    remote.task.update(plan=None, log=["正在拆解任务"])
    await dispatch.refresh(root, http=remote)
    assert dispatch.progress_line(dispatch.list_dispatches(root)[0]) == "还在拆计划：正在拆解任务"


def test_long_running_nudge_says_where_it_is(tmp_path):
    item = {"progress": {"total": 4, "landed": 2, "failed": 1, "stage": "running", "latest": "",
                         "steps": [{"title": "补单测", "status": "running"}]}}
    assert dispatch.progress_line(item) == "2/4 步已落地，1 步失败，正在做：补单测"
    assert dispatch.progress_line({**item, "progress": {**item["progress"], "stage": "integration_failed", "steps": []}}) \
        == "2/4 步已落地，1 步失败，集成测试没过"
    assert dispatch.progress_line({}) == ""
