"""Trusted, host-injected browser drivers; no discovery or code loading.

Drivers handle transport only. The website tool adapter retains grants, human
handoff, privacy projection, confirmations, ownership and runtime cleanup.
Native device drivers will have their own domain protocol, without JavaScript.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable, Protocol

BROWSER_BACKEND_API = 1
DEFAULT_BACKEND_ID = "chromium_cdp"


def validate_backend_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
        raise ValueError("Invalid browser backend identity")
    return value


class BrowserDriver(Protocol):
    @property
    def running(self) -> bool: ...

    async def start(self) -> None: ...
    async def close(self) -> None: ...  # Return only after owned processes exit.
    async def evaluate(self, expression: str) -> Any: ...
    async def navigate(self, url: str) -> None: ...
    async def wait_ready(self) -> None: ...
    async def click_at(self, x: float, y: float) -> None: ...
    async def insert_text(self, text: str) -> None: ...
    async def press_enter(self) -> None: ...
    async def screenshot(self) -> bytes: ...
    async def scroll(self, amount: int) -> None: ...


@dataclass(frozen=True)
class BrowserLaunch:
    profile_dir: Path
    device_mode: str


@dataclass(frozen=True)
class BrowserBackend:
    """A reviewed factory supplied by trusted assembly, never a model manifest.

    api_version describes this Python driver interface, not browser/CDP wire
    compatibility or permission. Identity must remain stable across compatible
    upgrades; changing it starts a separate profile and requires human login.
    """
    backend_id: str
    api_version: int
    device_modes: frozenset[str]
    create: Callable[[BrowserLaunch], BrowserDriver]

    def __post_init__(self):
        validate_backend_id(self.backend_id)
        if type(self.api_version) is not int or self.api_version != BROWSER_BACKEND_API:
            raise ValueError("Unsupported browser backend API")
        modes = frozenset(self.device_modes)
        if not modes or not modes <= {"desktop", "mobile"} or not callable(self.create):
            raise ValueError("Invalid browser backend declaration")
        object.__setattr__(self, "device_modes", modes)

    def supports(self, mode: str) -> bool:
        return mode in self.device_modes


def chromium_backend() -> BrowserBackend:
    """Construct a declaration only; browser detection/launch stays lazy."""
    def create(launch: BrowserLaunch) -> BrowserDriver:
        from src.browser.cdp import CdpBrowser, find_browser
        executable = find_browser()
        if not executable:
            raise ValueError("未找到本机 Chromium 浏览器")
        return CdpBrowser(executable, launch.profile_dir, device_mode=launch.device_mode)
    return BrowserBackend(DEFAULT_BACKEND_ID, BROWSER_BACKEND_API,
                          frozenset({"desktop", "mobile"}), create)
