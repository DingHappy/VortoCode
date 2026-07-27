"""污点措辞按来源分档——判定不变，别把「你自己打了句话」说成「模型读过被投毒的网页」。

真机 2026-07-27：主人在钉钉打「手动跑一次 daily-tech-news」，确认框顶着一句

    ⚠ 本回合已摄入外部内容（网页/搜索/MCP）。下面这个操作是模型在读过外部内容之后提出的
      ——请人工核对是否确是你的本意（防提示注入）

他根本没让它读任何网页。IM 每回合都从「入口不可信」起步打污点，于是这句重话**每一次确认
都出现**。永远亮着的红灯等于没有红灯：人学会闭眼点同意，等真有一次是投毒网页诱导的，
那条横幅和前面九十九条一模一样。**把狼来了喊成日常，就等于拆掉了这道防线。**

所以分两档措辞，但**放行判定一个字不动**——两种来源一律算污点。这是本文件要同时钉住的
两件事：措辞要分，判定不许分。
"""

import pytest

from src.agents import taint
from src.agents.gate import ALLOW, ASK, CHANNEL_TAINT_NOTE, DENY, TAINT_WARNING, decide, taint_prefix


@pytest.fixture(autouse=True)
def _clean():
    taint.reset_taint()
    yield
    taint.reset_taint()


# ---------------------------------------------------------------- 措辞分档
def test_clean_round_has_no_prefix():
    assert taint_prefix() == ""


def test_real_ingestion_still_shouts():
    taint.mark_tainted()
    assert taint_prefix() == TAINT_WARNING


def test_channel_only_gets_the_quiet_note():
    """IM 入口不可信、但本回合没读网页 → 轻提示，不喊"模型读过外部内容"。"""
    taint.mark_channel_untrusted()
    prefix = taint_prefix()
    assert CHANNEL_TAINT_NOTE in prefix
    assert "网页/搜索/MCP" not in prefix, "把没发生的事说成发生了"
    assert "读过外部内容" not in prefix


def test_channel_then_real_ingestion_escalates():
    """IM 回合里模型真去搜了一次 → 必须升级成重话（这才是 D0 要防的那一刻）。"""
    taint.mark_channel_untrusted()
    taint.mark_tainted()
    assert taint_prefix() == TAINT_WARNING


def test_real_ingestion_is_never_watered_down():
    """已经读过网页，之后再打 channel 标记不许把它降级——真摄入不可撤销。"""
    taint.mark_tainted()
    taint.mark_channel_untrusted()
    assert taint.taint_source() == "external" and taint_prefix() == TAINT_WARNING


def test_reset_clears_both_bit_and_source():
    taint.mark_tainted()
    taint.reset_taint()
    assert taint.is_tainted() is False and taint.taint_source() == ""


# ---------------------------------------------------------------- 判定不许跟着分档 ⭐
@pytest.mark.parametrize("marker", [taint.mark_tainted, taint.mark_channel_untrusted])
def test_both_sources_count_as_tainted(marker):
    marker()
    assert taint.is_tainted() is True


@pytest.mark.parametrize("marker", [taint.mark_tainted, taint.mark_channel_untrusted])
def test_both_sources_void_auto_approve(marker):
    """措辞可以温和，**放行不许放松**：污点态下 --yes 一律失效，两种来源一视同仁。"""
    marker()
    assert decide(tainted=True, pre_authorized=True, can_ask_human=True) == ASK
    assert decide(tainted=True, pre_authorized=True, can_ask_human=False) == DENY


def test_clean_round_still_honors_auto_approve():
    assert decide(tainted=False, pre_authorized=True, can_ask_human=False) == ALLOW


# ---------------------------------------------------------------- 嵌套子 agent
def test_nested_child_ingestion_propagates_as_external():
    """子 agent 读过网页 → 父回合按 external 记（子的摄入是真摄入，不能被冲淡成 channel）。"""
    taint.mark_channel_untrusted()
    with taint.merge_nested_taint() as state:
        taint.mark_tainted()
    assert state.child_tainted is True
    assert taint.taint_source() == "external"


def test_nested_clean_child_leaves_parent_wording_alone():
    taint.mark_channel_untrusted()
    with taint.merge_nested_taint():
        pass
    assert taint.taint_source() == "channel"


def test_nested_does_not_leak_child_taint_into_a_clean_parent():
    with taint.merge_nested_taint() as state:
        taint.mark_channel_untrusted()
    assert state.child_tainted is True
    assert taint.is_tainted() is True, "子回合的污点必须并回父回合（单调合并）"


# ---------------------------------------------------------------- 端到端：确认门真正发出的文案 ⭐
#
# 上面那些测的是 taint_prefix() 这个**函数**。真机 2026-07-27 部署后发现重话照旧出现——
# 因为 make_confirm_gate 里绕过了它、自己拼 TAINT_WARNING 常量：**定义改好了，唯一的调用点漏了**。
# 所以这一组不测函数，测**用户真正收到的那句话**。

async def test_gate_message_uses_the_quiet_note_for_channel_taint():
    from src.agents.gate import make_confirm_gate

    box = []

    async def _ask(msg):
        box.append(str(msg))
        return True

    taint.mark_channel_untrusted()
    await make_confirm_gate(_ask, can_ask_human=True)("跑一次 daily-tech-news？")
    assert "网页/搜索/MCP" not in box[0], "确认门仍在把「你自己打了句话」说成「读过被投毒的网页」"
    assert CHANNEL_TAINT_NOTE in box[0]
    assert "跑一次 daily-tech-news？" in box[0], "操作原文被吃掉了"


async def test_gate_message_still_shouts_after_real_ingestion():
    from src.agents.gate import make_confirm_gate

    box = []

    async def _ask(msg):
        box.append(str(msg))
        return True

    taint.mark_tainted()
    await make_confirm_gate(_ask, can_ask_human=True)("开 PR？")
    assert box[0].startswith(TAINT_WARNING), "真摄入过外部内容却没喊重话——D0 横幅丢了"


async def test_gate_message_is_bare_on_a_clean_round():
    from src.agents.gate import make_confirm_gate

    box = []

    async def _ask(msg):
        box.append(str(msg))
        return True

    await make_confirm_gate(_ask, can_ask_human=True)("写文件？")
    assert box[0] == "写文件？", "干净回合不该有任何前缀"
