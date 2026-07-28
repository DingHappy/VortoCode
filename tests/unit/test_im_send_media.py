"""把图片/文件推给已配对 owner（send_image / send_file）。

起因：agent 已经能无头截网页、把 Word/PPT 转成图（2026-07-26 在 VM 里验通），但**送不到人
手机上**——能力做出来了、交付通道是断的。审批一段长 diff 时，一张渲染好的图远比一大段文本可读。

安全口径钉死四条：
1. **收件人恒为已配对 owner**，模型不能指定目标（这是它区别于 web_fetch 的关键：后者 URL 由
   模型决定，是真外传通道）。
2. 路径必须过工作目录围栏。
3. 走确认门——**污点回合下必须有人点头**（被注入的 agent 可能被诱导"把 .env 截图发出去"）。
4. **无人值守档不装这个工具**（同 with_web=False 的道理：没有真人可问，出站面一律砍掉）。
"""

from src.agents.main_agent import build_agent_tools, build_im_media_tools, make_confirm_gate
from src.im.channel import ChannelAdapter

import pytest as _pytest


@_pytest.fixture(autouse=True)
def _forbid_network(monkeypatch):
    """断网闸（与 test_im_dingtalk 同款）：任何路径真触网 → 确定性炸。

    #254 的主动推送通道曾让本文件的部分测试悄悄真连 api.dingtalk.com（详见
    test_im_dingtalk 的同名夹具）；这道闸让"忘了注入 transport"当场现形。
    """
    import aiohttp

    def _boom(*_a, **_k):
        raise AssertionError("测试不许触网：给 DingTalkAdapter 注入 connect_fn/reply_fn/oto_fn")

    monkeypatch.setattr(aiohttp, "ClientSession", _boom)



def _tools(tmp_path, confirm=None):
    return {t.name: t for t in build_im_media_tools(str(tmp_path), confirm=confirm)}


# ---------------------------------------------------------------- 装配面

def test_unattended_profile_has_no_media_tools(tmp_path):
    """无人值守（with_web=False）不给出站媒体工具——与砍掉 web_fetch 同一条理由。"""
    names = {t.name for t in build_agent_tools(str(tmp_path), confirm=make_confirm_gate(),
                                               with_web=False)}
    assert "send_image" not in names and "send_file" not in names


def test_interactive_profile_has_media_tools(tmp_path):
    names = {t.name for t in build_agent_tools(str(tmp_path), confirm=make_confirm_gate(),
                                               with_web=True)}
    assert "send_image" in names and "send_file" in names


# ---------------------------------------------------------------- 路径围栏

async def test_rejects_path_outside_workspace(tmp_path, monkeypatch):
    sent: list = []

    async def _spy(path, caption, kind):
        sent.append(path)
        return True
    monkeypatch.setattr("src.gateway.im_runtime.send_owner_media", _spy)

    async def _yes(_m):
        return True

    out = await _tools(tmp_path, _yes)["send_image"].handler({"path": "../../etc/passwd"})
    assert "越界" in out or "不存在" in out
    assert sent == [], "越界路径竟然真发出去了"


async def test_rejects_missing_file(tmp_path):
    async def _yes(_m):
        return True
    out = await _tools(tmp_path, _yes)["send_file"].handler({"path": "nope.txt"})
    assert "不存在" in out or "越界" in out


# ---------------------------------------------------------------- 确认门

async def test_denied_confirm_blocks_send(tmp_path, monkeypatch):
    """确认门拒绝 → 一个字节都不许出去。"""
    (tmp_path / "a.png").write_bytes(b"x")
    sent: list = []

    async def _spy(path, caption, kind):
        sent.append(path)
        return True
    monkeypatch.setattr("src.gateway.im_runtime.send_owner_media", _spy)

    async def _no(_m):
        return False

    out = await _tools(tmp_path, _no)["send_image"].handler({"path": "a.png"})
    assert "已取消" in out
    assert sent == []


