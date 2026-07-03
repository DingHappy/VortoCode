"""env 变量前缀兼容：`AUTODEV_*` → `VORTOCODE_*`（2026-07 统一，A3）。

历史遗留的 `AUTODEV_*` 环境变量统一改名到 `VORTOCODE_*`（与包名/CLI 一致）。为不破坏既有部署，
设**兼容期**：先读新前缀，缺省回落旧前缀并打一次 `DeprecationWarning`。兼容期后可删回落分支。
"""
import os
import warnings
from typing import Optional

_warned: set = set()


def env_compat(new_key: str, old_key: str, default: Optional[str] = None) -> Optional[str]:
    """读环境变量：优先 new_key；缺省回落 old_key（首次回落打一次 DeprecationWarning）；都无则 default。"""
    v = os.getenv(new_key)
    if v is not None:
        return v
    old = os.getenv(old_key)
    if old is not None:
        if old_key not in _warned:
            _warned.add(old_key)
            warnings.warn(f"环境变量 {old_key} 已弃用，请改用 {new_key}（兼容回落生效中，将来会移除）",
                          DeprecationWarning, stacklevel=2)
        return old
    return default
