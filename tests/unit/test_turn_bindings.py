"""TurnBindings：Web/IM 共用的回合回调换绑点。未绑定时确认必须拒绝，其余事件静默丢弃。"""
import pytest

from src.gateway.turn_bindings import SLOTS, TurnBindings


async def test_unbound_confirm_is_denied_and_events_are_dropped():
    bindings = TurnBindings()
    assert await bindings.ask("删库？") is False
    bindings.emit_progress("x")
    bindings.emit_diff("t", "d")
    bindings.emit_workspace_required("project", "r", "t")
    bindings.emit_tool_event("start", {})
    bindings.emit_plan([])


async def test_bind_routes_to_current_turn_and_unbind_restores_fail_closed():
    bindings = TurnBindings()
    seen = []

    async def yes(message):
        seen.append(message)
        return True

    bindings.bind(confirm=yes, progress=seen.append)
    assert await bindings.ask("写文件？") is True
    bindings.emit_progress("进度")
    assert seen == ["写文件？", "进度"]
    bindings.unbind("confirm")
    assert await bindings.ask("再写？") is False and bindings.progress is not None
    bindings.unbind()
    assert all(getattr(bindings, slot) is None for slot in SLOTS)


def test_unknown_slot_is_rejected():
    with pytest.raises(TypeError):
        TurnBindings().bind(confrim=lambda m: True)
    with pytest.raises(TypeError):
        TurnBindings().unbind("confrim")
