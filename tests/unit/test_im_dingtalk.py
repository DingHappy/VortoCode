"""钉钉 Stream 适配器测试——注入假 transport（FakeWS + 假 reply_fn），不触网跑全协议逻辑。"""

import asyncio
import json

import pytest

from src.im.bridge import IMBridge
from src.im.dingtalk import BOT_TOPIC, DingTalkAdapter


def _ping(mid, opaque):
    return json.dumps({"type": "SYSTEM", "headers": {"messageId": mid, "topic": "ping"},
                       "data": json.dumps({"opaque": opaque})})


def _bot(mid, sender, content, webhook="https://wh"):
    return json.dumps({"type": "CALLBACK", "headers": {"messageId": mid, "topic": BOT_TOPIC},
                       "data": json.dumps({"senderStaffId": sender, "sessionWebhook": webhook,
                                           "text": {"content": content}})})


class FakeWS:
    """按 recv 顺序吐预置帧、记录 send 出去的帧；空了返回 None（连接关闭）。"""
    def __init__(self, frames):
        self._in = list(frames)
        self.sent = []

    async def recv(self):
        return self._in.pop(0) if self._in else None

    async def send(self, s):
        self.sent.append(s)

    async def close(self):
        pass


# ------------------------------------------------------------ 帧处理
@pytest.mark.asyncio
async def test_ping_responds_pong_with_opaque():
    a = DingTalkAdapter("c", "s", "o")
    ws = FakeWS([])
    out = await a._handle_frame(ws, _ping("m1", "OP7"))
    assert out == []
    resp = json.loads(ws.sent[0])
    assert resp["code"] == 200 and resp["headers"]["messageId"] == "m1"
    assert json.loads(resp["data"])["opaque"] == "OP7"


@pytest.mark.asyncio
async def test_bot_message_acks_and_yields_message():
    a = DingTalkAdapter("c", "s", "owner-1")
    ws = FakeWS([])
    evs = await a._handle_frame(ws, _bot("m2", "owner-1", "跑个任务", "https://wh2"))
    ack = json.loads(ws.sent[0])
    assert ack["headers"]["messageId"] == "m2"           # ACK 回 echo messageId
    assert evs[0].kind == "message" and evs[0].text == "跑个任务" and evs[0].sender_id == "owner-1"
    assert a._webhook == "https://wh2"                   # 更新回复目标


@pytest.mark.asyncio
async def test_disconnect_frame_raises_for_reconnect():
    import src.im.dingtalk as dd
    a = DingTalkAdapter("c", "s", "o")
    frame = json.dumps({"type": "SYSTEM", "headers": {"topic": "disconnect"}, "data": "{}"})
    with pytest.raises(dd._Disconnect):
        await a._handle_frame(FakeWS([]), frame)


# ------------------------------------------------------------ 文本式确认翻译
def test_text_confirm_translation():
    a = DingTalkAdapter("c", "s", "owner-1")
    assert a._to_event("owner-1", "普通任务").kind == "message"
    a._awaiting_confirm = "cid1"
    ev = a._to_event("owner-1", "y")
    assert ev.kind == "callback" and ev.callback_id == "cid1" and ev.approved is True
    assert a._awaiting_confirm is None                   # 消费后清空
    a._awaiting_confirm = "cid2"
    assert a._to_event("owner-1", "拒绝").approved is False
    a._awaiting_confirm = "cid3"                          # 非主人回的 y/n 不算确认
    assert a._to_event("stranger", "y").kind == "message" and a._awaiting_confirm == "cid3"


# ------------------------------------------------------------ 发送
@pytest.mark.asyncio
async def test_send_text_and_confirm_use_webhook():
    calls = []

    async def reply(wh, payload):
        calls.append((wh, payload))
    a = DingTalkAdapter("c", "s", "o", reply_fn=reply)

    await a.send_text("hi")
    assert calls == []                                   # 还没 webhook → 不发（钉钉反应式）
    a._webhook = "https://wh"
    await a.send_text("hi")
    assert calls[-1][0] == "https://wh" and calls[-1][1]["text"]["content"] == "hi"

    await a.send_confirm("要跑命令吗？", "cid9")
    assert a._awaiting_confirm == "cid9"
    assert "y" in calls[-1][1]["text"]["content"] and "n" in calls[-1][1]["text"]["content"]


@pytest.mark.asyncio
async def test_poll_yields_message_event():
    conns = [FakeWS([_bot("m", "owner-1", "hi")])]

    async def connect():
        if conns:
            return conns.pop(0)
        raise asyncio.CancelledError                     # 第二次建连 → 停掉 poll（测试收口）
    a = DingTalkAdapter("c", "s", "owner-1", connect_fn=connect)
    got = []
    async for ev in a.poll():
        got.append(ev)
        break
    assert got[0].kind == "message" and got[0].text == "hi"


# ------------------------------------------------------------ 钉钉 × bridge：文本确认端到端
class QueueWS:
    def __init__(self):
        self._q: asyncio.Queue = asyncio.Queue()
        self.sent = []

    def push(self, raw):
        self._q.put_nowait(raw)

    async def recv(self):
        return await self._q.get()

    async def send(self, s):
        self.sent.append(s)

    async def close(self):
        pass


class ScriptedLLM:
    def __init__(self, *responses):
        self._r = list(responses)
        self.n = 0

    async def chat(self, messages, **kwargs):
        i = min(self.n, len(self._r) - 1)
        self.n += 1
        return {"content": self._r[i]}


@pytest.mark.asyncio
async def test_dingtalk_text_confirm_through_bridge(tmp_path):
    """端到端：钉钉主人发任务 → 触发 save_skill 确认 → 适配器发'回复 y/n' → 主人回 y →
    适配器翻成 callback → bridge 解开确认 → 技能真写。证明文本式确认与 bridge 无缝对接。"""
    ws = QueueWS()

    async def connect():
        return ws

    async def reply(wh, payload):
        pass
    adapter = DingTalkAdapter("c", "s", "owner-1", connect_fn=connect, reply_fn=reply)
    llm = ScriptedLLM(
        '{"tool":"save_skill","args":{"name":"greet","description":"打招呼","instructions":"说你好"}}',
        "技能已保存。")
    bridge = IMBridge(str(tmp_path), adapter, "owner-1", channel="dingtalk", mode="build", llm=llm)

    run_task = asyncio.create_task(bridge.run())
    try:
        ws.push(_bot("m1", "owner-1", "把打招呼存成技能"))     # 发任务
        for _ in range(4000):                                # 等回合跑到确认（适配器挂起等 y/n）
            if adapter._awaiting_confirm:
                break
            await asyncio.sleep(0)
        assert adapter._awaiting_confirm is not None          # 确认提示已发出
        ws.push(_bot("m2", "owner-1", "y"))                   # 主人回 y（批准）
        await asyncio.wait_for(_until(lambda: bridge._turn_task and bridge._turn_task.done()), timeout=5)
    finally:
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass
    assert (tmp_path / ".vortocode" / "skills" / "greet" / "SKILL.md").exists()   # 批准 → 真写了


async def _until(cond, interval=0):
    while not cond():
        await asyncio.sleep(interval)
