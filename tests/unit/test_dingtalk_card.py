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


@pytest.mark.asyncio
async def test_card_failure_tells_the_owner_why_once():
    """卡片挂了要**说一声**，但只说第一次。

    真机首配撞的就是这个：模板配好了、权限没开，卡片每次都发不出去，主人收到的永远是
    文本 y/n，**没有任何线索**说明按钮为什么没出现。降级不告知 = 静默失败（#254 同款）。
    只说一次是因为：真坏了的话每条确认都挂一段报错就成了另一种噪音。
    """
    sent: list = []

    async def oto(key, param):
        sent.append(param["content"])

    class _PermDenied:
        async def send(self, text, cid):
            raise RuntimeError('卡片接口 HTTP 403: {"code":"Forbidden.AccessDenied.'
                               'AccessTokenPermissionDenied","message":"应用尚未开通所需的权限：'
                               '[Card.Instance.Write]"}')

        async def settle(self, cid, ok):
            pass

    a = DingTalkAdapter("c", "s", "o", oto_fn=oto, card_sender=_PermDenied())
    await a.send_confirm("要跑吗？", "cid1")
    assert "**y**" in sent[0], "降级后确认能力必须还在"
    assert "Card.Instance.Write" in sent[0], "主人得知道去开哪个权限"

    await a.send_confirm("再来一次？", "cid2")
    assert "**y**" in sent[1] and "Card.Instance.Write" not in sent[1], "只该提示第一次"


@pytest.mark.parametrize("blob,want", [
    ('HTTP 403: {"code":"...AccessTokenPermissionDenied"...[Card.Instance.Write]',
     "Card.Instance.Write"),
    ("HTTP 400: invalid cardTemplateId", "模板"),
])
def test_error_description_points_at_the_fix(blob, want):
    """报错要指向**怎么修**，不是把原始 JSON 糊人一脸。"""
    assert want in C.describe_card_error(RuntimeError(blob))


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


@pytest.mark.parametrize("word,expected", [
    ("approve", True), ("agree", True), ("accept", True), ("yes", True), ("y", True),
    ("deny", False), ("reject", False), ("refuse", False), ("no", False), ("n", False),
    ("AGREE", True), ("  Reject  ", False),          # 大小写/空白不该让按钮变哑巴
])
def test_button_vocabulary_covers_official_template_wording(word, expected):
    """官方审批模板用的是 agree/reject，本仓文档写的是 approve/deny——两套都得认。

    只认一套的后果是：照着官方模板配完，按钮**一声不响地什么都不做**，
    而这类"没有任何报错的失败"最难自查（本仓的老毛病，见 #254 静默投递）。
    """
    got = C.parse_card_callback({"outTrackId": "cid", "userId": "u",
                                 "cardActionData": {"params": {"action": word}}})
    assert got == ("cid", expected, "u")


@pytest.mark.parametrize("word", ["maybe", "cancel", "approve_later", "", "确认", "1"])
def test_vocabulary_stays_fail_closed(word):
    """词表放宽了，**方向没变**：不明确表态的一律不放行。"""
    assert C.parse_card_callback({"outTrackId": "cid", "userId": "u",
                                  "cardActionData": {"params": {"action": word}}}) is None


