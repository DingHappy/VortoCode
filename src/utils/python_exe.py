"""解析"跑测试用的 Python 解释器"——打包版里 `sys.executable` 不是解释器。

Desktop 的 runtime 是 PyInstaller 单文件（`vortocode-runtime`）：此时 `sys.executable`
指向 runtime 自己，`[sys.executable, "-m", "pytest"]` 会被 runtime 的 argparse 接住，
真机上报的是：

    vortocode-runtime: error: argument command: invalid choice: 'pytest' (choose from server)

于是**隔离 dev 流水线在 Desktop 上永远跑不了 Python 测试**：子 agent 明明写出了改动
（diff 几十行），却一律判红丢弃，用户只看到"试了 2 次仍未过"。源码运行（CLI/TUI/Web）
不受影响，所以这条只在打包产物里发作，测试里也照不到。

顺序：`VORTOCODE_PYTHON` 显式指定 → 未打包就用 `sys.executable` → 打包则在 PATH 上找
`python3`/`python`。都找不到时返回 "python3"：让失败停在"找不到解释器"这种看得懂的错上，
而不是上面那条把人带偏的 argparse 报错。
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import List


def python_executable() -> str:
    """跑 Python 子进程该用的解释器路径。"""
    override = (os.getenv("VORTOCODE_PYTHON") or "").strip()
    if override:
        return override
    if not getattr(sys, "frozen", False):
        return sys.executable
    for candidate in ("python3", "python"):
        found = shutil.which(candidate)
        if found:
            return found
    return "python3"


def pytest_argv(*args: str) -> List[str]:
    """`python -m pytest` 命令行（附加参数原样追加）。"""
    return [python_executable(), "-m", "pytest", *args]
