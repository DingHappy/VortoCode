"""钉钉互动卡片确认（T2）——把"回复 y/n"变成真按钮。

三条纪律各有对照，缺一条这个功能就可能把 bot 弄死或把闸弄漏：

1. **没配模板 = 完全不启用**，且建连 payload 与不加本功能时**逐字节相同**。
   加订阅主题会改建连请求；万一钉钉拒绝未知主题，长连建不起来就是 bot 直接死——
   这个风险本地验不了，所以必须锁在"配了才有"的门后面。
2. **卡片任何一步出错都退回文本 y/n**。卡片是体验优化，确认能力一秒都不能丢。
3. **按钮点击仍走 bridge 的三道入站闸**（白名单/群提及/审批只认主人）。
   换个入口就绕过闸是最典型的漏法。
"""

import json

import pytest

from src.im import dingtalk_card as C
from src.im.dingtalk import BOT_TOPIC, DingTalkAdapter


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch):
    """断网闸（同 test_im_dingtalk）：任何路径真触网 → 确定性炸。"""
    import aiohttp

    def _boom(*_a, **_k):
        raise AssertionError("测试不许触网：注入 connect_fn/reply_fn/oto_fn/card_sender")

    monkeypatch.setattr(aiohttp, "ClientSession", _boom)


@pytest.fixture(autouse=True)
def _no_template_by_default(monkeypatch):
    monkeypatch.delenv("VORTOCODE_DD_CARD_TEMPLATE_ID", raising=False)


class _FakeCard:
    """假卡片发送器：记录发了什么、可配置成发送失败。"""

    def __init__(self, boom=False):
        self.sent: list = []
        self.settled: list = []
        self.boom = boom

    async def send(self, text, callback_id):
        if self.boom:
            raise RuntimeError("卡片接口 HTTP 500")
        self.sent.append((text, callback_id))

    async def settle(self, callback_id, approved):
        self.settled.append((callback_id, approved))


class _WS:
    def __init__(self):
        self.sent: list = []

    async def send(self, s):
        self.sent.append(s)

    async def close(self):
        pass


# --------------------------------------------------------------- 1. 零风险门
def test_without_template_subscription_is_byte_identical():
    """**这条是整个功能能安全上线的前提。**

    没配模板时建连 payload 必须与不加本功能时完全一样：多订阅一个未知主题若被钉钉拒绝，
    长连建不起来 = bot 直接死，而这个风险在本地验不出来。
    """
    a = DingTalkAdapter("c", "s", "o")
    assert a._subscriptions() == [{"type": "CALLBACK", "topic": BOT_TOPIC}]
    assert a._card is None


def test_with_template_adds_exactly_one_topic(monkeypatch):
    monkeypatch.setenv("VORTOCODE_DD_CARD_TEMPLATE_ID", "TPL-1")
    a = DingTalkAdapter("c", "s", "o")
    topics = [x["topic"] for x in a._subscriptions()]
    assert topics == [BOT_TOPIC, C.CARD_CALLBACK_TOPIC]


@pytest.mark.asyncio
async def test_without_template_confirm_stays_text():
    """没配模板 → 确认仍是文本 y/n，行为与今天一模一样。"""
    sent: list = []

    async def oto(key, param):
        sent.append(param["content"])

    a = DingTalkAdapter("c", "s", "o", oto_fn=oto)
    await a.send_confirm("要跑命令吗？", "cid1")
    assert a._awaiting_confirm == "cid1"
    assert sent and "**y**" in sent[0] and "**n**" in sent[0]


# --------------------------------------------------------------- 2. 降级路径
@pytest.mark.asyncio
async def test_card_used_when_configured():
    card = _FakeCard()
    a = DingTalkAdapter("c", "s", "o", card_sender=card)
    await a.send_confirm("写文件 a.txt？", "cid9")
    assert card.sent == [("写文件 a.txt？", "cid9")]


