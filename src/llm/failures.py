"""Conservative classification of model-service failures; no routing or retries."""
from __future__ import annotations

import re


def is_model_capacity_error(error: object) -> bool:
    """Recognize the observed model-load ceiling rejection, not HTTP 507 alone.

    A 507 can also mean storage exhaustion. Unknown errors keep the existing
    bounded retry behavior; this does not estimate local RAM or choose a model.
    """
    text = str(error).lower()
    return "model" in text and re.search(
        r"does not fit under (?:the )?dynamic memory ceiling", text) is not None
