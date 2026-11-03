"""可信类别贯穿内核门、终端和原审计；展示文案不能降级签名或授权。"""

import asyncio

import pytest

from src.agents.confirmation_context import approval_context, approval_details
from src.agents.gate import make_confirm_gate
from src.agents.taint import mark_tainted, reset_taint
from src.gateway.audit import list_audit, record_decision_audit


@pytest.mark.parametrize("kind,normal", [
    ("read", True), ("own_data", True), ("draft", True), ("write", False),
    ("execute", False), ("deliver", False), ("interact", False), ("irreversible", False),
    (None, False), ("READ", False), ("我只是只读", False), ([], False),
])
def test_only_known_low_risk_categories_use_the_daily_key(kind, normal):
    details = approval_details(kind)
    assert details["risk"] == ("normal" if normal else "high")
    assert details["required_key"] == ("device" if normal else "approval")


async def test_kernel_propagates_kind_and_leaves_typed_manual_audit(tmp_path):
    reset_taint()
    seen = []

    async def ask(text, kind):
        seen.append((text, kind, approval_context()))
        return True

    gate = make_confirm_gate(ask, can_ask_human=True, on_decision=lambda op, ok, taint:
        record_decision_audit(str(tmp_path), session="s", mode="build", operation=op,
                              decision=ok, tainted=taint))
    assert await gate("保存草稿（文案不能决定类别）", "deliver")
    assert seen[0][1] == "deliver" and seen[0][2]["risk"] == "high"
    [entry] = list_audit(str(tmp_path))
    assert (entry["operation_kind"], entry["decision_source"], entry["approval_scope"]) == ("deliver", "user", "once")
    assert approval_context() is None


async def test_legacy_callback_and_internal_typeerror_are_not_retried():
    reset_taint()
    calls = []

    async def legacy(text):
        calls.append(text)
        return True

    assert await make_confirm_gate(legacy, can_ask_human=True)("旧确认", "own_data")

    async def broken(text, kind):
        calls.append(kind)
        raise TypeError("inside callback")

    with pytest.raises(TypeError, match="inside callback"):
        await make_confirm_gate(broken, can_ask_human=True)("失败", "deliver")
    assert calls == ["旧确认", "deliver"]
    assert approval_context() is None


async def test_cancelled_wait_is_audited_as_cancellation_and_never_a_user_denial():
    reset_taint()
    waiting = asyncio.Event()
    decisions = []

    async def ask(_text, _kind):
        waiting.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(make_confirm_gate(ask, can_ask_human=True,
        on_decision=lambda _op, ok, _taint: decisions.append((ok, approval_context())))("操作", "deliver"))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(decisions) == 1 and decisions[0][0] is False
    assert decisions[0][1]["approval_outcome"] == decisions[0][1]["decision_source"] == "cancelled"
    assert approval_context() is None


async def test_taint_revokes_preapproval_and_context_does_not_leak_between_tasks(tmp_path):
    reset_taint()

    async def run(kind, taint):
        reset_taint()
        if taint:
            mark_tainted()

        async def ask(_text, actual_kind):
            await asyncio.sleep(0)
            assert approval_context()["operation_kind"] == actual_kind == kind
            return False

        def audit(op, ok, dirty):
            record_decision_audit(str(tmp_path), session=kind, mode="build", operation=op,
                                  decision=ok, tainted=dirty)

        return await make_confirm_gate(ask, can_ask_human=True, trust_level="full",
                                       on_decision=audit)("操作", kind)

    assert await asyncio.gather(run("own_data", False), run("draft", True), run("interact", False)) == [True, False, False]
    entries = {item["session"]: item for item in list_audit(str(tmp_path))}
    assert entries["own_data"]["decision_source"] == "preauthorized"
    assert entries["own_data"]["approval_scope"] == "existing_trust"
    assert entries["draft"]["decision_source"] == "user" and entries["draft"]["tainted"]
    assert entries["interact"]["decision_source"] == "user"
    assert approval_context() is None


def test_audit_preserves_legacy_absence_and_sanitizes_optional_metadata(tmp_path):
    fields = dict(session="legacy", mode="build", operation="old", decision=False, tainted=False)
    record_decision_audit(str(tmp_path), **fields)
    record_decision_audit(str(tmp_path), **fields, approval={"operation_kind": "invented",
        "risk": "normal", "required_key": [], "approval_scope": "forever", "secret": "never-save"})
    current, legacy = list_audit(str(tmp_path))
    assert "operation_kind" not in legacy
    assert current["operation_kind"] == current["required_key"] == current["approval_scope"] == "unknown"
    assert "never-save" not in (tmp_path / ".vortocode/audit.log").read_text()
