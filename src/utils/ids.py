"""标识符的两条边界规则——**全仓各一份**。

`.vortocode/` 下大量状态按 id 落成文件（`<id>.json`），所以 id 直接进路径。
**一个没清洗的 id 就是一次路径穿越**。这条规则在仓库里长出过 8 处实现、5 种变体
（2026-09-11 依赖图体检），和 `resolve_within` 是同一类问题——而那一类已经证明会漂移
（三份里两份对空串返回 base 本身、一份不捕 OSError，注释还写着"契约相同"）。

## 但它们是**两件事**，不能合成一件

    safe_id(value)            清洗型：坏字符换 _，空则 None
                              —— 任何字符串都能变成一个合法文件名
    typed_id(value, prefix)   校验型：必须完整匹配 <prefix>-[A-Za-z0-9_-]+，否则拒绝
                              —— 白名单，不认识的一律不要

**校验型严格得多。** 把它合进清洗型，等于把"拒绝未知"降级成"清洗一切"——那是削弱安全，
不是消除重复。原来 terminals/review_threads/runs 用的就是校验型（`term-` / `review-` / `run-`），
那个差异是刻意的，要留着。

（同理，`_now()` 全仓 15 处看着更刺眼，却**不该收**：products 刻意用微秒精度——秒级会让
`latest()` 在同一秒内随机取一条——其余用秒级。那也是刻意的差异。）
"""
from __future__ import annotations

import re
from typing import Optional

# 清洗型允许的字符集。全仓七处原先各写一遍这个正则，**七处完全一致**——
# 核心判据没漂过，但五种变体的存在说明下一次漂移只是时间问题。
_BAD = re.compile(r"[^A-Za-z0-9_-]")


def safe_id(value: object) -> Optional[str]:
    """清洗成文件名安全的 id；空或全是坏字符 → None（**不是空串**）。

    返回 None 而不是空串，是为了让调用方写 `if safe_id(x) is None: 拒绝`——
    空串是 falsy 但仍然是个字符串，拼进路径会得到一个目录而不是文件。
    """
    if not value:
        return None
    cleaned = _BAD.sub("_", str(value)).strip("_")
    return cleaned or None


def typed_id(value: object, prefix: str) -> str:
    """校验一个**带类型前缀**的 id；不合法 → 空串。

    与 `safe_id` 的关键区别：这里**不做任何转换**。`run-x/../y` 不会被洗成 `run-x_.._y`，
    它会被直接拒绝——调用方据此知道"这个 id 我不认识"，而不是拿着一个被悄悄改过的 id 往下走。
    """
    text = str(value or "")
    return text if re.fullmatch(rf"{re.escape(prefix)}-[A-Za-z0-9_-]+", text) else ""
