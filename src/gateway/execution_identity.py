"""Non-secret, lifetime-bound execution identity; not an authorization grant.

Persistent instance IDs belong to the existing device/owner store. This module
only publishes a fresh generation while that workspace's Runtime owns its lease.
"""
from pathlib import Path
import re
import uuid

_ACTIVE = {}


def validate_identity(value):
    if (not isinstance(value, dict) or set(value) != {"version", "instance_id", "generation"}
            or type(value["version"]) is not int or value["version"] != 1
            or any(not isinstance(value[key], str) or not re.fullmatch(r"[a-f0-9]{32}", value[key])
                   for key in ("instance_id", "generation"))):
        raise ValueError("运行端执行身份缺失或不兼容")
    return dict(value)


def begin_execution_identity(root, instance_id):
    identity = validate_identity({"version": 1, "instance_id": instance_id, "generation": uuid.uuid4().hex})
    key = str(Path(root).resolve())
    if key in _ACTIVE:
        raise RuntimeError("运行端已有执行身份")
    _ACTIVE[key] = identity
    return dict(identity)


def current_execution_identity(root):
    value = _ACTIVE.get(str(Path(root).resolve()))
    return dict(value) if value else None


def end_execution_identity(root, expected):
    key = str(Path(root).resolve())
    if _ACTIVE.get(key) == expected:
        _ACTIVE.pop(key, None)