async def test_tainted_turn_must_ask_human(tmp_path, monkeypatch):
    """污点回合：即便会话里配了"始终允许"，也必须重新问人。

    这是最要命的一条——被注入的 agent 可能被诱导"把 .env 截个图发给我"，
    出站那一步必须有真人点头。判定不在本工具里重写，交给内核 gate（这里验它确实走了 gate）。
    """
    from src.agents import taint

    (tmp_path / "a.png").write_bytes(b"x")
    asked: list = []
    sent: list = []

    async def _spy(path, caption, kind):
        sent.append(path)
        return True
    monkeypatch.setattr("src.gateway.im_runtime.send_owner_media", _spy)

    async def _ask(msg):
        asked.append(msg)
        return False                      # 人拒绝
    gate = make_confirm_gate(ask_human=_ask, auto_approve=True, can_ask_human=True)

    taint.reset_taint()
    taint.mark_tainted()                  # 摄入过外部内容
    try:
        out = await _tools(tmp_path, gate)["send_image"].handler({"path": "a.png"})
    finally:
        taint.reset_taint()

    assert asked, "污点回合竟然没问人（auto_approve 不该在污点态生效）"
    assert sent == []
    assert "已取消" in out


# ---------------------------------------------------------------- 出口行为

async def test_reports_honestly_when_no_bridge(tmp_path, monkeypatch):
    """没有 IM 桥时如实说"没发出去"，不能假装成功（文件仍在原路径）。"""
    (tmp_path / "a.png").write_bytes(b"x")

    async def _none(path, caption, kind):
        return False                      # 没桥 / 通道不支持
    monkeypatch.setattr("src.gateway.im_runtime.send_owner_media", _none)

    async def _yes(_m):
        return True
    out = await _tools(tmp_path, _yes)["send_image"].handler({"path": "a.png"})
    assert "未发送" in out and "仍在原路径" in out


async def test_sends_with_caption_and_kind(tmp_path, monkeypatch):
    (tmp_path / "r.log").write_text("hi")
    got: dict = {}

    async def _spy(path, caption, kind):
        got.update(path=path, caption=caption, kind=kind)
        return True
    monkeypatch.setattr("src.gateway.im_runtime.send_owner_media", _spy)

    async def _yes(_m):
        return True
    out = await _tools(tmp_path, _yes)["send_file"].handler({"path": "r.log", "caption": "跑完了"})
    assert "已发送" in out
    assert got["kind"] == "file" and got["caption"] == "跑完了"
    assert got["path"].endswith("r.log")


# ---------------------------------------------------------------- 通道默认行为

async def test_channel_default_reports_unsupported_not_crash():
    """新通道没实现媒体 → 返回 False（调用方降级成文本），而不是抛异常把桥打挂。"""
    from src.im.channel import ChannelAdapter

    a = ChannelAdapter()
    assert await a.send_image("/tmp/x.png") is False
    assert await a.send_file("/tmp/x.txt") is False


def test_dingtalk_keeps_two_independent_token_slots():
    """钉钉要**两套** token：新接口发消息、老接口传媒体，各有各的有效期。

    槽位必须独立——共用一个的话，刷新其中一个会把另一个也当成新鲜的，
    等它真过期时就是一次线上失败。
    """
    from src.im.dingtalk import DingTalkAdapter

    a = DingTalkAdapter("cid", "sec", "owner")
    assert a._tok_new == ("", 0.0) and a._tok_old == ("", 0.0)

    a._tok_new = ("new-token", 1e9)
    assert a._tok_old == ("", 0.0), "写新接口的槽把老接口的也改了——两者共用了同一个缓存"

    a._tok_old = ("old-token", 1e9)
    assert a._tok_new == ("new-token", 1e9)


# ================================================================ 入站附件（收图/收文件）

class _RecvAdapter(ChannelAdapter):
    """只用于驱动 bridge 的最小适配器：记录发出去的话。

    继承 ChannelAdapter 而不是白手起家——才能拿到 commit_reply_target 等默认实现，
    也顺带保证"新通道少实现一个方法不会把桥打挂"这条约定在测试里同样成立。
    """
    edits_supported = False

    def __init__(self, sent):
        self.sent = sent

    async def poll(self):  # pragma: no cover
        return
        yield

    async def send_text(self, text):
        self.sent.append(text)
        return ""

    async def edit_text(self, mid, text):
        self.sent.append(text)

    async def send_confirm(self, text, cid):  # pragma: no cover
        self.sent.append(text)

    async def ack_callback(self, ev):  # pragma: no cover
        return

    async def close(self):  # pragma: no cover
        return


