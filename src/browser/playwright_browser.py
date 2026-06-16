"""浏览器自动化 - Playwright 集成"""

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class BrowserType(str, Enum):
    """浏览器类型"""
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


@dataclass
class BrowserConfig:
    """浏览器配置"""
    browser_type: BrowserType = BrowserType.CHROMIUM
    headless: bool = True
    viewport_width: int = 1280
    viewport_height: int = 720
    timeout: int = 30000  # 毫秒
    user_agent: Optional[str] = None
    proxy: Optional[str] = None


@dataclass
class PageInfo:
    """页面信息"""
    url: str
    title: str
    content: str
    screenshot: Optional[bytes] = None


@dataclass
class ElementInfo:
    """元素信息"""
    selector: str
    text: str
    tag_name: str
    attributes: Dict[str, str]
    bounding_box: Optional[Dict[str, float]] = None


class BrowserAutomation:
    """浏览器自动化"""
    
    def __init__(self, config: BrowserConfig = None):
        self.config = config or BrowserConfig()
        self.browser = None
        self.context = None
        self.page = None
        self._playwright = None
    
    async def start(self):
        """启动浏览器"""
        try:
            from playwright.async_api import async_playwright
            
            self._playwright = await async_playwright().start()
            
            # 选择浏览器类型
            if self.config.browser_type == BrowserType.CHROMIUM:
                launcher = self._playwright.chromium
            elif self.config.browser_type == BrowserType.FIREFOX:
                launcher = self._playwright.firefox
            else:
                launcher = self._playwright.webkit
            
            # 启动浏览器
            self.browser = await launcher.launch(
                headless=self.config.headless
            )
            
            # 创建上下文
            context_options = {
                "viewport": {
                    "width": self.config.viewport_width,
                    "height": self.config.viewport_height
                }
            }
            
            if self.config.user_agent:
                context_options["user_agent"] = self.config.user_agent
            
            if self.config.proxy:
                context_options["proxy"] = {"server": self.config.proxy}
            
            self.context = await self.browser.new_context(**context_options)
            
            # 创建页面
            self.page = await self.context.new_page()
            
            # 设置超时
            self.page.set_default_timeout(self.config.timeout)
            
            logger.info("Browser started")
        
        except ImportError:
            logger.error("playwright not installed. Run: pip install playwright && playwright install")
            raise
        except Exception as e:
            logger.error(f"Failed to start browser: {e}")
            raise
    
    async def stop(self):
        """停止浏览器"""
        try:
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self._playwright:
                await self._playwright.stop()
            
            logger.info("Browser stopped")
        
        except Exception as e:
            logger.error(f"Failed to stop browser: {e}")
    
    async def navigate(self, url: str) -> PageInfo:
        """导航到 URL"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        await self.page.goto(url, wait_until="networkidle")
        
        return await self.get_page_info()
    
    async def get_page_info(self) -> PageInfo:
        """获取页面信息"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        return PageInfo(
            url=self.page.url,
            title=await self.page.title(),
            content=await self.page.content()
        )
    
    async def screenshot(self, full_page: bool = False) -> bytes:
        """截图"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        return await self.page.screenshot(full_page=full_page)
    
    async def click(self, selector: str):
        """点击元素"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        await self.page.click(selector)
    
    async def fill(self, selector: str, value: str):
        """填写表单"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        await self.page.fill(selector, value)
    
    async def type_text(self, selector: str, text: str, delay: int = 50):
        """输入文本（模拟按键）"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        await self.page.type(selector, text, delay=delay)
    
    async def press(self, key: str):
        """按键"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        await self.page.keyboard.press(key)
    
    async def evaluate(self, expression: str) -> Any:
        """执行 JavaScript"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        return await self.page.evaluate(expression)
    
    async def get_text(self, selector: str) -> str:
        """获取元素文本"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        return await self.page.text_content(selector)
    
    async def get_attribute(self, selector: str, attribute: str) -> Optional[str]:
        """获取元素属性"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        return await self.page.get_attribute(selector, attribute)
    
    async def query_selector(self, selector: str) -> Optional[ElementInfo]:
        """查询元素"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        element = await self.page.query_selector(selector)
        if not element:
            return None
        
        return ElementInfo(
            selector=selector,
            text=await element.text_content() or "",
            tag_name=await element.evaluate("el => el.tagName"),
            attributes=await element.evaluate("el => Object.fromEntries([...el.attributes].map(a => [a.name, a.value]))"),
            bounding_box=await element.bounding_box()
        )
    
    async def query_selector_all(self, selector: str) -> List[ElementInfo]:
        """查询所有匹配元素"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        elements = await self.page.query_selector_all(selector)
        
        results = []
        for element in elements:
            results.append(ElementInfo(
                selector=selector,
                text=await element.text_content() or "",
                tag_name=await element.evaluate("el => el.tagName"),
                attributes=await element.evaluate("el => Object.fromEntries([...el.attributes].map(a => [a.name, a.value]))"),
                bounding_box=await element.bounding_box()
            ))
        
        return results
    
    async def wait_for_selector(self, selector: str, timeout: int = None) -> ElementInfo:
        """等待元素出现"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        element = await self.page.wait_for_selector(
            selector,
            timeout=timeout or self.config.timeout
        )
        
        return ElementInfo(
            selector=selector,
            text=await element.text_content() or "",
            tag_name=await element.evaluate("el => el.tagName"),
            attributes=await element.evaluate("el => Object.fromEntries([...el.attributes].map(a => [a.name, a.value]))"),
            bounding_box=await element.bounding_box()
        )
    
    async def scroll_to(self, x: int = 0, y: int = 0):
        """滚动页面"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        await self.page.evaluate(f"window.scrollTo({x}, {y})")
    
    async def go_back(self):
        """后退"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        await self.page.go_back()
    
    async def go_forward(self):
        """前进"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        await self.page.go_forward()
    
    async def reload(self):
        """刷新页面"""
        if not self.page:
            raise RuntimeError("Browser not started")
        
        await self.page.reload()
    
    async def get_cookies(self) -> List[Dict[str, Any]]:
        """获取 cookies"""
        if not self.context:
            raise RuntimeError("Browser not started")
        
        return await self.context.cookies()
    
    async def set_cookies(self, cookies: List[Dict[str, Any]]):
        """设置 cookies"""
        if not self.context:
            raise RuntimeError("Browser not started")
        
        await self.context.add_cookies(cookies)
    
    async def save_screenshot(self, path: str, full_page: bool = False):
        """保存截图"""
        screenshot_bytes = await self.screenshot(full_page)
        Path(path).write_bytes(screenshot_bytes)
    
    async def get_console_logs(self) -> List[str]:
        """获取控制台日志"""
        # 需要在页面加载前设置监听
        logs = []
        
        def on_console(msg):
            logs.append(f"[{msg.type}] {msg.text}")
        
        self.page.on("console", on_console)
        
        return logs


class BrowserManager:
    """浏览器管理器"""
    
    def __init__(self):
        self.browsers: Dict[str, BrowserAutomation] = {}
    
    async def create_browser(
        self,
        name: str,
        config: BrowserConfig = None
    ) -> BrowserAutomation:
        """创建浏览器实例"""
        browser = BrowserAutomation(config)
        await browser.start()
        self.browsers[name] = browser
        return browser
    
    def get_browser(self, name: str) -> Optional[BrowserAutomation]:
        """获取浏览器实例"""
        return self.browsers.get(name)
    
    async def close_browser(self, name: str):
        """关闭浏览器"""
        browser = self.browsers.get(name)
        if browser:
            await browser.stop()
            del self.browsers[name]
    
    async def close_all(self):
        """关闭所有浏览器"""
        for name in list(self.browsers.keys()):
            await self.close_browser(name)
