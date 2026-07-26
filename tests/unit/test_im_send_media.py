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
