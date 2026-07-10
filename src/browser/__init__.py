"""浏览器自动化模块"""

from .playwright_browser import (
    BrowserAutomation,
    BrowserManager,
    BrowserConfig,
    BrowserType,
    PageInfo,
    ElementInfo
)
from .verify import loopback_url_error, run_browser_probe, safe_evidence_path

__all__ = [
    "BrowserAutomation",
    "BrowserManager",
    "BrowserConfig",
    "BrowserType",
    "PageInfo",
    "ElementInfo",
    "loopback_url_error",
    "run_browser_probe",
    "safe_evidence_path",
]
