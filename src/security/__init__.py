"""安全模块——只剩云沙箱执行路由的命令闸（其余"权限系统"是生产零调用的死代码，已删）。

真正管事的权限判定在别处，见 permissions.py 的模块文档。
"""

from .permissions import (
    RiskLevel,
    PermissionManager,
    SafetyGuard
)
__all__ = [
    "RiskLevel",
    "PermissionManager",
    "SafetyGuard",
]
