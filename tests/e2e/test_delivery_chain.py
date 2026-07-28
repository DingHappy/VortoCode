"""交付链路端到端验收——「一句话进来，结果送到人手机上」这条链整条是通的。

与单测的分工见 `src/gateway/canary.py` 的模块 docstring：单测证明每一段的行为正确，
这里证明**接缝**没断。2026-07-27 一天五个 bug 全是接缝断了而每段单测照常绿。

本文件分两类用例，缺一不可：

1. **主验收**（`test_*_lane`）：五条交付通路逐条跑通。
2. **阳性对照**（`test_*_detects_*`）：把 canary 自己的探测能力钉住。没有它们，探测逻辑一旦
   写坏（正则失效、断言面取错），受影响的通路会**静默地永远绿**——那正是本仓清理过一轮的
   安慰剂测试形态（"在它声称要防的回归里保持绿色"）。手工突变验证过一次不够，得钉在套件里。
"""

import pytest

from src.gateway import canary
from src.gateway.canary import (FakeChannel, Lane, ScriptedLLM, _bridge, _sandbox,
                                lane_cold_push, lane_confirm, lane_cron_delivery, lane_inbound,
                                lane_notify, lane_venue, run_canary, summarize)


@pytest.fixture
def box():
    """一次性工作区（含 native=0 / sandbox=off 的环境钉子，退出时全部还原）。"""
    with _sandbox() as b:
        yield b


# --------------------------------------------------------------- 主验收：五条通路
@pytest.mark.parametrize("lane_fn", [lane_inbound, lane_confirm, lane_venue,
                                     lane_notify, lane_cron_delivery, lane_cold_push])
async def test_lane_green(box, lane_fn):
    """每条交付通路单独跑——分开跑是为了红的时候一眼知道断在哪一截。"""
    lane = await lane_fn(box.root)
    assert lane.ok, f"{lane.name}：{lane.detail}"


async def test_full_canary_green():
    """`vc canary` 的完整口径（部署脚本据此拦上线）。"""
    lanes = await run_canary()
    ok, text = summarize(lanes)
    assert ok, text + "\n" + "\n".join(f"{ln.glyph} {ln.name}: {ln.detail}" for ln in lanes)


async def test_canary_exit_code_maps_to_red():
    """summarize 的红绿要能变成退出码——部署脚本只认非零。"""
    assert summarize([Lane("a", True, "")]) == (True, "交付链路 1/1 条贯通")
    ok, text = summarize([Lane("a", True, ""), Lane("b", False, "断了")])
    assert ok is False and "b" in text


# --------------------------------------------------------------- 阳性对照：探测能力本身
@pytest.mark.parametrize("leak", [
    "要动手请按 Tab 键切到 build",
    "按 Enter 确认",
    "按回车继续",
    "直接按 y 批准",
    "press Tab to switch",
    "用快捷键切换模式",
    "Ctrl+C 可中断",
])
async def test_venue_detector_catches_keyboard_hints(box, monkeypatch, leak):
    """场地泄漏探测器必须真的认得出各种"按某个键"的说法。

    做法是把泄漏注入到**出站文本**（最贴近真实故障：模型或某处硬编码把终端操作说给了聊天用户），
    然后断言 lane_venue 转红。正则改坏了这条会立刻炸——这就是它存在的全部意义。
    """
    lane = await lane_venue(box.root)
    assert lane.ok, f"基线本身就是红的，先修基线：{lane.detail}"

    real_bridge = canary._bridge

    def _leaky(root, chan, llm, **kw):
        br = real_bridge(root, chan, llm, **kw)
        original = br.agent._system

        def _patched(mode, _orig=original):
            return str(_orig(mode)) + "\n" + leak

        br.agent._system = _patched
        return br

    monkeypatch.setattr(canary, "_bridge", _leaky)
    leaked = await lane_venue(box.root)
    assert not leaked.ok, f"泄漏 {leak!r} 没被抓到——探测器失灵了"
    assert "键盘操作提示" in leaked.detail


async def test_venue_detector_allows_chat_reply_affordance(box):
    """反向对照：**回复 y** 是钉钉确认的正当交互（dingtalk.py send_confirm 就这么发的），
    不能被当成场地泄漏拦下来。探测器过严会逼着把真实交互改掉，比漏报更麻烦。"""
    for text in ["请回复 **y**（批准）或 **n**（拒绝）。", "回复 /mode build 切换模式"]:
        assert not any(p.search(text) for p in canary._VENUE_LEAKS), \
            f"{text!r} 是本端的正当交互，不该被判为场地泄漏"


async def test_notify_detector_catches_missing_im_lane(box, monkeypatch):
    """投递器的 IM 那一路断掉时，lane_notify 必须转红（#253 的机制）。"""
    lane = await lane_notify(box.root)
    assert lane.ok, f"基线本身就是红的：{lane.detail}"

    from src.gateway import im_runtime
    monkeypatch.setattr(im_runtime, "set_owner_notifier", lambda _fn: None)   # 注册被吞掉
    broken = await lane_notify(box.root)
    assert not broken.ok and "IM" in broken.detail


