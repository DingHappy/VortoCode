"""Shared agent tool protocol.

Kept separate from main_agent so tool providers in other layers can construct
tools without importing the whole agent loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from typing import Awaitable, Callable, Iterable, Literal


class ToolTurnYield(Exception):
    """A trusted tool durably suspended work; return control to its caller."""


@dataclass(frozen=True)
class ToolResult:
    """Explicit text outcome from a trusted handler; never inferred from prose.

    Legacy strings and {text, images, ok} results remain supported. This value
    reports the invocation outcome, not acceptance of the user's whole task.
    """
    text: str
    status: Literal["succeeded", "failed", "blocked", "cancelled"] = "succeeded"

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "failed", "blocked", "cancelled"}:
            raise ValueError("Unknown tool result status")


@dataclass(frozen=True)
class ToolResource:
    """Trusted, display-only local observation; never invokes a tool or connects.

    Shared by tools from one adapter. The existing tool namespace owns discovery;
    this is not an execution API, credential store or independent registry.
    """
    id: str
    kind: str
    title: str
    observe: Callable[[], dict]


@dataclass(frozen=True, eq=False)
class InternalToolScope:
    """Trusted host binding; identity and domain checks cannot come from tool arguments."""
    name: str
    tools: frozenset[str]
    check: Callable[[str, dict], None]


@dataclass(frozen=True, eq=False)
class ApprovedTaskScope:
    """Host-issued permission for a particular disposable task workspace."""
    task_id: str
    workspace: str
    _identity: tuple[int, int] = field(init=False, repr=False)

    def __post_init__(self):
        from pathlib import Path
        observed = Path(self.workspace).resolve(strict=True).stat()
        object.__setattr__(self, "_identity", (observed.st_dev, observed.st_ino))

    def permits(self, tool: Tool, args: dict, kind: str) -> bool:
        from pathlib import Path
        if not self.task_id or tool.task_scope is not self:
            return False
        try:
            root = Path(self.workspace).resolve(strict=True)
            observed = root.stat()
            if (observed.st_dev, observed.st_ino) != self._identity or tool.execution_root != str(root):
                return False
            if tool.name == "run_tests":
                return kind == "execute"
            if tool.name not in {"edit_file", "write_file"} or kind != "write":
                return False
            path = Path(str(args.get("path") or ""))
            if path.is_absolute() or not str(path):
                return False
            (root / path).resolve().relative_to(root)
            return True
        except (OSError, ValueError, RuntimeError):
            return False


@dataclass(frozen=True)
class PreparedToolCall:
    """Read-only preparation followed by one precisely described action."""
    invoke: Callable[[], Awaitable[object]]
    message: str
    operation_kind: str | None = None
    denied_result: object = "主人没有确认，操作已取消。"
    approval_context: Callable | None = None
    on_denied: Callable[[], None] | None = None
    protective: bool = False


async def _prepared_only(args: dict) -> object:
    raise RuntimeError("Prepared tool has no unprepared execution path")


@dataclass
class Tool:
    """A trusted callable and its policy metadata, independent of an agent core."""

    name: str
    description: str
    args: dict[str, str]
    handler: Callable[[dict], Awaitable[object]]
    operation_kind: str = field(kw_only=True)
    read_only: bool = True
    untrusted_source: bool = False
    outward: bool = False
    external_content: bool = False
    required_capabilities: tuple[str, ...] = ()
    argument_schema: dict[str, dict] = field(default_factory=dict)  # Local explicit constraints; runtime checks the documented subset without coercion.
    yields_turn: bool = False  # Execute sequentially; later tools must not run after a suspension.
    input_schema: dict | None = None  # Full external JSON Schema; native/provided tools retain types and required fields.
    serial: bool = False  # Read-only tools that may ask for approval must not race confirmation cards.
    resource: ToolResource | None = None
    required_args: tuple[str, ...] = ()  # Opt-in local required fields; legacy tools remain optional.
    confirm: Callable | None = field(default=None, repr=False)
    prepare: Callable[[dict], Awaitable[object]] | None = field(default=None, repr=False)
    possible_operations: tuple[str, ...] = ()
    requires_confirmation: bool = False
    always_confirm: bool = False
    internal_scope: InternalToolScope | None = field(default=None, repr=False)
    task_scope: ApprovedTaskScope | None = field(default=None, repr=False)
    execution_root: str | None = field(default=None, repr=False)
    authority_owner: object | None = field(default=None, repr=False)
    authority_name: str | None = field(default=None, repr=False)
    classify: Callable[[dict], str] | None = field(default=None, repr=False)
    denied_result: object = "主人没有确认，操作已取消。"
    protective: bool = False
    _implementation: Callable[[dict], Awaitable[object]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        from src.agents.trust import INTERNAL, KINDS
        if self.operation_kind not in KINDS or any(kind not in KINDS for kind in self.possible_operations):
            raise ValueError("工具必须显式声明已知操作类别")
        if self.operation_kind == INTERNAL and (self.internal_scope is None or self.name not in self.internal_scope.tools):
            raise ValueError("内部协作工具必须绑定宿主声明的作用域")
        self._implementation = self.handler

        async def invoke(args: dict) -> object:
            from src.agents.tool_runtime import invoke_tool_handler
            return await invoke_tool_handler(self, args)

        self.handler = invoke

    @classmethod
    def prepared(cls, name: str, description: str, args: dict[str, str],
                 prepare: Callable[[dict], Awaitable[object]], *, operation_kind: str, **kwargs) -> Tool:
        return cls(name, description, args, _prepared_only, operation_kind=operation_kind,
                   prepare=prepare, **kwargs)


def tool_definitions(tools: Iterable[Tool]) -> list[dict]:
    """Export declarations only; handlers and local policy never cross the wire.

    Full provider schemas retain their types/required fields. Legacy args keep
    their optional-string contract unless explicitly overridden by the host.
    Each export is an independent copy.
    """
    return [{"name": tool.name, "description": tool.description, "input_schema":
             deepcopy(tool.input_schema) if tool.input_schema is not None else {
                 "type": "object", "properties": {
                     name: {"type": "string", "description": description,
                            **deepcopy(tool.argument_schema.get(name, {}))}
                     for name, description in tool.args.items()}, "required": list(tool.required_args)}}
            for tool in tools]


def function_schemas(tools: Iterable[Tool]) -> list[dict]:
    """OpenAI-compatible function-calling adapter over the shared declarations."""
    return [{"type": "function", "function": {
        "name": item["name"], "description": item["description"],
        "parameters": item["input_schema"]}} for item in tool_definitions(tools)]
