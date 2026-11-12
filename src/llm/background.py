"""Bounded transport retries for fresh background agents, never whole-turn replay."""
from typing import Optional

from src.llm.client import LLMClient, LLMConfig


def background_client(config: Optional[LLMConfig] = None) -> LLMClient:
    """Keep endpoint/model credentials isolated and leave interactive defaults alone."""
    client = LLMClient(config.model_copy(deep=True) if config is not None else None)
    client.config.max_retries = 2
    return client