def test_parse_official_sdk_shape():
    """官方 SDK 里那条**真实**形状——这是唯一有出处的一条，别让重构把它顺手改没了。

    对照 open-dingtalk/dingtalk-stream-sdk-go 的 ``card.CardRequest``：顶层 outTrackId/userId，
    按钮参数在 cardActionData.cardPrivateData.params，参数名由模板自定（官方示例用 action）。
    上面那组多形状用例是防御性的猜测，这一条不是。
    """
    frame = {
        "outTrackId": "cid-real", "userId": "u-real", "corpId": "c1",
        "spaceType": "IM_ROBOT", "userIdType": 1,
        "cardActionData": {"cardPrivateData": {"actionIds": ["btn_ok"],
                                               "params": {"action": "approve"}}},
    }
    assert C.parse_card_callback(frame) == ("cid-real", True, "u-real")
    frame["cardActionData"]["cardPrivateData"]["params"]["action"] = "deny"
    assert C.parse_card_callback(frame) == ("cid-real", False, "u-real")


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
    # 没有发送者身份——绝不能翻成"是主人"，否则 bridge 的白名单闸和审批闸全被架空
    {"outTrackId": "cid", "cardActionData": {"params": {"action": "approve"}}, "userId": ""},
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
    assert ev.sender_id == "owner-1" and ev.ack == "card"    # 身份如实申报，不缺省成主人
    # 帧处理阶段**零状态变更**——这帧还没过闸。过闸前就动状态，陌生帧就能吃掉
    # 主人的文本 y/n 兜底、把卡片刷成"已批准"的假象。
    assert a._awaiting_confirm == "cid3"
    assert card.settled == []
    await a.ack_callback(ev)                                 # bridge 过闸后的唯一动作
    assert a._awaiting_confirm is None                       # 此刻才消费待确认态
    assert card.settled == [("cid3", True)]                  # 此刻才刷终态


@pytest.mark.asyncio
async def test_stranger_card_click_is_still_gated_by_the_bridge(tmp_path):
    """陌生人点按钮 → 事件照常产出，但**由 bridge 丢掉**（审批只认主人）。

    这条走真 bridge，不是断言适配器自己判定——安全判定归 bridge 一处，
    适配器只如实申报事实（channel.py 的分工）。被丢掉的点击**什么都不许改**：
    卡片不许刷成"已批准"的假象，主人的文本 y/n 兜底也不许被吃掉。
    """
    from src.im.bridge import IMBridge
    from tests.unit.test_im_bridge import ScriptedLLM

    card = _FakeCard()
    a = DingTalkAdapter("c", "s", "owner-1", card_sender=card)
    a._awaiting_confirm = "cid4"
    bridge = IMBridge(str(tmp_path), a, "owner-1", channel="test", llm=ScriptedLLM("x"))

    import asyncio
    fut = asyncio.get_event_loop().create_future()
    bridge._pending["cid4"] = fut

    from src.im.channel import ChannelEvent
    await bridge._on_event(ChannelEvent(kind="callback", sender_id="stranger-9",
                                        callback_id="cid4", approved=True, ack="card"))
    assert not fut.done(), "陌生人的按钮点击批准了一次确认——审批闸被绕过"
    assert card.settled == []                    # 卡片没被刷成"已批准"的假象
    assert a._awaiting_confirm == "cid4"         # 主人的文本兜底还在

    await bridge._on_event(ChannelEvent(kind="callback", sender_id="owner-1",
                                        callback_id="cid4", approved=True, ack="card"))
    assert fut.done() and fut.result() is True               # 主人点才算数
    assert card.settled == [("cid4", True)]      # 过了闸，卡片才刷终态
    assert a._awaiting_confirm is None


@pytest.mark.asyncio
async def test_callback_frame_without_sender_is_dropped():
    """回调帧里没有发送者身份 → **整帧丢弃**，绝不冒充主人。

    旧实现写的是 ``sender_id=sender or owner_id``——把"认不出是谁"翻成"是主人"，
    bridge 的白名单闸和审批闸全部形同虚设。fail-closed 的方向：宁可按钮失效
    （文本 y/n 兜底还在），也不把一次无名交互记到主人头上。
    """
    card = _FakeCard()
    a = DingTalkAdapter("c", "s", "owner-1", card_sender=card)
    a._awaiting_confirm = "cid9"
    frame = json.dumps({
        "type": "CALLBACK",
        "headers": {"messageId": "m9", "topic": C.CARD_CALLBACK_TOPIC},
        "data": json.dumps({"outTrackId": "cid9",
                            "cardActionData": {"params": {"action": "approve"}}})})
    evs = await a._handle_frame(_WS(), frame)
    assert evs == []                             # 没有事件 → bridge 连见都见不到
    assert a._awaiting_confirm == "cid9"         # 文本兜底不被吃掉
    assert card.settled == []                    # 卡片不被刷成任何终态


