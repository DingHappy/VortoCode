"""Response accounting receipts; independent of session counters and billing claims."""
from __future__ import annotations

import contextvars
import json
import math
import os
from contextlib import contextmanager

_meters: contextvars.ContextVar = contextvars.ContextVar("model_observations", default=())


@contextmanager
def observation_scope():
    # A separate bucket per concurrent worker; nested scopes also reach parents.
    bucket: dict = {}
    token = _meters.set((*_meters.get(), bucket))
    try:
        yield bucket
    finally:
        _meters.reset(token)


def record_response(config, model: str, prompt: int, completion: int, exact: bool) -> None:
    if not _meters.get():
        return
    from types import SimpleNamespace
    from src.llm.providers import request_target_view
    provider = request_target_view(SimpleNamespace(config=config))["provider"] if config else "unattributed"
    for meter in _meters.get():
        key = (provider, model)
        item = meter.setdefault(key, {"provider": provider, "model": model, "calls": 0,
                                     "prompt_tokens": 0, "completion_tokens": 0, "estimated_calls": 0})
        item["calls"] += 1
        item["prompt_tokens"] += prompt
        item["completion_tokens"] += completion
        item["estimated_calls"] += int(not exact)


def _tariff(provider: str, model: str):
    """Only explicit operator prices, keyed by provider:model; no guessed catalog price."""
    try:
        prices = json.loads(os.getenv("VORTOCODE_MODEL_PRICES", "{}"))
        price = prices.get(f"{provider}:{model}")
        if (not isinstance(price, dict) or set(price) != {"input_per_million", "output_per_million", "currency"}
                or price["currency"] not in {"USD", "CNY", "EUR"}
                or any(type(price[key]) not in (float, int) or not math.isfinite(price[key]) or price[key] < 0
                       for key in ("input_per_million", "output_per_million"))):
            return None
        return price
    except (ValueError, AttributeError, TypeError):
        return None


def summarize(meter: dict, elapsed_ms: int, category: str, *, complete: bool) -> dict:
    from src.gateway.audit import _clip_text
    components = []
    for item in meter.values():
        price = _tariff(item["provider"], item["model"])
        estimate = ((item["prompt_tokens"] * price["input_per_million"]
                     + item["completion_tokens"] * price["output_per_million"]) / 1_000_000) if price else None
        components.append({**item, "provider": _clip_text(item["provider"], 160),
                           "model": _clip_text(item["model"], 160), "tariff": price,
                           "cost_estimate": estimate if estimate is None or math.isfinite(estimate) else None})
    currencies = {item["tariff"]["currency"] for item in components if item["tariff"]}
    priced = bool(components) and all(item["cost_estimate"] is not None for item in components) and len(currencies) == 1
    return {"category": category, "elapsed_ms": elapsed_ms,
            "scope": "delegated_attempt_including_nested_accounted_responses",
            "usage_evidence": "accounted_responses" if complete and components and len(components) <= 32 else "partial_or_unknown",
            "components": components[:32], "components_truncated": len(components) > 32,
            "tokens": sum(item["prompt_tokens"] + item["completion_tokens"] for item in components) if components else None,
            "cost_estimate": sum(item["cost_estimate"] for item in components) if priced and complete else None,
            "currency": next(iter(currencies)) if priced else None,
            "billing_verified": False}
