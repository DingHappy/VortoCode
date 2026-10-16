"""Explicit evaluation clients and strict grading; never changes runtime profiles."""
from __future__ import annotations

import asyncio

from src.llm.client import LLMClient, LLMConfig
from src.llm.providers import providers


def evaluation_client(spec: str | None = None) -> LLMClient:
    if not spec:
        client = LLMClient()
        client.config = client.config.model_copy(update={'max_retries': 0, 'timeout': 90, 'request_timeout': 90})
        return client
    config = LLMConfig(max_retries=0, timeout=90, request_timeout=90)
    if spec:
        provider, sep, model = spec.partition(":")
        if not sep:
            provider, model = "default", provider
        target = providers().get(provider)
        if not model.strip() or target is None or not target.usable:
            raise ValueError("evaluation target unavailable; use registered provider:model (default: for model tags)")
        config = config.model_copy(update={"base_url": target.base_url, "api_key": target.api_key, "model": model.strip()})
    return LLMClient(config)


def judge_label(text: str) -> bool:
    """Official prompt asks yes/no only; malformed output is an error, never a pass."""
    answer = text.strip().lower()
    if answer not in {"yes", "no"}:
        raise ValueError("judge did not return an unambiguous yes/no label")
    return answer == "yes"


async def close_clients(*clients):
    await asyncio.gather(*(client.close() for client in clients))
