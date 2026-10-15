"""主要聊天：手机（Telegram）与电脑（Desktop）共用一段对话。

钉住：登记规则（先到先得、格式校验）、同一时刻只跑一个回合（外部入口与 Web 回合同一槽位）、
IM 回合拿主要聊天的最新历史跑并交回、对话记录只在主要聊天记一份、Desktop 实时收到更新。
"""
from __future__ import annotations

import asyncio

import pytest

from src.gateway import main_conversation as mc
from src.gateway.session_actor import ActorContext, SessionActors


@pytest.fixture(autouse=True)
def _restore_host():
    previous = mc._HOST
    yield
    mc.set_main_conversation_host(previous)


# —— 登记 ——

def test_claim_first_wins_and_validates(tmp_path):
    root = str(tmp_path)
    assert mc.main_chat_key(root) is None
    with pytest.raises(ValueError):
        mc.claim_main_chat(root, "sid-../../etc")
    assert mc.claim_main_chat(root, "sid-desk1") == "sid-desk1"
    assert mc.claim_main_chat(root, "sid-desk2") == "sid-desk1"      # 后连上的 Desktop 改用已登记的那段
    (tmp_path / ".vortocode" / "main_chat.json").write_text("{坏的", encoding="utf-8")
    assert mc.main_chat_key(root) is None


def test_no_target_without_host_or_claim(tmp_path):
    mc.set_main_conversation_host(None)
    mc.claim_main_chat(str(tmp_path), "sid-a")
    assert mc.shared_turn_target(str(tmp_path)) is None
    mc.set_main_conversation_host(lambda **_: None)
    assert mc.shared_turn_target(str(tmp_path / "other")) is None


# —— 同一槽位、轮流执行 ——

def _context(actors: SessionActors, store: dict) -> ActorContext:
    async def publish():
        store.setdefault("published", 0)
        store["published"] += 1

    async def execute(item):
        store.setdefault("ran", []).append(item["id"])

    return ActorContext(key="sid-x", get_session=lambda: store.setdefault("session", {"prompt_queue": []}),
                        touch=lambda: None, persist=lambda: None, publish=publish, execute=execute)


async def test_external_turn_waits_for_running_turn_and_blocks_new_ones():
    actors, store = SessionActors(clean_item=lambda raw: raw), {}
    context = _context(actors, store)
    order = []
    gate = asyncio.Event()

    async def web_turn():
        order.append("web-start")
        await gate.wait()
        order.append("web-end")

    actors._launch(context, web_turn, kind="foreground")
    await asyncio.sleep(0)

    async def phone_turn():
        order.append("phone")

    external = asyncio.create_task(actors.run_exclusive(context, phone_turn, poll=0.01))
    await asyncio.sleep(0.05)
    assert order == ["web-start"]                          # 前一个回合没完，不插队
    gate.set()
    assert await asyncio.wait_for(external, 2) is True
    assert order == ["web-start", "web-end", "phone"]


async def test_external_turn_errors_propagate_and_stop_cancels():
    actors, store = SessionActors(clean_item=lambda raw: raw), {}
    context = _context(actors, store)

    async def boom():
        raise RuntimeError("模型挂了")

    with pytest.raises(RuntimeError, match="模型挂了"):
        await actors.run_exclusive(context, boom, poll=0.01)
    assert not actors.busy("sid-x")

    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(10)

    item = {"id": "im-1", "text": "手机上的话", "mode": "plan"}
    task = asyncio.create_task(actors.run_exclusive(context, slow, item=item, poll=0.01))
    await started.wait()
    assert actors.snapshot(context)["running"]["text"] == "手机上的话"   # Desktop 看得到这一轮在跑
    actors.cancel("sid-x")                                 # Desktop 点「停止」
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)


async def test_external_turn_gives_up_when_slot_stays_busy():
    actors, store = SessionActors(clean_item=lambda raw: raw), {}
    context = _context(actors, store)
    actors._launch(context, lambda: asyncio.sleep(5), kind="foreground")
    assert await actors.run_exclusive(context, lambda: asyncio.sleep(0), wait_timeout=0.05, poll=0.01) is False


# —— IM 桥并入主要聊天 ——