@pytest.mark.asyncio
async def test_card_failure_falls_back_to_text():
    """卡片挂了必须退回文本——**确认能力一秒都不能丢**。

    2026-07-28 刚被"投递静默失败"教育过：交付路径宁可难看，不可断。
    """
    text: list = []

    async def oto(key, param):
        text.append(param["content"])

    a = DingTalkAdapter("c", "s", "o", oto_fn=oto, card_sender=_FakeCard(boom=True))
    await a.send_confirm("跑个命令？", "cid7")
    assert text and "**y**" in text[0], "卡片挂了却没退回文本——这次确认人就收不到了"
    assert a._awaiting_confirm == "cid7"


@pytest.mark.asyncio
async def test_text_reply_still_works_when_card_sent():
    """卡片发出去了，主人**照样能打字回 y**。

    他不一定想点按钮；卡片万一在他手机上渲染不出来，打字必须始终是可用的兜底。
    """
    a = DingTalkAdapter("c", "s", "owner-1", card_sender=_FakeCard())
    await a.send_confirm("要跑吗？", "cid5")
    ev = a._to_event("owner-1", "y")
    assert ev.kind == "callback" and ev.callback_id == "cid5" and ev.approved is True


# --------------------------------------------------------------- 3. 回调解析（fail-closed）
@pytest.mark.parametrize("blob", [
    {"cardActionData": {"cardPrivateData": {"params": {"action": "approve"}}}},
    {"cardActionData": json.dumps({"params": {"value": "approve"}})},
    {"actionData": {"key": "approve"}},
    {"params": {"buttonKey": "approve"}},
])
def test_parse_approve_across_payload_shapes(blob):
    """钉钉在不同版本里把按钮值放在几个不同位置——都要认得出。"""
    got = C.parse_card_callback({"outTrackId": "cid1", "userId": "u1", **blob})
    assert got == ("cid1", True, "u1")


def test_parse_deny():
    got = C.parse_card_callback({"outTrackId": "cid2", "userId": "u1",
                                 "cardActionData": {"params": {"action": "deny"}}})
    assert got == ("cid2", False, "u1")


@pytest.mark.parametrize("data", [
    {},
    {"outTrackId": "cid"},                                   # 没有动作数据
    {"outTrackId": "cid", "cardActionData": {"params": {"action": "whatever"}}},
    {"cardActionData": {"params": {"action": "approve"}}},    # 没有 outTrackId
    {"outTrackId": "cid", "cardActionData": "不是 json"},
    None,
])
def test_unrecognized_callback_is_never_an_approval(data):
    """**认不出就当没看见，绝不当成"批准"。** fail-closed 的方向永远是"不放行"。"""
    assert C.parse_card_callback(data) is None


# --------------------------------------------------------------- 4. 闸不能被绕过
@pytest.mark.asyncio
async def test_card_click_becomes_a_normal_callback_event():
    """按钮点击产出的仍是普通 callback 事件 → 照样过 bridge 的三道入站闸。

    换个入口就绕过闸（白名单/群提及/审批只认主人）是最典型的漏法。
    """
    card = _FakeCard()
    a = DingTalkAdapter("c", "s", "owner-1", card_sender=card)
    a._awaiting_confirm = "cid3"
    frame = json.dumps({
        "type": "CALLBACK",
        "headers": {"messageId": "m1", "topic": C.CARD_CALLBACK_TOPIC},
        "data": json.dumps({"outTrackId": "cid3", "userId": "owner-1",
                            "cardActionData": {"params": {"action": "approve"}}})})
    evs = await a._handle_frame(_WS(), frame)
    assert len(evs) == 1
    ev = evs[0]
    assert ev.kind == "callback" and ev.callback_id == "cid3" and ev.approved is True
    assert a._awaiting_confirm is None                       # 待确认态被消费
    assert card.settled == [("cid3", True)]                  # 卡片刷成终态