def test_split_title_survives_empty_text():
    """空/全空白文案不许炸。

    旧实现在这里抛 StopIteration（协程里变 RuntimeError）——虽被 send_confirm 的
    降级兜住不丢确认，但按钮会**静默**消失。
    """
    assert C._split_title("") == ("需要你确认", "需要你确认")
    assert C._split_title("  \n\t ") == ("需要你确认", "需要你确认")
    payload = C.build_card_payload("T", "o", "cid", "")      # 整条拼装链也不许炸
    assert payload["cardData"]["cardParamMap"]["title"] == "需要你确认"


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
    """点完要把卡片刷成终态——按钮留着还能点，人会以为这条还等着自己。

    三条断言都是 2026-08-01 真机联调逼出来的，各对应一种"点了没反应"：
    """
    ok = C.build_update_payload("TPL", "cid", True)
    no = C.build_update_payload("TPL", "cid", False)

    # ① 漏 cardTemplateId → 真机 400 MissingcardTemplateId，卡片纹丝不动
    assert ok["cardTemplateId"] == "TPL"
    assert ok["outTrackId"] == "cid"

    # ② status 必须是裸词 agree/reject：官方审批模板拿它做按钮显示条件，
    #    传"✅ 已批准"这种话条件不成立，按钮不会变灰
    assert ok["cardData"]["cardParamMap"]["status"] == "agree"
    assert no["cardData"]["cardParamMap"]["status"] == "reject"

    # ③ 必须按 key 合并：整包替换会把 title/body 冲掉，卡片当场变空白
    assert ok["cardUpdateOptions"]["updateCardDataByKey"] is True


@pytest.mark.asyncio
async def test_settle_sends_the_template_id():
    """settle 要把模板 ID 带上——这是真机上"点了没反应"的根因，得从出站面钉住。"""
    seen: list = []

    async def _capture(url, payload, token):
        seen.append((url, payload))
        return {}

    await C.CardSender("TPL-9", "owner", post_fn=_capture).settle("cid", True)
    assert seen and seen[0][1]["cardTemplateId"] == "TPL-9"


@pytest.mark.asyncio
async def test_outcome_is_visible_without_relying_on_the_template():
    """结论要写进**标题**，不能只押在"模板配了显示条件"上。

    真机首配的模板就没配那个条件：钉钉返回成功、日志写着"已刷成终态"，
    而人看到的是**纹丝不动的卡片**，跟没点一样。反馈不该依赖模板配得对。
    """
    seen: list = []

    async def _capture(url, payload, token):
        seen.append(payload)
        return {}

    s = C.CardSender("TPL", "owner", post_fn=_capture)
    await s.send("要跑 rm -rf 吗\n正文细节", "cid1")
    await s.settle("cid1", True)

    params = seen[-1]["cardData"]["cardParamMap"]
    assert params["title"] == "✅ 已批准 · 要跑 rm -rf 吗"    # 原标题要留着，不能只剩个勾
    assert params["status"] == "agree"                      # 模板条件那条路照样走
    assert seen[-1]["cardUpdateOptions"]["updateCardDataByKey"] is True

    await s.settle("cid1", False)                           # 已消费过 → 退回只写 status
    assert "title" not in seen[-1]["cardData"]["cardParamMap"]


@pytest.mark.asyncio
async def test_title_cache_is_bounded():
    """常驻进程里这个缓存**不许无限长**——确认是低频动作，64 条足够覆盖在途的。"""
    async def _ok(url, payload, token):
        return {}

    s = C.CardSender("TPL", "owner", post_fn=_ok)
    for i in range(200):
        await s.send(f"第 {i} 次\n正文", f"cid{i}")
    assert len(s._titles) <= 64
    assert "cid199" in s._titles and "cid0" not in s._titles   # 丢的是最旧的


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