async def test_telegram_turn_uses_and_returns_main_chat_history(tmp_path):
    from tests.unit.test_im_bridge import OWNER, FakeAdapter, ScriptedLLM, _drive_no_turn, _msg, _run_turn_to_completion
    from src.im.bridge import IMBridge

    shared = {"history": [{"role": "user", "content": "电脑上说过：下午盯一下 PR #431"},
                          {"role": "assistant", "content": "好的，记下了。"}]}
    calls = []

    async def host(*, key, source, user_text, rid, run):
        calls.append({"key": key, "source": source, "text": user_text})
        seen = list(shared["history"])
        history, reply = await run(seen)
        shared["history"] = history
        return reply

    mc.set_main_conversation_host(host)
    mc.claim_main_chat(str(tmp_path), "sid-desk")
    adapter = FakeAdapter()
    llm = ScriptedLLM("PR #431 已经合并了。")
    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="telegram", llm=llm, workspace_scope="general")
    await _run_turn_to_completion(bridge, adapter, _msg("刚才那个 PR 怎么样了"))

    assert calls == [{"key": "sid-desk", "source": "telegram", "text": "刚才那个 PR 怎么样了"}]
    contents = [str(m.get("content")) for m in shared["history"]]
    assert any("PR #431" in c and "电脑上说过" in c for c in contents)   # 手机这一轮带着电脑上的上下文
    assert any("刚才那个 PR" in c for c in contents)                    # 并交回了主要聊天
    assert "PR #431 已经合并了。" in adapter.texts()
    assert getattr(bridge, "_transcript", []) == []                   # 记录只在主要聊天里记一份

    await _drive_no_turn(bridge, adapter, _msg("/new"))
    assert any("不能在这里清空" in text for text in adapter.texts())
    assert shared["history"]                                          # 没被清掉


async def test_project_scope_bridge_never_joins_main_chat(tmp_path):
    from tests.unit.test_im_bridge import OWNER, FakeAdapter, ScriptedLLM, _msg, _run_turn_to_completion
    from src.im.bridge import IMBridge

    called = []

    async def host(**kwargs):
        called.append(kwargs)
        return ""

    mc.set_main_conversation_host(host)
    mc.claim_main_chat(str(tmp_path), "sid-desk")
    adapter = FakeAdapter()
    bridge = IMBridge(str(tmp_path), adapter, OWNER, channel="telegram", llm=ScriptedLLM("hi"),
                      workspace_scope="project")
    await _run_turn_to_completion(bridge, adapter, _msg("你好"))
    assert called == [] and bridge._transcript


# —— Web 宿主：写进同一段会话、存盘、推给 Desktop ——

async def test_web_host_appends_persists_and_broadcasts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from src.gateway.session_store import load_session
    from src.web.routers import realtime

    events = []

    async def capture(key, event, *, fallback=None):
        events.append((key, event["type"]))

    monkeypatch.setattr(realtime, "_publish_session_event", capture)
    monkeypatch.setattr(realtime, "_schedule_turn_digest", lambda key, rid: None)
    key = "sid-maintest"
    try:
        sess = realtime._TABLE.get(key, repo_root=str(tmp_path), factory=realtime._new_agent)
        sess["agent"].history = [{"role": "user", "content": "电脑上的话"}]

        async def run(history):
            assert history == [{"role": "user", "content": "电脑上的话"}]
            return history + [{"role": "user", "content": "手机上的话"},
                              {"role": "assistant", "content": "收到"}], "收到"

        reply = await realtime._run_main_conversation_turn(key=key, source="telegram",
                                                           user_text="手机上的话", rid="im-abc", run=run)
        assert reply == "收到"
        assert [m["content"] for m in sess["agent"].history][-1] == "收到"
        saved = load_session(str(tmp_path), key)
        assert [(i["role"], i["source"]) for i in saved["transcript"]] == [("user", "telegram"), ("assistant", "telegram")]
        assert ("sid-maintest", "agent_history") in events and ("sid-maintest", "agent_done") in events
        assert not realtime._ACTORS.busy(key)
    finally:
        realtime._TABLE.pop(key)


def test_main_chat_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    from src.web.server import app

    client = TestClient(app)
    assert client.get("/api/assistant/main-chat").json() == {"sid": None}
    assert client.put("/api/assistant/main-chat", json={"sid": "abc"}).json() == {"sid": "abc"}
    assert client.put("/api/assistant/main-chat", json={"sid": "xyz"}).json() == {"sid": "abc"}
    assert client.put("/api/assistant/main-chat", json={"sid": "a/b"}).status_code == 400
    assert client.get("/api/assistant/main-chat").json() == {"sid": "abc"}


