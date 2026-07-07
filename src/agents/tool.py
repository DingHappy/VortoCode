"""Shared agent tool protocol.

Kept separate from main_agent so tool providers in other layers can construct
tools without importing the whole agent loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable


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
