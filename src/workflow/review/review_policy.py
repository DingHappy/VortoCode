"""Repository-owned, strictly additive delivery review policy.

The policy can only add a review gate; absence keeps the personal/default
workflow unchanged.  Invalid configured policy fails closed at delivery time
so a typo cannot silently disable a team's intended requirement.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode

_MAX_CONFIG_BYTES = 64 * 1024


def _mapping_value(node: Node | None, key: str) -> Node | None:
    if not isinstance(node, MappingNode):
        return None
    matches = [value for name, value in node.value if isinstance(name, ScalarNode) and name.value == key]
    if len(matches) > 1:
        raise ValueError(f"{key} 不能重复配置")
    return matches[0] if matches else None


def load_task_review_policy(repo_root: str) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    path = root / ".vortocode" / "review-policy.yaml"
    default = {
        "configured": False,
        "active": False,
        "config_path": "",
        "config_sha256": "",
        "require_all_hunks_decided": False,
        "error": "",
    }
    if not path.is_file():
        return default
    try:
        raw = path.read_bytes()
        if len(raw) > _MAX_CONFIG_BYTES:
            raise ValueError("review-policy.yaml 超过 64 KiB")
        text = raw.decode("utf-8")
        payload = yaml.safe_load(text) or {}
        if not isinstance(payload, dict):
            raise ValueError("review-policy.yaml 顶层必须是对象")
        unknown_top = sorted(str(key) for key in payload if key != "task_branch")
        if unknown_top:
            raise ValueError(f"未知策略字段：{', '.join(unknown_top[:5])}")
        branch = payload.get("task_branch") or {}
        if not isinstance(branch, dict):
            raise ValueError("task_branch 必须是对象")
        unknown_branch = sorted(str(key) for key in branch if key != "require_all_hunks_decided")
        if unknown_branch:
            raise ValueError(f"未知 task_branch 字段：{', '.join(unknown_branch[:5])}")
        document = yaml.compose(text, Loader=yaml.SafeLoader)
        branch_node = _mapping_value(document, "task_branch")
        value_node = _mapping_value(branch_node, "require_all_hunks_decided")
        if value_node is None:
            value = False
        elif (
            not isinstance(value_node, ScalarNode)
            or value_node.tag != "tag:yaml.org,2002:bool"
            or value_node.value.lower() not in {"true", "false"}
        ):
            raise ValueError("require_all_hunks_decided 必须是 true/false")
        else:
            value = value_node.value.lower() == "true"
        return {
            "configured": True,
            "active": value,
            "config_path": ".vortocode/review-policy.yaml",
            "config_sha256": hashlib.sha256(raw).hexdigest()[:16],
            "require_all_hunks_decided": value,
            "error": "",
        }
    except (OSError, UnicodeError, TypeError, ValueError, yaml.YAMLError) as exc:
        return {
            **default,
            "configured": True,
            "config_path": ".vortocode/review-policy.yaml",
            "error": str(exc)[:300],
        }
