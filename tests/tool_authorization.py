"""Explicit authorization for offline tool implementation fixtures.

Callers choose a simulated human or a clean FULL grant. Security tests keep their
own gates, including absent channels and rejections; existing bindings are kept.
"""
from src.agents.gate import make_confirm_gate


async def approve_fixture(message, kind=None):
    return True


def authorized_tools(value, *, human=True):
    gate = make_confirm_gate(approve_fixture if human else None,
                             auto_approve=True, can_ask_human=human)
    items = value if isinstance(value, (tuple, list)) else [value]
    for tool in items:
        if tool.confirm is None:
            tool.confirm = gate
    return value
