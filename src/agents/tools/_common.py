"""工具工厂共用的小工具——上限旋钮与宽松真值判定。

放这里而不是留在 main_agent：各工厂要用它，main_agent 也要用它。搁在 main_agent 里
会让 tools.* 反过来 import main_agent，而 main_agent 又要 import tools.* —— 直接成环。
依赖方向只有一条：main_agent → tools.* → tools._common。
"""

from __future__ import annotations

from typing import Any


# 单个工具结果回灌给模型的最大字符数（默认值），与 read_file 整文件截断上限。
#
# ⚠️ 这两个值**没有跟着 microcompaction 一起放宽**，是想清楚后的决定：折叠只发生在回合开始、
# 且只折"老段"，而工具结果是在**回合内**产生、落在当前回合——那恰恰是折叠和裁剪都够不着的
# 区域（当前回合的消息受保护、最新一条永远保留）。也就是说"先给足"的字节在最要命的地方
# **无法被回收**：并行 3 个 read_file 就能让一条消息吃掉几倍于整个历史预算的空间。
# 想放宽的人可以自己经 env 开（他清楚自己的窗口有多大），但默认值必须是能兜住的那个。
_MAX_TOOL_RESULT_DEFAULT = 4_000


_MAX_READ_FILE_DEFAULT = 6_000


def _env_limit(name: str, default: int) -> int:
    """读一个正整数上限。**每次调用时读**，不是在 import 时读——.env 由入口（cli/tui/web）在
    import 之后才加载，模块级常量在那之前读只会读到空值，于是 .env 里配的旋钮**静默失效**
    （自审逮到的真 bug）。坏值/非正一律回退默认（绝不 clamp 成 1，那会把结果截成一个字符）。"""
    import os as _os
    try:
        v = int(_os.getenv(name) or default)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def _max_tool_result() -> int:
    return _env_limit("VORTOCODE_MAX_TOOL_RESULT", _MAX_TOOL_RESULT_DEFAULT)


def _max_read_file() -> int:
    return _env_limit("VORTOCODE_MAX_READ_FILE", _MAX_READ_FILE_DEFAULT)


_MAX_IMAGE_BYTES_DEFAULT = 5 * 1024 * 1024      # read_file 读图上限：太大的图 base64 后会撑爆请求体


def _max_image_bytes() -> int:
    return _env_limit("VORTOCODE_MAX_IMAGE_BYTES", _MAX_IMAGE_BYTES_DEFAULT)


def _image_exts() -> frozenset:
    from src.llm.content import IMAGE_EXTS
    return IMAGE_EXTS


def _truthy(v: Any) -> bool:
    """宽松真值：兼容原生 function-calling 的 bool 与提示式协议的字符串（"true"/"1"/"yes"…）。"""
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y", "t", "all")
