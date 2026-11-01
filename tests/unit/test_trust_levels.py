"""授权级别（src/agents/trust.py + gate 集成）——档位只松确认频次，绝不松安全边界。

钉死三条：污点回合任何档位都不免确认；无人值守永远 ASK（fail-closed）；外部会话最高 READS。
"""

import pytest

from src.agents import trust
from src.agents.gate import make_confirm_gate
from src.agents.taint import mark_tainted, reset_taint


@pytest.fixture(autouse=True)
def _clean_taint():
    reset_taint()
    yield
    reset_taint()


def test_levels_are_ordered_and_unknown_is_strictest():
    assert trust.normalize("FULL") == trust.FULL
    assert trust.normalize("no-such-level") == trust.ASK
    assert trust.normalize(None) == trust.ASK


@pytest.mark.parametrize(
    "level,kind,expected",
    [
        (trust.ASK, trust.READ, False),
        (trust.ASK, trust.WRITE, False),
        (trust.READS, trust.READ, True),
        (trust.READS, trust.WRITE, False),
        (trust.READS, trust.EXECUTE, False),
        (trust.FULL, trust.READ, True),
        (trust.FULL, trust.WRITE, True),
        (trust.FULL, trust.EXECUTE, True),
        (trust.FULL, trust.DELIVER, True),
        # ADR-011：主人自己的可撤销数据与草稿在 READS 起免确认；不可逆与 INTERACT 任何档都问。
        (trust.ASK, trust.OWN_DATA, False),
        (trust.ASK, trust.DRAFT, False),
        (trust.READS, trust.OWN_DATA, True),
        (trust.READS, trust.DRAFT, True),
        (trust.READS, trust.DELIVER, False),
        (trust.FULL, trust.OWN_DATA, True),
        (trust.FULL, trust.DRAFT, True),
        (trust.FULL, trust.IRREVERSIBLE, False),
        (trust.FULL, trust.INTERACT, False),
    ],
)
def test_authorization_matrix(level, kind, expected):
    assert trust.pre_authorized(level, kind) is expected


def test_unknown_kind_counts_as_write():
    """工具忘了申报类别 → 按最重的算，宁可多问一次。"""
    assert trust.pre_authorized(trust.READS, "brand-new-tool") is False
    assert trust.pre_authorized(trust.READS, None) is False


@pytest.mark.parametrize(
    "profile,requested,expected",
    [
        ("local", trust.FULL, trust.FULL),
        ("external", trust.FULL, trust.READS),
        ("researcher", trust.FULL, trust.READS),
        ("unattended", trust.FULL, trust.ASK),
        ("unattended", trust.READS, trust.ASK),
        # None = 端没申报档案（TUI/CLI 直接建门）：不夹，授权由调用方自己判。
        (None, trust.FULL, trust.FULL),
        ("", trust.FULL, trust.ASK),
        ("something-new", trust.FULL, trust.ASK),
        ("local", trust.READS, trust.READS),
    ],
)
def test_capability_profile_is_the_ceiling(profile, requested, expected):
    assert trust.resolve(requested, profile) == expected


async def _decide(**kwargs):
    asked = []

    async def ask(message):
        asked.append(message)
        return True

    gate = make_confirm_gate(ask, can_ask_human=True, **kwargs)
    return gate, asked


async def test_full_trust_skips_the_prompt_for_writes():
    gate, asked = await _decide(trust_level=trust.FULL, capability_profile="local")
    assert await gate("写 todo.py", trust.WRITE) is True
    assert asked == []


async def test_reads_trust_still_asks_before_writing():
    gate, asked = await _decide(trust_level=trust.READS, capability_profile="local")
    assert await gate("读 todo.py", trust.READ) is True
    assert asked == []
    assert await gate("写 todo.py", trust.WRITE) is True
    assert asked == ["写 todo.py"]


async def test_tainted_turn_revokes_full_trust():
    """D0：读过外部内容后，最高授权也必须回到真人拍板。"""
    gate, asked = await _decide(trust_level=trust.FULL, capability_profile="local")
    mark_tainted()
    assert await gate("写 todo.py", trust.WRITE) is True
    assert len(asked) == 1
    assert "外部内容" in asked[0]


async def test_tainted_turn_without_a_human_denies_even_at_full_trust():
    gate = make_confirm_gate(None, trust_level=trust.FULL, capability_profile="local",
                             can_ask_human=False)
    mark_tainted()
    assert await gate("写 todo.py", trust.WRITE) is False


async def test_unattended_never_gets_a_free_pass():
    gate = make_confirm_gate(None, trust_level=trust.FULL, capability_profile="unattended",
                             can_ask_human=False)
    assert await gate("写 todo.py", trust.WRITE) is False
    assert await gate("读 todo.py", trust.READ) is False


async def test_auto_approve_still_behaves_like_full_trust():
    """CLI 的 --yes 语义不变（向后兼容）。"""
    gate, asked = await _decide(auto_approve=True, capability_profile="local")
    assert await gate("写 todo.py", trust.WRITE) is True
    assert asked == []


async def test_gate_default_kind_is_write():
    gate, asked = await _decide(trust_level=trust.READS, capability_profile="local")
    assert await gate("某个没申报类别的操作") is True
    assert asked == ["某个没申报类别的操作"]


async def test_external_session_cannot_be_dialed_up_to_write_without_asking():
    """Web/IM 会话天生要读外部内容：用户档位最高只到 READS，写仍要拍板。"""
    gate, asked = await _decide(trust_level=trust.FULL, capability_profile="external")
    assert await gate("写 todo.py", trust.WRITE) is True
    assert asked == ["写 todo.py"]


async def test_own_data_is_free_in_the_desktop_general_assistant():
    """Desktop 通用助理按 external 档案装配、上限 READS：改主人自己的事项不再每次确认。"""
    gate, asked = await _decide(trust_level=trust.READS, capability_profile="external")
    assert await gate("把「周五交周报」记进事项", trust.OWN_DATA) is True
    assert await gate("起草给张三的回复", trust.DRAFT) is True
    assert asked == []
    assert await gate("写 todo.py", trust.WRITE) is True
    assert asked == ["写 todo.py"]


async def test_tainted_turn_turns_own_data_back_into_a_question():
    """读过外部内容的回合里，改主人数据也要真人拍板——污点作废一切预授权，OWN_DATA 不例外。"""
    gate, asked = await _decide(trust_level=trust.READS, capability_profile="external")
    mark_tainted()
    assert await gate("把邮件里的要求记进事项", trust.OWN_DATA) is True
    assert len(asked) == 1 and "外部内容" in asked[0]


async def test_unattended_cannot_write_own_data_silently():
    """无人值守问不到人：OWN_DATA 也一样被拒，由调用方改成提议，不静默落地。"""
    gate = make_confirm_gate(None, trust_level=trust.READS, capability_profile="unattended",
                             can_ask_human=False)
    assert await gate("记一条事项", trust.OWN_DATA) is False
    assert await gate("起草周报", trust.DRAFT) is False


async def test_irreversible_always_asks_even_with_auto_approve():
    gate, asked = await _decide(auto_approve=True, capability_profile="local")
    assert await gate("删除这个分支", trust.IRREVERSIBLE) is True
    assert asked == ["删除这个分支"]
