"""Shared agent tool protocol.

Kept separate from main_agent so tool providers in other layers can construct
tools without importing the whole agent loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable


class ToolTurnYield(Exception):
    """A trusted tool durably suspended work; return control to its caller."""


@dataclass
class Tool:
    """A callable tool exposed to the main agent loop."""

    name: str
    description: str
    args: dict[str, str]
    handler: Callable[[dict], Awaitable[str]]
    read_only: bool = True
    untrusted_source: bool = False
    outward: bool = False
    external_content: bool = False
    required_capabilities: tuple[str, ...] = ()
    argument_schema: dict[str, dict] = field(default_factory=dict)
    yields_turn: bool = False  # Execute sequentially; later tools must not run after a suspension.
