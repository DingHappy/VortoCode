"""剥掉 Rich 控制台标记（`[b]`/`[dim]`/`[/]`），给不渲染 Rich 的出口用。

主 agent 的工具提示行是按终端写的，带 Rich 标记（`🔧 [b]name[/b][dim] …[/dim]`）。Web / IM
这类出口必须剥成纯文本，否则用户看到的是标签原文。

**为什么不直接用 rich**：`rich` 只在 `[tui]` extra 里，而 Web-only 部署（`pip install vortocode`
不带 extra，起 `vc server`）根本没装它。原来的写法是 `try: import rich … except: pass`——
import 失败被静默吞掉，于是那些机器上 Web 控制台一直原样显示 `[b]list_files[/b]`，而且没有任何
迹象说明剥离失败过。这里改成不依赖 rich 的实现，装没装 rich 行为都一样。
"""
from __future__ import annotations

import re

# 判定照抄 rich.markup 的口径：标签内容以 [a-z]、`#`、`/`、`@` 开头（`[/]` 是"关闭全部"）。
# 因此正文里的 `[1]`、`[P0]`、`[TODO]` 不会被误伤——大写和数字开头的方括号一律留着。
_TAG = re.compile(r"\[(/?)([a-z#@][^\[\]]*|/?)\]")


def strip_rich_markup(text) -> str:
    """把 Rich 标记剥成纯文本；非字符串按 str() 处理。"""
    out = _TAG.sub("", str(text))
    return out.replace("\\[", "[")          # rich 里 `\[` 是转义过的字面方括号
