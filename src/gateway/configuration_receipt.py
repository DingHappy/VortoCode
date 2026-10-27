"""Non-secret receipt for the model configuration supplied at process startup.

This confirms the launch configuration, not provider availability or successful
inference. The receipt is frozen at startup and withdrawn during shutdown.
"""
import os
import re

_receipt: dict | None = None


def begin_configuration_receipt() -> None:
    global _receipt
    revision = os.getenv("VORTOCODE_MODEL_CONFIGURATION_REVISION", "")
    launch_id = os.getenv("VORTOCODE_CONFIGURATION_LAUNCH_ID", "")
    valid = lambda value: bool(re.fullmatch(r"[a-zA-Z0-9-]{1,96}", value))
    _receipt = ({"schema_version": 1, "revision": revision, "launch_id": launch_id}
                if valid(revision) and valid(launch_id) else None)


def end_configuration_receipt() -> None:
    global _receipt
    _receipt = None


def configuration_receipt() -> dict | None:
    return dict(_receipt) if _receipt is not None else None