@pytest.mark.parametrize("publication_failure", [False, True])
async def test_external_observer_loss_stops_reserved_turn_and_preserves_queue(publication_failure):
    actors, store = SessionActors(clean_item=lambda raw: raw), {}
    base = _context(actors, store)
    publishing, executing, blocked = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cancelled = asyncio.Event()
    first_publication = True

    async def publish():
        nonlocal first_publication
        if first_publication:
            first_publication = False
            publishing.set()
            await blocked.wait()
            if publication_failure:
                raise RuntimeError("observer disconnected")

    async def execute():
        executing.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    context = ActorContext(key=base.key, get_session=base.get_session, touch=base.touch,
                           persist=base.persist, publish=publish, execute=base.execute)
    observer = asyncio.create_task(actors.run_exclusive(context, execute,
        item={"id": "phone-turn", "text": "phone", "mode": "plan"}))
    await asyncio.wait_for(publishing.wait(), 1)
    await asyncio.wait_for(executing.wait(), 1)
    queued = {"id": "desktop-next", "text": "next", "mode": "plan"}
    assert await actors.enqueue(context, queued) == "queued"
    if publication_failure:
        blocked.set()
        with pytest.raises(RuntimeError, match="observer disconnected"):
            await observer
    else:
        observer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await observer
    await asyncio.wait_for(cancelled.wait(), 1)
    for _ in range(10):
        if not actors.busy(context.key):
            break
        await asyncio.sleep(0)
    assert not actors.busy(context.key)
    assert actors.snapshot(context)["running"] is None
    assert [item["id"] for item in actors.queue(context)] == ["desktop-next"]
    assert store.get("ran", []) == []
    await actors.resume(context)
    for _ in range(10):
        if not actors.busy(context.key):
            break
        await asyncio.sleep(0)
    assert store["ran"] == ["desktop-next"]


async def test_late_external_observer_cancel_does_not_stop_newer_desktop_turn():
    actors, store = SessionActors(clean_item=lambda raw: raw), {}
    base = _context(actors, store)
    publishing = asyncio.Event()
    desktop_started = asyncio.Event()
    desktop_release = asyncio.Event()
    first_publication = True

    async def publish():
        nonlocal first_publication
        if first_publication:
            first_publication = False
            publishing.set()
            await asyncio.Event().wait()

    async def desktop(item):
        desktop_started.set()
        await desktop_release.wait()

    context = ActorContext(key=base.key, get_session=base.get_session, touch=base.touch,
                           persist=base.persist, publish=publish, execute=desktop)
    observer = asyncio.create_task(actors.run_exclusive(context, lambda: asyncio.sleep(0)))
    await asyncio.wait_for(publishing.wait(), 1)
    for _ in range(10):
        if not actors.busy(context.key):
            break
        await asyncio.sleep(0)
    assert not actors.busy(context.key)
    assert await actors.start(context, {"id": "desktop", "text": "new", "mode": "plan"})
    await asyncio.wait_for(desktop_started.wait(), 1)
    current = actors.tasks[context.key]
    observer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await observer
    assert actors.tasks[context.key] is current
    assert not current.cancelled()
    assert actors.snapshot(context)["running"]["id"] == "desktop"
    desktop_release.set()
    await current


