"""IM 进度推送——不支持编辑的通道只发增量，别把说过的话再说一遍。

真机 2026-07-27（钉钉）：一个回合里两次进度推送，第二条完整重复了第一条的内容：

    🏃 🔧 web_search query=今天科技新闻 2026年7月27日
    🏃 🔧 web_search query=今天科技新闻 2026年7月27日
       🔧 web_search query=2026年7月27日 科技新闻 人工智能 AI

成因：`_push_progress` 一律发累计窗口 `lines[-12:]`。这对**支持编辑**的通道是对的
（原地改同一条消息 → 滚动展示最近 12 行）；但钉钉不支持编辑，`edit_text` 内部是**发新消息**，
于是第 N 次推送把前 N-1 次的内容全重发一遍——工具越多重复越长。
"""

import pytest

from src.im.bridge import IMBridge
from src.im.channel import ChannelAdapter

OWNER = "owner-1"


class _Adapter(ChannelAdapter):
    """记录收发；edits_supported 决定 edit_text 是原地改还是发新消息（钉钉是后者）。"""

    def __init__(self, edits: bool):
        self.edits_supported = edits
        self.owner_id = OWNER
        self.msgs: list = []               # 用户实际**看到**的每一条

    async def send_text(self, text):
        self.msgs.append(text)
        return f"mid-{len(self.msgs)}"

    async def edit_text(self, mid, text):
        if self.edits_supported:
            self.msgs[int(str(mid).split("-")[1]) - 1] = text     # 原地改
        else:
            self.msgs.append(text)                                # 钉钉：发新消息


def _bridge(tmp_path, edits):
    class _LLM:
        async def chat(self, messages, **kwargs):
            return {"content": "x"}

    b = IMBridge(str(tmp_path), _Adapter(edits), OWNER, channel="test", llm=_LLM())
    b._progress_interval = 0.0             # 关掉节流，专测内容而非时序
    return b


async def _drive(bridge, steps):
    """喂 steps 条进度，返回用户看到的消息列表。"""
    pid, last, sent, lines = None, 0.0, 0, []
    for s in steps:
        lines.append(s)
        pid, last, sent = await bridge._push_progress(pid, lines, last, sent)
    return bridge.adapter.msgs


# ---------------------------------------------------------------- 钉钉（不支持编辑）
async def test_non_editing_channel_never_repeats_itself(tmp_path):
    """真机复现：三步进度，后面的消息不许包含前面已经说过的那一行。"""
    steps = ["🔧 web_search query=今天科技新闻", "🔧 web_search query=科技新闻 AI", "🔧 web_fetch url=..."]
    msgs = await _drive(_bridge(tmp_path, edits=False), steps)

    assert len(msgs) == 3
    for i, m in enumerate(msgs):
        assert steps[i] in m, f"第 {i} 条没带上本次的新进度：{m}"
        for earlier in steps[:i]:
            assert earlier not in m, f"第 {i} 条重复了之前说过的「{earlier}」：{m}"


async def test_non_editing_channel_batches_what_throttling_held_back(tmp_path):
    """节流跳过的行不能丢——下次一并发出去。"""
    b = _bridge(tmp_path, edits=False)
    lines = ["a"]
    pid, last, sent = await b._push_progress(None, lines, 0.0, 0)

    b._progress_interval = 9999.0                     # 之后全部被节流挡住
    for s in ("b", "c"):
        lines.append(s)
        pid, last, sent = await b._push_progress(pid, lines, last, sent)
    assert b.adapter.msgs == ["🏃 a"], "被节流的行不该提前发出去"

    b._progress_interval = 0.0
    lines.append("d")
    await b._push_progress(pid, lines, last, sent)
    assert "b" in b.adapter.msgs[-1] and "c" in b.adapter.msgs[-1] and "d" in b.adapter.msgs[-1], \
        "节流期间攒下的行被丢了"
    assert "a" not in b.adapter.msgs[-1], "又把首条重发了一遍"


async def test_no_new_lines_sends_nothing(tmp_path):
    """没有新东西就别推一条空进度（重推同一份内容也算噪音）。"""
    b = _bridge(tmp_path, edits=False)
    pid, last, sent = await b._push_progress(None, ["a"], 0.0, 0)
    await b._push_progress(pid, ["a"], last, sent)
    assert b.adapter.msgs == ["🏃 a"]


# ---------------------------------------------------------------- Telegram（支持编辑）
async def test_editing_channel_keeps_the_rolling_window(tmp_path):
    """支持编辑的通道语义相反：一条消息原地滚动，必须保留累计窗口。"""
    msgs = await _drive(_bridge(tmp_path, edits=True), ["a", "b", "c"])
    assert len(msgs) == 1, "编辑通道不该多发消息"
    assert all(x in msgs[0] for x in ("a", "b", "c")), "滚动窗口被砍成增量了"


async def test_editing_channel_window_is_capped(tmp_path):
    msgs = await _drive(_bridge(tmp_path, edits=True), [str(i) for i in range(20)])
    assert "19" in msgs[0] and "7" not in msgs[0].split("\n"), "窗口上限 12 行失效"


# ---------------------------------------------------------------- 端差异来自通道声明，不是硬编码
@pytest.mark.parametrize("edits, interval", [(True, 2.0), (False, 10.0)])
def test_throttle_follows_channel_edit_support(tmp_path, edits, interval):
    """不支持编辑 = 每次都是新消息 → 节流必须更慢，否则刷屏。"""
    assert _bridge(tmp_path, edits)._progress_interval == 0.0     # 被测试改过
    b = IMBridge(str(tmp_path), _Adapter(edits), OWNER, channel="test", llm=None)
    assert b._progress_interval == interval