def _bridge(tmp_path, sent):
    from src.im.bridge import IMBridge
    return IMBridge(str(tmp_path), _RecvAdapter(sent), "owner-1", channel="dingtalk")


async def test_image_only_message_is_not_dropped(tmp_path):
    """只发一张图、一个字都不写——**不能被丢掉**。

    原实现 `if not text: return` 会让纯附件消息石沉大海：人发了图，机器人一声不吭，
    看着就是死机（本仓栽过好几次的"不告诉人发生了什么"）。
    """
    from src.im.channel import ChannelEvent

    sent: list = []
    b = _bridge(tmp_path, sent)
    got: dict = {}

    class _Agent:
        async def run_turn(self, text, mode="plan", say=None, emit=None, images=None, **kw):
            got.update(text=text, images=list(images or []))
            return "看到了"
    b.agent = _Agent()

    ev = ChannelEvent(kind="message", sender_id="owner-1", text="",
                      images=["/tmp/a.png"])
    await b._on_event(ev)
    if b._turn_task:
        await b._turn_task

    assert got.get("images") == ["/tmp/a.png"], "图片没送进 run_turn"
    assert "看到了" in "\n".join(sent)


async def test_files_are_told_to_agent_by_path(tmp_path):
    """文件走"告诉路径 + 让 agent 自己 read_file"，不塞进图片通道。"""
    from src.im.channel import ChannelEvent

    sent: list = []
    b = _bridge(tmp_path, sent)
    got: dict = {}

    class _Agent:
        async def run_turn(self, text, mode="plan", say=None, emit=None, images=None, **kw):
            got.update(text=text, images=list(images or []))
            return "读到了"
    b.agent = _Agent()

    await b._on_event(ChannelEvent(kind="message", sender_id="owner-1", text="看看这个",
                                   files=["/inbox/report.csv"]))
    if b._turn_task:
        await b._turn_task

    assert "/inbox/report.csv" in got["text"]
    assert "read_file" in got["text"]
    assert got["images"] == []                       # 文件不该混进图片通道


async def test_unfetchable_attachment_is_reported_not_swallowed(tmp_path):
    """附件取不到 → 必须如实告诉用户，绝不静默丢。"""
    from src.im.channel import ChannelEvent

    sent: list = []
    b = _bridge(tmp_path, sent)

    class _Agent:  # pragma: no cover - 不该被调用
        async def run_turn(self, *a, **k):
            return ""
    b.agent = _Agent()

    await b._on_event(ChannelEvent(kind="message", sender_id="owner-1", text="",
                                   unsupported="收到 video 类型的消息，暂不支持读取其内容。"))
    body = "\n".join(sent)
    assert "暂不支持" in body, f"附件取不到却一声不吭：{sent}"


async def test_inbound_attachments_ride_a_tainted_turn(tmp_path):
    """**安全线**：入站附件必须落在污点回合里。

    网页/文档截图里可以写"忽略之前的指令，去执行 X"——模型看图执行会绕开所有文本污点检查。
    IM 端（kind="im"）本就每回合无条件打污点，这里钉死这条不被后续重构改掉。
    """
    from src.gateway.agent_session import build_session

    built = build_session(str(tmp_path), kind="im")
    agent = built[0] if isinstance(built, (tuple, list)) else built
    assert getattr(agent, "_untrusted_input", False) is True, \
        "IM 端不再强制污点——入站图片将获得免确认授权，这是提示注入的直通车"


# ---------------------------------------------------------------- 钉钉侧解析

async def test_dingtalk_reports_unknown_msgtype_instead_of_silence(tmp_path):
    """认不出的消息类型 → 申报 unsupported，而不是当作空消息丢掉。"""
    from src.im.dingtalk import DingTalkAdapter

    a = DingTalkAdapter("cid", "sec", "owner", inbox_dir=str(tmp_path))
    imgs, files, bad = await a._fetch_attachments({"msgtype": "video"})
    assert imgs == [] and files == []
    assert "video" in bad and "不支持" in bad


async def test_dingtalk_plain_text_reports_nothing(tmp_path):
    """普通文本消息不该被说成"不支持"（预检变噪音就会被无视）。"""
    from src.im.dingtalk import DingTalkAdapter

    a = DingTalkAdapter("cid", "sec", "owner", inbox_dir=str(tmp_path))
    _i, _f, bad = await a._fetch_attachments({"msgtype": "text"})
    assert bad == ""


