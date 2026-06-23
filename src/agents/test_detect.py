"""按仓库标志文件**自动探测测试命令**，让隔离 dev 流水线不再写死 pytest。

差异化的隔离实现+验证流水线（run_isolated_task / dev_isolated / dev_parallel / dev_auto）此前
一律 `pytest -q`，于是只对 Python 仓库成立——JS/Go/Rust 项目跑不出绿、护城河失效。这里据
标志文件判型，返回对应测试命令；探测不到则退回 pytest（VortoCode 自身就是 Python，保持原行为）。

selector：仅对 pytest 有意义（文件级 narrow）；其它语言多不支持文件级选择，给了也整体跑。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional


def _pytest_cmd(selector: Optional[str]) -> List[str]:
    return [sys.executable, "-m", "pytest", "-q", selector or "tests/"]


def is_pytest_cmd(cmd) -> bool:
    """这条命令是不是 pytest（决定 selector 该不该作为文件级 narrow 追加）。"""
    return any("pytest" in str(c) for c in (cmd or []))


def detect_test_cmd(repo_root, selector: Optional[str] = None) -> List[str]:
    """据 repo_root 的标志文件返回测试命令（list[str]，直接喂 subprocess）。

    顺序：Node(确有 test 脚本) → Rust → Go → Python(pyproject/setup/pytest 配置/tests 目录/conftest)
    → Makefile 有 test 目标 → 兜底 pytest。selector 只在 pytest 分支生效。
    """
    root = Path(repo_root)

    # Node：必须 package.json 里**确有** test 脚本，否则 `npm test` 会 "missing script: test"
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = data.get("scripts") if isinstance(data, dict) else None
            if isinstance(scripts, dict) and "test" in scripts:
                if (root / "pnpm-lock.yaml").exists():
                    return ["pnpm", "test"]
                if (root / "yarn.lock").exists():
                    return ["yarn", "test"]
                return ["npm", "test", "--silent"]
        except Exception:  # noqa: BLE001 —— package.json 坏了就当没有，继续往下探
            pass

    if (root / "Cargo.toml").is_file():                     # Rust
        return ["cargo", "test"]
    if (root / "go.mod").is_file():                         # Go
        return ["go", "test", "./..."]

    # Python：任一强信号
    py_markers = ("pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "tox.ini", "conftest.py")
    if any((root / m).exists() for m in py_markers) or (root / "tests").is_dir():
        return _pytest_cmd(selector)

    # Makefile 里有 `test:` 目标
    mk = root / "Makefile"
    if mk.is_file():
        try:
            if any(ln.lstrip().startswith("test:") for ln in mk.read_text(encoding="utf-8").splitlines()):
                return ["make", "test"]
        except Exception:  # noqa: BLE001
            pass

    return _pytest_cmd(selector)                            # 兜底：pytest（VortoCode 自身就是 Python）