async def test_cron_detector_catches_missing_notify(box, monkeypatch):
    """`cron_run` 不传 notify 时必须转红——#253 的**确切**复现。

    这是整套 canary 里最该钉死的一条：当时 `run_job` / `make_notifier` / 工具本身的单测全绿，
    唯独没人测"这两段接上了没有"，于是作业跑完主人在钉钉一无所获。
    """
    lane = await lane_cron_delivery(box.root)
    assert lane.ok, f"基线本身就是红的：{lane.detail}"

    from src.gateway import cron as cron_mod
    real = cron_mod.run_job_by_name

    async def _no_notify(repo_root, name, **kw):        # 把 notify 吞掉 = 复现原 bug
        kw.pop("notify", None)
        return await real(repo_root, name, **kw)

    monkeypatch.setattr(cron_mod, "run_job_by_name", _no_notify)
    broken = await lane_cron_delivery(box.root)
    assert not broken.ok, "cron_run 没把结果推给任何人，canary 却是绿的——探测面取错了"
    assert "没推给任何人" in broken.detail


async def test_cold_push_detector_catches_silent_drop(box, monkeypatch):
    """阳性对照——载荷就是 2026-07-28 事故前的原实现，逐字放回去：

        if self._webhook: 发；否则什么都不做、返回成功。

    当时 lane_notify 是绿的（FakeChannel 永远发成功），静默丢发生在真适配器的通道选择里，
    整套 canary 拦不住那次事故。这条钉死：那段代码再回来，冷启动通路必须立刻红。
    """
    lane = await lane_cold_push(box.root)
    assert lane.ok, f"基线本身就是红的，先修基线：{lane.detail}"

    from src.im.dingtalk import DingTalkAdapter, _clip

    async def _old_broken(self, text):
        if self._webhook:
            await self._reply_fn(self._webhook,
                                 {"msgtype": "text", "text": {"content": _clip(text)}})
        return ""                # ← 没 webhook：静默不发，还报成功（事故原样）

    monkeypatch.setattr(DingTalkAdapter, "send_text", _old_broken)
    broken = await lane_cold_push(box.root)
    assert not broken.ok, "事故原代码放回去了，冷启动通路却还是绿的——canary 没看住这条"
    assert "无声蒸发" in broken.detail


async def test_confirm_detector_catches_open_gate(box, monkeypatch):
    """确认门失灵（写操作静默放行）时 lane_confirm 必须转红。"""
    lane = await lane_confirm(box.root)
    assert lane.ok, f"基线本身就是红的：{lane.detail}"

    from src.agents import gate as gate_mod
    monkeypatch.setattr(gate_mod, "decide", lambda **kw: gate_mod.ALLOW)
    broken = await lane_confirm(box.root)
    assert not broken.ok and "确认" in broken.detail


# --------------------------------------------------------------- harness 自身的性质
async def test_canary_never_touches_real_workspace():
    """canary 必须跑在临时目录里——部署后在 VM 上跑，绝不能碰真 `.vortocode/`（cron.yaml！）。"""
    from pathlib import Path
    with _sandbox() as b:
        assert "vc-canary-" in b.root, "工作区不是一次性临时目录"
        assert not (Path(b.root) / ".vortocode" / "cron.yaml").exists()
        root = b.root
    assert not Path(root).exists(), "跑完没清掉临时工作区"


async def test_canary_restores_owner_notifier():
    """跑完必须把 im_runtime 的投递口放回去——否则在 serve 进程里跑一次就把真桥的推送打聋。"""
    from src.gateway import im_runtime

    async def _sentinel(_t):
        return None

    im_runtime.set_owner_notifier(_sentinel)
    try:
        await run_canary()
        assert im_runtime._OWNER_NOTIFIER is _sentinel, "canary 跑完把别人的投递口留在自己身上了"
    finally:
        im_runtime.set_owner_notifier(None)


async def test_scripted_llm_is_offline():
    """ScriptedLLM 不得触网/触真模型——canary 要能在断网的门禁里跑。"""
    llm = ScriptedLLM("a", "b")
    assert (await llm.chat([]))["content"] == "a"
    assert (await llm.chat([]))["content"] == "b"
    assert (await llm.chat([]))["content"] == "b"        # 用尽后复读最后一条，不炸


async def test_fake_channel_outbound_covers_every_visible_surface():
    """出站面必须把三类都收进来——漏一类，那类里的泄漏就永远扫不到。"""
    chan = FakeChannel()
    await chan.send_text("正文")
    await chan.send_confirm("确认提问", "cid")
    await chan.edit_text("mid", "进度")
    assert set(chan.outbound()) == {"正文", "确认提问", "进度"}


async def test_bridge_uses_owner_config(box):
    """canary 必须建**主人那条桥**（with_dev=True）。

    用 with_dev=False（同事版研究员助手）会被"请先自述人设"截胡，回合根本起不来，
    五条链路里三条变成假超时——写这个 canary 时先踩了一遍，钉在这里防复发。
    """
    br = _bridge(box.root, FakeChannel(), ScriptedLLM("x"))
    assert br._with_dev is True
    assert "cron_run" in br.agent.tools, "主人那条桥应当有排班工具面"
