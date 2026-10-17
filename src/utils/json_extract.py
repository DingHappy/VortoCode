"""Extract JSON from model responses without changing fallback semantics."""

import json
import re
from typing import Optional, Union


def extract_json(text: str) -> Optional[Union[dict, list]]:
    """从 LLM 输出中健壮地提取 JSON。

    处理三种常见情况：纯 JSON、被 ```json 围栏包裹、以及 JSON 前后夹杂解释文字。
    解析失败返回 None（由调用方决定降级策略），绝不抛异常打断流程。
    """
    if not text:
        return None
    t = text.strip()

    # 去除 markdown 代码围栏
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t).strip()

    # 先尝试整体解析
    try:
        return json.loads(t)
    except Exception:
        pass

    # 回退：扫描第一个配平的 {...} 或 [...]
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = t.find(open_ch)
        if start == -1:
            continue
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except Exception:
                        break
    return None
