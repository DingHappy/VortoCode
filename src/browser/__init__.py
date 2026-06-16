"""浏览器自动化模块"""

from .playwright_browser import (
    BrowserAutomation,
    BrowserManager,
    BrowserConfig,
    BrowserType,
    PageInfo,
    ElementInfo
)

__all__ = [
    "BrowserAutomation",
    "BrowserManager",
    "BrowserConfig",
    "BrowserType",
    "PageInfo",
    "ElementInfo",
]
