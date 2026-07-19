"""Read-only, redacted project extension inventory for Desktop."""
from __future__ import annotations

import os

from src.web.deps import APIRouter

router = APIRouter()


@router.get("/api/extensions/inspect")
async def inspect_project_extensions():
    from src.gateway.extensions_inspect import inspect_extensions

    return inspect_extensions(os.getcwd())