@pytest.mark.asyncio
async def test_stranger_card_click_is_still_gated_by_the_bridge(tmp_path):
    """陌生人点按钮 → 事件照常产出，但**由 bridge 丢掉**（审批只认主人）。

    这条走真 bridge，不是断言适配器自己判定——安全判定归 bridge 一处，
    适配器只如实申报事实（channel.py 的分工）。
    """
    from src.im.bridge import IMBridge
    from tests.unit.test_im_bridge import ScriptedLLM

    a = DingTalkAdapter("c", "s", "owner-1", card_sender=_FakeCard())
    bridge = IMBridge(str(tmp_path), a, "owner-1", channel="test", llm=ScriptedLLM("x"))

    import asyncio
    fut = asyncio.get_event_loop().create_future()
    bridge._pending["cid4"] = fut

    from src.im.channel import ChannelEvent
    await bridge._on_event(ChannelEvent(kind="callback", sender_id="stranger-9",
                                        callback_id="cid4", approved=True))
    assert not fut.done(), "陌生人的按钮点击批准了一次确认——审批闸被绕过"

    await bridge._on_event(ChannelEvent(kind="callback", sender_id="owner-1",
                                        callback_id="cid4", approved=True))
    assert fut.done() and fut.result() is True               # 主人点才算数


@pytest.mark.asyncio
async def test_card_callback_is_acked():
    """卡片回调也要回 ACK，否则钉钉会重投。"""
    a = DingTalkAdapter("c", "s", "o", card_sender=_FakeCard())
    ws = _WS()
    await a._handle_frame(ws, json.dumps({
        "type": "CALLBACK",
        "headers": {"messageId": "m9", "topic": C.CARD_CALLBACK_TOPIC},
        "data": json.dumps({"outTrackId": "c", "userId": "o",
                            "cardActionData": {"params": {"action": "deny"}}})}))
    assert ws.sent and json.loads(ws.sent[0])["headers"]["messageId"] == "m9"


# --------------------------------------------------------------- 5. 载荷形状
def test_payload_carries_template_track_and_stream_callback():
    p = C.build_card_payload("TPL-7", "owner-1", "cid8", "写文件 a.txt？\n路径在仓库内")
    assert p["cardTemplateId"] == "TPL-7"
    assert p["outTrackId"] == "cid8"                          # 回调靠它认出是哪次确认
    assert p["callbackType"] == "STREAM"
    assert p["openSpaceId"] == "dtv1.card//IM_ROBOT.owner-1"  # 收件人恒为已配对 owner


def test_taint_warning_lands_in_the_title():
    """污点警示必须留在**标题**——那是卡片上最显眼的位置。

    把安全提示挤到正文末尾等于没提示（#251/#252 治的就是"红灯说了等于没说"）。
    """
    text = "⚠️ 本回合摄入过外部内容，请仔细核对\n跑命令：rm -rf /tmp/x"
    p = C.build_card_payload("T", "o", "c", text)
    assert "⚠️" in p["cardData"]["cardParamMap"]["title"]
    assert "rm -rf" in p["cardData"]["cardParamMap"]["body"]


def test_update_payload_marks_the_decision():
    """点完要把卡片刷成终态——按钮留着还能点，人会以为这条还等着自己。"""
    assert "已批准" in C.build_update_payload("c", True)["cardData"]["cardParamMap"]["status"]
    assert "已拒绝" in C.build_update_payload("c", False)["cardData"]["cardParamMap"]["status"]


@pytest.mark.asyncio
async def test_settle_failure_never_raises():
    """刷终态失败只记日志——确认已经生效，不能因为刷不动卡片而翻车。"""
    async def _boom(url, payload, token):
        raise RuntimeError("HTTP 500")

    sender = C.CardSender("T", "o", post_fn=_boom)
    await sender.settle("cid", True)          # 不抛即通过


@pytest.mark.asyncio
async def test_sender_posts_to_create_and_deliver():
    calls: list = []

    async def _post(url, payload, token):
        calls.append((url, payload))
        return {}

    await C.CardSender("TPL", "owner-1", post_fn=_post).send("确认？", "cid1")
    assert calls and calls[0][0].endswith("/card/instances/createAndDeliver")
    assert calls[0][1]["outTrackId"] == "cid1"
