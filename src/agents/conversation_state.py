"""Versioned conversation data only; never carries tools, permissions or execution state."""
from copy import deepcopy


def compression_state(value=None):
    if value is None:
        return {'version': 1, 'summary': '', 'task_anchor': '', 'task_anchor_external': False}
    if (not isinstance(value, dict)
            or set(value) != {'version', 'summary', 'task_anchor', 'task_anchor_external'}
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['summary'], str) or not isinstance(value['task_anchor'], str)
            or type(value['task_anchor_external']) is not bool):
        raise ValueError('会话压缩状态格式无效；未丢弃信任标记或恢复执行')
    return deepcopy(value)


def agent_compression(agent):
    export = getattr(agent, 'export_compression_state', None)
    return compression_state(export()) if callable(export) else None


def restore_agent_compression(agent, value):
    restore = getattr(agent, 'restore_compression_state', None)
    if callable(restore):
        restore(compression_state(value))