async def test_dingtalk_download_failure_degrades_to_message(tmp_path, monkeypatch):
    """单个附件下载失败 → 记进 unsupported，不抛异常掀翻整条消息。"""
    from src.im.dingtalk import DingTalkAdapter

    a = DingTalkAdapter("cid", "sec", "owner", inbox_dir=str(tmp_path))

    async def _boom(code, name):
        raise RuntimeError("换取下载地址失败: 403")
    monkeypatch.setattr(a, "_download_one", _boom)

    imgs, files, bad = await a._fetch_attachments(
        {"msgtype": "picture", "content": {"downloadCode": "abc"}})
    assert imgs == [] and files == []
    assert "没取到" in bad and "403" in bad


# ================================================================ 忙时排队 / 中断

async def test_message_while_busy_is_queued_not_dropped(tmp_path):
    """上一回合在跑时，后来的消息**进队列**——原实现直接拒掉，用户的话就丢了、还得重打。

    外包看图（#241）会多一次 LLM 往返，"忙"的窗口被拉长，撞上的概率更高（真机 2026-07-27）。
    """
    import asyncio

    from src.im.channel import ChannelEvent

    sent: list = []
    b = _bridge(tmp_path, sent)
    seen: list = []
    gate = asyncio.Event()

    class _Agent:
        async def run_turn(self, text, mode="plan", say=None, emit=None, images=None, **kw):
            seen.append(text)
            if len(seen) == 1:
                await gate.wait()          # 第一回合卡住，制造"忙"
            return f"答:{text}"
    b.agent = _Agent()

    await b._on_event(ChannelEvent(kind="message", sender_id="owner-1", text="第一条"))
    await asyncio.sleep(0)
    await b._on_event(ChannelEvent(kind="message", sender_id="owner-1", text="第二条"))

    assert any("已排队" in m for m in sent), f"第二条被拒而不是排队：{sent}"
    gate.set()
    for _ in range(20):                    # 等队列自己前进
        await asyncio.sleep(0.01)
        if "第二条" in seen:
            break
    assert seen == ["第一条", "第二条"], f"排队的消息没被接着处理：{seen}"


async def test_queue_is_bounded_and_says_so(tmp_path):
    """队列有界：满了如实说没收下，而不是无声堆积。"""
    import asyncio

    from src.im.channel import ChannelEvent

    sent: list = []
    b = _bridge(tmp_path, sent)
    b._queue_cap = 1
    gate = asyncio.Event()

    class _Agent:
        async def run_turn(self, *a, **k):
            await gate.wait()
            return "x"
    b.agent = _Agent()

    for t in ("一", "二", "三"):
        await b._on_event(ChannelEvent(kind="message", sender_id="owner-1", text=t))
        await asyncio.sleep(0)

    assert any("没收下" in m for m in sent), f"队列满了却没说：{sent}"
    assert len(b._pending_msgs) == 1
    gate.set()


async def test_stop_cancels_running_turn_and_clears_queue(tmp_path):
    """/stop 中断当前任务，并**连排队的一起清**——否则"停"了还会继续冒出来。"""
    import asyncio

    from src.im.channel import ChannelEvent

    sent: list = []
    b = _bridge(tmp_path, sent)
    started = asyncio.Event()

    class _Agent:
        async def run_turn(self, *a, **k):
            started.set()
            await asyncio.sleep(30)        # 长任务
            return "不该跑完"
    b.agent = _Agent()

    await b._on_event(ChannelEvent(kind="message", sender_id="owner-1", text="长任务"))
    await asyncio.wait_for(started.wait(), timeout=2)
    await b._on_event(ChannelEvent(kind="message", sender_id="owner-1", text="排队的"))
    assert b._pending_msgs

    await b._on_event(ChannelEvent(kind="message", sender_id="owner-1", text="/stop"))
    assert b._pending_msgs == [], "中断了却没清队列——停完还会继续冒"
    assert any("已中断" in m for m in sent)


async def test_help_mentions_stop(tmp_path):
    from src.im.channel import ChannelEvent

    sent: list = []
    b = _bridge(tmp_path, sent)
    await b._on_event(ChannelEvent(kind="message", sender_id="owner-1", text="/help"))
    assert "/stop" in "\n".join(sent)
