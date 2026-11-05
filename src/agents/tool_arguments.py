"""Bounded checks for the explicit, local Tool.argument_schema overrides.

This is not a JSON Schema implementation. Full provider input_schema remains a
provider/handler contract. Description-only arguments retain their types and
optionality unless the host opts in; no coercion, defaults or authority is added.
"""
from __future__ import annotations

import math
from typing import Any


class ToolArgumentError(ValueError):
    """Diagnostics contain trusted field paths and fixed text, never values."""


_TYPES = {"string", "integer", "number", "boolean", "array", "object", "null"}
_ANNOTATIONS = {"description", "title", "default", "examples"}
_LIMITS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
           "minLength", "maxLength", "minItems", "maxItems"}
_KEYS = _ANNOTATIONS | _LIMITS | {"type", "enum", "items", "properties"}


class _Budget:
    def __init__(self):
        self.left = 10000

    def visit(self, depth: int) -> None:
        self.left -= 1
        if depth > 32 or self.left < 0:
            raise ToolArgumentError("参数或本机参数合同过于复杂，请缩小本次调用。")


def _json_value(value: Any, budget: _Budget, depth: int = 0) -> None:
    budget.visit(depth)
    if type(value) in (str, int, bool) or value is None:
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item, budget, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item, budget, depth + 1)
        return
    raise ToolArgumentError("参数必须是 JSON 数据，不能含非有限数值或非字符串字段名。")


def _contract(schema: Any, budget: _Budget, depth: int = 0) -> None:
    budget.visit(depth)
    invalid = "本机参数合同无效或含不支持的约束，已拒绝执行；请由工具提供方修正。"
    if type(schema) is not dict or set(schema) - _KEYS:
        raise ToolArgumentError(invalid)
    if "type" in schema:
        types = schema["type"] if type(schema["type"]) is list else [schema["type"]]
        if not types or any(type(t) is not str or t not in _TYPES for t in types):
            raise ToolArgumentError(invalid)
    if "enum" in schema and (type(schema["enum"]) is not list or not schema["enum"]):
        raise ToolArgumentError(invalid)
    for key in _LIMITS & schema.keys():
        value = schema[key]
        if key.endswith(("Length", "Items")):
            valid = type(value) is int and value >= 0
        else:
            valid = type(value) is int or type(value) is float and math.isfinite(value)
        if not valid:
            raise ToolArgumentError(invalid)
    if "items" in schema:
        _contract(schema["items"], budget, depth + 1)
    if "properties" in schema:
        if type(schema["properties"]) is not dict:
            raise ToolArgumentError(invalid)
        for child in schema["properties"].values():
            _contract(child, budget, depth + 1)


def _type_matches(value: Any, name: str) -> bool:
    return {
        "string": type(value) is str,
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "boolean": type(value) is bool,
        "array": type(value) is list,
        "object": type(value) is dict,
        "null": value is None,
    }[name]


def _equal(left: Any, right: Any, budget: _Budget, depth: int = 0) -> bool:
    # Python's True == 1 must not allow a boolean to match a numeric enum,
    # including inside an array/object enum member.
    budget.visit(depth)
    if type(left) is not type(right):
        return type(left) in (int, float) and type(right) in (int, float) and left == right
    if type(left) is list:
        return len(left) == len(right) and all(_equal(a, b, budget, depth + 1) for a, b in zip(left, right))
    if type(left) is dict:
        return left.keys() == right.keys() and all(_equal(left[k], right[k], budget, depth + 1) for k in left)
    return left == right


def _validate(value: Any, schema: dict, path: str, budget: _Budget, depth: int = 0) -> None:
    budget.visit(depth)

    def reject(detail: str) -> None:
        raise ToolArgumentError(f"{path}：{detail}；未执行，请按参数合同修正。")

    if "type" in schema:
        types = schema["type"] if type(schema["type"]) is list else [schema["type"]]
        if not any(_type_matches(value, name) for name in types):
            reject("类型应为 " + "/".join(types))
    if "enum" in schema and not any(_equal(value, option, budget) for option in schema["enum"]):
        reject("不在声明的枚举范围内")
    if type(value) in (int, float):
        for key, fails in (("minimum", lambda bound: value < bound),
                           ("maximum", lambda bound: value > bound),
                           ("exclusiveMinimum", lambda bound: value <= bound),
                           ("exclusiveMaximum", lambda bound: value >= bound)):
            if key in schema and fails(schema[key]):
                reject("超出声明的数值范围")
    if type(value) in (str, list):
        suffix = "Length" if type(value) is str else "Items"
        if "min" + suffix in schema and len(value) < schema["min" + suffix]:
            reject("少于声明的最小长度或项数")
        if "max" + suffix in schema and len(value) > schema["max" + suffix]:
            reject("超过声明的最大长度或项数")
    if type(value) is list and "items" in schema:
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{index}]", budget, depth + 1)
    if type(value) is dict:
        for name, child in schema.get("properties", {}).items():
            if name in value:
                _validate(value[name], child, f"{path}.{name}", budget, depth + 1)


def validate_arguments(args: Any, overrides: dict[str, dict], required: tuple[str, ...] = ()) -> None:
    """Check JSON data plus only explicitly declared local field constraints.

    Missing fields are allowed unless explicitly required. Extra fields retain
    the legacy open contract.
    Full schema users pass no overrides; their handler owns schema enforcement.
    Invalid/unsupported local contracts fail closed, including on absent fields.
    """
    if type(args) is not dict:
        raise ToolArgumentError("参数必须是 JSON 对象；未执行，不能替换为空参数。")
    if type(overrides) is not dict or type(required) is not tuple or any(type(k) is not str for k in required):
        raise ToolArgumentError("本机参数合同无效，已拒绝执行；请由工具提供方修正。")
    _json_value(args, _Budget())
    _json_value(overrides, _Budget())
    for name in required:
        if name not in args:
            raise ToolArgumentError(f"$.{name}：缺少必填参数；未执行，请按参数合同补充。")
    schema_budget, value_budget = _Budget(), _Budget()
    for name, schema in overrides.items():
        _contract(schema, schema_budget)
        if name in args:
            _validate(args[name], schema, f"$.{name}", value_budget)
