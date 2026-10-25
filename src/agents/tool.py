"""Shared agent tool protocol.

Kept separate from main_agent so tool providers in other layers can construct
tools without importing the whole agent loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from typing import Awaitable, Callable, Iterable


class ToolTurnYield(Exception):
    """A trusted tool durably suspended work; return control to its caller."""


@dataclass
class Tool:
    """A trusted callable and its policy metadata, independent of an agent core."""

    name: str
    description: str
    args: dict[str, str]
    handler: Callable[[dict], Awaitable[object]]
    read_only: bool = True
    untrusted_source: bool = False
    outward: bool = False
    external_content: bool = False
    required_capabilities: tuple[str, ...] = ()
    argument_schema: dict[str, dict] = field(default_factory=dict)
    yields_turn: bool = False  # Execute sequentially; later tools must not run after a suspension.
    input_schema: dict | None = None  # Full external JSON Schema; native/provided tools retain types and required fields.
    serial: bool = False  # Read-only tools that may ask for approval must not race confirmation cards.


def tool_definitions(tools: Iterable[Tool]) -> list[dict]:
    """Export declarations only; handlers and local policy never cross the wire.

    Full provider schemas retain their types/required fields. Legacy args keep
    their existing optional-string contract. Each export is an independent copy.
    """
    return [{"name": tool.name, "description": tool.description, "input_schema":
             deepcopy(tool.input_schema) if tool.input_schema is not None else {
                 "type": "object", "properties": {
                     name: {"type": "string", "description": description,
                            **deepcopy(tool.argument_schema.get(name, {}))}
                     for name, description in tool.args.items()}, "required": []}}
            for tool in tools]


def function_schemas(tools: Iterable[Tool]) -> list[dict]:
    """OpenAI-compatible function-calling adapter over the shared declarations."""
    return [{"type": "function", "function": {
        "name": item["name"], "description": item["description"],
        "parameters": item["input_schema"]}} for item in tool_definitions(tools)]
