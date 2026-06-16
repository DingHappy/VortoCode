"""LLM 客户端"""

import logging as _logging

from .client import LLMClient, LLMConfig, get_llm_client, MODELS

_logger = _logging.getLogger(__name__)


def resolve_optional_client(llm_client=None):
    """返回可用的 LLM 客户端或 None。

    显式传入则直接用；否则仅当配置了 OPENAI_API_KEY 才自动构建——无 key 返回 None，
    让调用方回退到确定性实现（离线/CI 默认不打网络）。供生成器等「LLM 优先、否则降级」处复用。
    """
    if llm_client is not None:
        return llm_client
    try:
        client = get_llm_client("balanced")
    except Exception as e:  # 没装 LLM 依赖等
        _logger.warning("LLM client 不可用: %s", e)
        return None
    return client if client.config.api_key else None


def strip_code_fence(text):
    """去掉 LLM 输出里可能包裹的 ```lang ... ``` 围栏，返回纯代码/文本。"""
    t = (text or "").strip()
    if not t.startswith("```"):
        return text
    lines = t.split("\n")[1:]  # 去掉开头的 ```lang
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


__all__ = [
    "LLMClient",
    "LLMConfig",
    "get_llm_client",
    "MODELS",
    "resolve_optional_client",
    "strip_code_fence",
]
