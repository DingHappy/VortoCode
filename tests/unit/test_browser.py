"""浏览器自动化离线测试。

真实浏览器操作需 Playwright + 浏览器二进制，无法离线测；这里覆盖不需要浏览器的部分：
配置默认值、管理器查找、以及「未启动时各操作 fail-fast 抛 RuntimeError」的护栏。
"""

import pytest

from src.browser import BrowserManager, BrowserConfig, BrowserAutomation, BrowserType


def test_config_defaults():
    c = BrowserConfig()
    assert c.browser_type == BrowserType.CHROMIUM
    assert c.headless is True
    assert c.viewport_width == 1280
    assert c.viewport_height == 720


def test_manager_get_unknown_returns_none():
    assert BrowserManager().get_browser("nope") is None


@pytest.mark.asyncio
async def test_operations_raise_when_not_started():
    # 构造不触发 Playwright；未 start 时各操作必须 fail-fast
    b = BrowserAutomation()
    for call in (
        b.navigate("https://example.com"),
        b.click("#x"),
        b.fill("#x", "v"),
        b.screenshot(),
        b.get_text("#x"),
    ):
        with pytest.raises(RuntimeError):
            await call
