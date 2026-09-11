"""路径围栏——**全仓唯一一份**。一切文件操作都必须先过它，把路径钉死在工作目录内。

## 为什么收到这里

2026-09-11 的依赖图体检发现：这个函数**全仓写了三份**，而且**三份不等价**：

    src/web/auth.resolve_within         空串 → 返回 base 本身；**不捕 OSError**
    src/agents/tools/files._resolve_within   空串 → None；额外 lstrip("@")；捕 OSError
    src/memory/rewind._resolve_within        空串 → 返回 base；捕 OSError

而 rewind 那份的注释写着"契约与 src/web/auth.resolve_within 相同"——**那句话是假的**。

CLAUDE.md 的原话是"安全规则**上收到内核一处**，别在每个端各写一遍（那正是「加一端漏一端」
的历史病根）"。路径围栏正是这样一条规则，却写了三份、还漂移了。

## 取最严的那套语义

- **空串 → None**。`resolve_within(base, "")` 原先有两份返回 base 目录本身，那是个真空子：
  调用方写的是 `if resolve_within(...) is None: 拒绝`，空串于是**过了闸**，然后拿着一个
  目录路径往下走。
- **OSError → None**。`Path.resolve()` 在软链环（ELOOP）、超长路径上会抛 OSError。
  web 那份不捕，异常直接穿出围栏——一个安全判定函数**抛异常而不是拒绝**，是 fail-open 的形状。
- `@` 前缀的剥离是 **agents 层的书写约定**（`@src/x.py`），不属于围栏本身，留在调用方做。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def resolve_within(base: Any, rel: Any) -> Optional[Path]:
    """把 `rel` 解析进 `base` 并做边界校验；越界一律返回 None（**不抛异常**）。

    拦得住：绝对路径（`base / "/etc/passwd"` 语义上丢掉 base → 落到根 → relative_to 抛）、
    `..` 上跳、软链逃逸（resolve() 之后再判）、空串、以及解析过程本身出错。

    用 `relative_to` 做**组件级**判断，而不是字符串 startswith——后者会把 `/x/proj-secrets`
    误判成在 `/x/proj` 内。
    """
    text = str(rel or "").strip()
    if not text:
        return None
    try:
        root = Path(base).resolve()
        target = (root / text).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    return target
