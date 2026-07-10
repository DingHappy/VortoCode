"""Pytest 共享夹具（全套件）。"""
import pytest


@pytest.fixture(autouse=True)
def _default_prompt_protocol(monkeypatch):
    """把测试套件默认钉在**提示式**工具协议（VORTOCODE_NATIVE_TOOLS=0）。

    2026-07 起 native_default() 默认**开**（真机对照 dogfood：native 3/3 正确落地 vs 提示式 1/3，
    对 mimo 明显更可靠）。但大量 loop-mechanics 测试用脚本化 LLM 输出**提示式 JSON** 工具调用
    （`{"tool":...}`）——若默认走 native，这些 JSON 不会被当工具调用解析、测试全崩。故此处把套件
    默认钉在提示式，保证确定性、匹配这些 stub。

    需要另一种行为的用例自行覆盖：显式测 native 的传 `native=True` 或 `setenv(...,"1")`；测
    `native_default()` 真实默认的用例先 `delenv`（去掉这里设的 0 → 读到默认开）。
    """
    monkeypatch.setenv("VORTOCODE_NATIVE_TOOLS", "0")


@pytest.fixture(autouse=True)
def _default_dev_review_off(monkeypatch):
    """把测试套件默认钉在**关闭** dev_auto 的 PR 前审查段（VORTOCODE_DEV_REVIEW=0）。

    生产默认**开**，但审查段会另起 reviewer 子 agent（需 LLM），且给 dev_auto 输出追加审查注记——
    会干扰无关的 dev_auto 单测。审查段有自己的专门测试（test_review.py 直接测 run_gate）。测真实默认
    的用例先 `delenv`（去掉这里设的 0 → 读到默认开）。
    """
    monkeypatch.setenv("VORTOCODE_DEV_REVIEW", "0")


@pytest.fixture(autouse=True)
def _explicit_test_sandbox_off(monkeypatch):
    """Unit fixtures execute tiny local commands, so authorize host execution explicitly.

    Production defaults to ``auto`` and unattended generated-code paths fail closed when
    no Seatbelt/bubblewrap backend is available. The sandbox policy and argv construction
    have dedicated tests which delete/override this variable as needed; unrelated unit
    tests should not depend on the CI runner having bubblewrap installed.
    """
    monkeypatch.setenv("VORTOCODE_SANDBOX", "off")