def test_concurrent_claims_publish_one_complete_registration(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, local

    root = str(tmp_path)
    original_read = mc.main_chat_key
    barrier, state = Barrier(2), local()

    def synchronized_first_read(root):
        result = original_read(root)
        if not getattr(state, "read", False):
            state.read = True
            barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(mc, "main_chat_key", synchronized_first_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(mc.claim_main_chat, root, key) for key in ("sid-first", "sid-second")]
        results = [future.result(timeout=5) for future in futures]
    assert results[0] == results[1] == original_read(root)
    assert results[0] in {"sid-first", "sid-second"}
    assert list((tmp_path / ".vortocode").glob(".main_chat.*.tmp")) == []


def test_corrupt_registration_is_preserved_and_claim_route_reports_conflict(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.web.server import app

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("VORTOCODE_API_TOKEN", raising=False)
    path = tmp_path / ".vortocode" / "main_chat.json"
    path.parent.mkdir()
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(mc.MainChatUnavailable):
        mc.claim_main_chat(str(tmp_path), "sid-new")
    response = TestClient(app).put("/api/assistant/main-chat", json={"sid": "new"})
    assert response.status_code == 409
    assert path.read_text(encoding="utf-8") == "{broken"
    assert list(path.parent.glob(".main_chat.*.tmp")) == []


def test_failed_registration_publication_cleans_temporary_file(tmp_path, monkeypatch):
    def fail_link(*args):
        raise OSError("publication failed")

    monkeypatch.setattr(mc.os, "link", fail_link)
    with pytest.raises(OSError, match="publication failed"):
        mc.claim_main_chat(str(tmp_path), "sid-new")
    assert mc.main_chat_key(str(tmp_path)) is None
    assert list((tmp_path / ".vortocode").glob(".main_chat.*.tmp")) == []


async def test_phone_completion_runs_desktop_queue_with_its_original_executor():
    actors, store = SessionActors(clean_item=lambda raw: raw), {}
    desktop = _context(actors, store)
    phone_started, phone_release = asyncio.Event(), asyncio.Event()
    swallowed = []

    async def no_execute(item):
        swallowed.append(item["id"])

    phone = ActorContext(key=desktop.key, get_session=desktop.get_session,
                         touch=desktop.touch, persist=desktop.persist,
                         publish=desktop.publish, execute=no_execute)

    async def turn():
        phone_started.set()
        await phone_release.wait()

    external = asyncio.create_task(actors.run_exclusive(phone, turn))
    await asyncio.wait_for(phone_started.wait(), 1)
    assert await actors.enqueue(desktop, {"id": "desktop-next", "text": "next", "mode": "plan"}) == "queued"
    phone_release.set()
    assert await external
    if actors.busy(desktop.key):
        await actors.tasks[desktop.key]
    assert store["ran"] == ["desktop-next"]
    assert swallowed == []
    assert actors.queue(desktop) == []
    assert actors.forget(desktop.key)
    assert desktop.key not in actors._foreground_contexts


async def test_phone_request_reports_paused_queue_without_waiting_or_consuming_input():
    actors, store = SessionActors(clean_item=lambda raw: raw), {}
    context = _context(actors, store)
    queued = {"id": "desktop-next", "text": "next", "mode": "plan"}
    assert await actors.enqueue(context, queued) == "queued"
    actors.cancel(context.key, reason="stop")
    calls = []

    async def phone():
        calls.append("phone")

    assert await asyncio.wait_for(actors.run_exclusive(context, phone), 1) is False
    assert actors.snapshot(context)["paused"] is True
    assert calls == []
    assert actors.queue(context) == [queued]
    await actors.resume(context)
    assert actors.snapshot(context)["paused"] is False
    if actors.busy(context.key):
        await actors.tasks[context.key]
    assert store["ran"] == ["desktop-next"]


@pytest.mark.parametrize("cancelled", [False, True])
async def test_incomplete_phone_turn_is_consistent_in_display_context_and_saved_history(tmp_path, monkeypatch, cancelled):
    monkeypatch.chdir(tmp_path)
    from src.gateway.session_store import load_session
    from src.web.routers import realtime

    events = []

    async def capture(key, event, *, fallback=None):
        events.append(event)

    monkeypatch.setattr(realtime, "_publish_session_event", capture)
    key = "sid-incomplete-phone"
    try:
        sess = realtime._TABLE.get(key, repo_root=str(tmp_path), factory=realtime._new_agent)
        original = [{"role": "user", "content": "earlier desktop message"}]
        sess["agent"].history = original

        async def run(history):
            assert history == original
            if cancelled:
                raise asyncio.CancelledError()
            raise RuntimeError("provider unavailable")

        expected = asyncio.CancelledError if cancelled else RuntimeError
        with pytest.raises(expected):
            await realtime._run_main_conversation_turn(key=key, source="telegram",
                user_text="phone request", rid="im-incomplete", run=run)
        saved = load_session(str(tmp_path), key)
        assert saved["history"] == sess["agent"].history
        assert saved["history"][:-2] == original
        assert saved["history"][-2] == {"role": "user", "content": "phone request"}
        message = saved["history"][-1]["content"]
        assert ("已取消" if cancelled else "执行失败") in message
        assert "未确认完成" in message
        assert saved["transcript"][-1]["text"] == message
        assert saved["transcript"][-1]["source"] == "telegram"
        assert any(event["type"] == ("agent_cancelled" if cancelled else "agent_error") for event in events)
        assert not realtime._ACTORS.busy(key)
    finally:
        realtime._TABLE.pop(key)
        realtime._ACTORS.forget(key)
