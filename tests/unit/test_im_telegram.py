"""Telegram 适配器测试——注入假 request_fn，不触网跑全逻辑。"""

import pytest

from src.im.telegram import TelegramAdapter, _to_event


def _recorder(results=None):
    """造一个记录调用、按 method 返回预设 result 的假 request_fn。"""
    calls = []
    results = results or {}

    async def _req(method, payload):
        calls.append((method, payload))
        r = results.get(method)
        return r() if callable(r) else r

    return _req, calls


# ------------------------------------------------------------ 归一化
def test_to_event_message_and_callback():
    m = _to_event({"update_id": 1, "message": {"text": "hi", "from": {"id": 7}}})
    assert m.kind == "message" and m.text == "hi" and m.sender_id == "7"
    c = _to_event({"update_id": 2, "callback_query": {"id": "q9", "data": "approve:abc", "from": {"id": 7}}})
    assert c.kind == "callback" and c.callback_id == "abc" and c.approved is True and c.ack == "q9"
    d = _to_event({"update_id": 3, "callback_query": {"id": "q", "data": "deny:z", "from": {"id": 7}}})
    assert d.approved is False and d.callback_id == "z"
    assert _to_event({"update_id": 4, "edited_message": {}}) is None   # 无关更新跳过


# ------------------------------------------------------------ 发送
@pytest.mark.asyncio
async def test_send_text_and_confirm():
    req, calls = _recorder({"sendMessage": {"message_id": 42}})
    a = TelegramAdapter("tok", "99", request_fn=req)

    mid = await a.send_text("你好")
    assert mid == "42"
    assert calls[0][0] == "sendMessage"
    assert calls[0][1]["chat_id"] == "99" and calls[0][1]["text"] == "你好"

    await a.send_confirm("要跑命令吗？", "cid1")
    method, payload = calls[-1]
    assert method == "sendMessage"
    row = payload["reply_markup"]["inline_keyboard"][0]
    assert row[0]["callback_data"] == "approve:cid1" and row[1]["callback_data"] == "deny:cid1"


@pytest.mark.asyncio
async def test_ack_callback():
    req, calls = _recorder()
    a = TelegramAdapter("tok", "99", request_fn=req)
    from src.im.channel import ChannelEvent
    await a.ack_callback(ChannelEvent(kind="callback", ack="qid7"))
    assert calls[-1][0] == "answerCallbackQuery" and calls[-1][1]["callback_query_id"] == "qid7"


# ------------------------------------------------------------ 长轮询
@pytest.mark.asyncio
async def test_poll_yields_events_and_advances_offset():
    batches = [
        [{"update_id": 5, "message": {"text": "hi", "from": {"id": 99}}}],
        [{"update_id": 6, "callback_query": {"id": "q", "data": "deny:c", "from": {"id": 99}}}],
    ]

    async def _req(method, payload):
        assert method == "getUpdates"
        return batches.pop(0) if batches else []

    a = TelegramAdapter("tok", "99", request_fn=_req)
    got = []
    async for ev in a.poll():
        got.append(ev)
        if len(got) >= 2:
            break
    assert got[0].kind == "message" and got[1].kind == "callback"
    assert a._offset == 7                          # offset 推进到最后 update_id + 1


@pytest.mark.asyncio
async def test_poll_backs_off_on_error_then_recovers(monkeypatch):
    import src.im.telegram as tg
    sleeps = []

    async def _fast_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(tg.asyncio, "sleep", _fast_sleep)

    state = {"n": 0}

    async def _req(method, payload):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("网络抖动")            # 第一次失败 → 退避
        return [{"update_id": 1, "message": {"text": "ok", "from": {"id": 99}}}]

    a = TelegramAdapter("tok", "99", request_fn=_req)
    async for ev in a.poll():
        assert ev.text == "ok"
        break
    assert sleeps and sleeps[0] == 1.0              # 失败退避过、随后恢复
