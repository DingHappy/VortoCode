"""docs/ENVIRONMENT.md 与代码里读取的 VORTOCODE_* 变量两头对齐。

这不是在用源码子串「证明」某个防护存在（那类测试本仓不写）；它只管一份清单的完整性：
新加变量忘了写文档、或删了变量文档还留着，都会在这里报出来。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = re.compile(r"VORTOCODE_[A-Z0-9_]+")


def _names(text: str) -> set[str]:
    return set(NAME.findall(text))


def _source_names() -> set[str]:
    found: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        found |= _names(path.read_text(encoding="utf-8"))
    return found


def test_every_variable_the_code_reads_is_documented():
    documented = (ROOT / "docs" / "ENVIRONMENT.md").read_text(encoding="utf-8")
    missing = sorted(name for name in _source_names() if name not in documented)
    assert not missing, f"docs/ENVIRONMENT.md 缺少这些变量：{missing}"


def test_the_reference_lists_no_variable_the_code_no_longer_reads():
    source = _source_names()
    stale = sorted(name for name in _names((ROOT / "docs" / "ENVIRONMENT.md").read_text(encoding="utf-8"))
                   if name not in source)
    assert not stale, f"docs/ENVIRONMENT.md 里这些变量代码已不再读取：{stale}"
