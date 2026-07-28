"""钉钉 Stream 适配器测试——注入假 transport（FakeWS + 假 reply_fn），不触网跑全协议逻辑。"""

import asyncio
import json

import pytest

from src.im.bridge import IMBridge
from src.im.dingtalk import BOT_TOPIC, DingTalkAdapter


def _ping(mid, opaque):
    return json.dumps({"type": "SYSTEM", "headers": {"messageId": mid, "topic": "ping"},
                       "data": json.dumps({"opaque": opaque})})


def _bot(mid, sender, content, webhook="https://wh", conv="1", at=False):
    """一帧机器人消息（conv "1"=单聊 / "2"=群聊；at=群里是否 @ 到机器人，即 isInAtList）。"""
    return json.dumps({"type": "CALLBACK", "headers": {"messageId": mid, "topic": BOT_TOPIC},
                       "data": json.dumps({"senderStaffId": sender, "sessionWebhook": webhook,
                                           "conversationType": conv, "isInAtList": at,
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
    # B6-6：收帧只**申报**回复路由（reply_to），不直接采纳——否则白名单外的消息也能改写
    # 回复目标（劫走后续产出）。采纳发生在 bridge 过闸后调 commit_reply_target。
    assert evs[0].reply_to == "https://wh2"
    assert a._webhook is None
    a.commit_reply_target(evs[0])                        # 模拟 bridge 放行本条
    assert a._webhook == "https://wh2"


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
    calls, oto = [], []

    async def reply(wh, payload):
        calls.append((wh, payload))
    a = DingTalkAdapter("c", "s", "o", reply_fn=reply)

    async def fake_send_msg(key, param):
        oto.append((key, param))
    a._send_msg = fake_send_msg

    a._webhook = "https://wh"
    await a.send_text("hi")
    assert calls[-1][0] == "https://wh" and calls[-1][1]["text"]["content"] == "hi"
    assert oto == []                     # webhook 活着就不动主动通道——回话留在原会话里

    await a.send_confirm("要跑命令吗？", "cid9")
    assert a._awaiting_confirm == "cid9"
    assert "y" in calls[-1][1]["text"]["content"] and "n" in calls[-1][1]["text"]["content"]


@pytest.mark.asyncio
async def test_send_text_cold_start_falls_back_to_proactive():
    """没有 webhook（服务刚重启 / 主人一夜没说话）→ 必须走 oToMessages 主动推，不许静默丢。

    真机 2026-07-28：01:32 重启清掉内存 webhook，早 9 点新闻、02:00 值班通报、"已就绪"横幅
    全部无声蒸发——旧实现 `if self._webhook: 发` 在这个分支里什么都不做还返回成功，
    而且本文件曾有一条测试把它当特性钉着（"还没 webhook → 不发（钉钉反应式）"）。
    这一条就是那条安慰剂的反转。
    """
    oto = []

    async def reply(wh, payload):
        raise AssertionError("没有 webhook 不该走 reply_fn")
    a = DingTalkAdapter("c", "s", "owner-1", reply_fn=reply)

    async def fake_send_msg(key, param):
        oto.append((key, param))
    a._send_msg = fake_send_msg

    await a.send_text("📰 早报")
    assert oto == [("sampleText", {"content": "📰 早报"})]


@pytest.mark.asyncio
async def test_send_text_stale_webhook_falls_back_and_drops_it():
    """webhook 失效（过期/网络错）→ 回退主动通道，并把失效目标丢掉（下条过闸入站会重新提交）。"""
    oto = []

    async def reply(wh, payload):
        raise RuntimeError("sessionWebhook errcode 310000: session expired")
    a = DingTalkAdapter("c", "s", "o", reply_fn=reply)
    a._webhook = "https://stale"

    async def fake_send_msg(key, param):
        oto.append((key, param))
    a._send_msg = fake_send_msg

    await a.send_text("hi")
    assert oto == [("sampleText", {"content": "hi"})]
    assert a._webhook is None


@pytest.mark.asyncio
async def test_send_text_total_failure_raises():
    """两条通道都失败必须抛——上层 notify_owner 靠它返回 False、投递器才能落"未送达"的账。
    绝不许回到"什么都没发还报成功"。"""
    a = DingTalkAdapter("c", "s", "o")

    async def boom(key, param):
        raise RuntimeError("batchSend HTTP 403")
    a._send_msg = boom

    with pytest.raises(RuntimeError):
        await a.send_text("hi")


def test_webhook_reply_problem_detects_expired_and_http_errors():
    """webhook 过期时钉钉回 HTTP 200 + 非零 errcode——旧实现连响应都不看，过期后每条都"成功"地消失。"""
    from src.im.dingtalk import _webhook_reply_problem
    assert _webhook_reply_problem(200, b'{"errcode":0,"errmsg":"ok"}') == ""
    assert _webhook_reply_problem(200, b"") == ""
    assert _webhook_reply_problem(200, b"not json") == ""        # 非 JSON 当成功，别误杀
    assert "310000" in _webhook_reply_problem(200, b'{"errcode":310000,"errmsg":"expired"}')
    assert "HTTP 500" in _webhook_reply_problem(500, b"x")


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


@pytest.mark.asyncio
async def test_group_confirm_survives_a_reply_without_mention(tmp_path):
    """群里端到端（B6-7）：主人回的 y 漏了 @ → 被群提及门丢掉是对的，但**待确认态不能被吃掉**。

    回归的是：漏 @ 的那句 y 先在适配器里被翻成 callback（顺带清空 _awaiting_confirm）、
    再被 bridge 的提及门丢弃 → 这次确认从此没人能解，只能干等 600s 超时=拒绝。
    修好后：漏 @ 的 y 不批准任何东西（口径没放宽），补一条 "@机器人 y" 仍然生效。
    """
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
    skill = tmp_path / ".vortocode" / "skills" / "greet" / "SKILL.md"

    run_task = asyncio.create_task(bridge.run())
    try:
        ws.push(_bot("m1", "owner-1", "把打招呼存成技能", conv="2", at=True))   # 群里 @ 我发任务
        for _ in range(4000):
            if adapter._awaiting_confirm:
                break
            await asyncio.sleep(0)
        cid = adapter._awaiting_confirm
        assert cid is not None                                # 确认提示已发到群里

        ws.push(_bot("m2", "owner-1", "y", conv="2", at=False))    # 主人回 y，但**忘了 @**
        for _ in range(200):
            await asyncio.sleep(0)
        assert bridge._ignored_no_mention == 1                # 提及门照丢（口径没放宽）
        assert not skill.exists()                             # 没 @ 的 y 批不动任何东西
        assert adapter._awaiting_confirm == cid               # 关键：待确认态还在，没被静默吃掉
        assert not bridge._turn_task.done()                   # 回合仍挂在确认上，没被判死

        ws.push(_bot("m3", "owner-1", "y", conv="2", at=True))     # 补一条带 @ 的 y → 生效
        await asyncio.wait_for(_until(lambda: bridge._turn_task.done()), timeout=5)
    finally:
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass
    assert skill.exists()                                     # 主人的批准最终落地（没被超时吞掉）


async def _until(cond, interval=0):
    while not cond():
        await asyncio.sleep(interval)
