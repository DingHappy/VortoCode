"""browser 路由（从 server.py 拆出）。"""
from src.web.deps import *  # noqa: F401,F403

router = APIRouter()

@router.post("/api/browser/navigate")
async def browser_navigate(request: BrowserNavigateRequest):
    """浏览器导航（默认禁用，需 VORTOCODE_ENABLE_BROWSER=1；URL 防 SSRF/file://）"""
    require_browser()  # fail-closed：必须在 try 之外，否则 403 会被吞成 200
    reason = validate_navigation_url(request.url)
    if reason:
        raise HTTPException(status_code=400, detail=f"URL 被拒绝：{reason}")
    try:
        browser = state.browser_manager.get_browser(request.browser_name)
        if not browser:
            browser = await state.browser_manager.create_browser(request.browser_name)
        
        page_info = await browser.navigate(request.url)
        
        return {
            "success": True,
            "url": page_info.url,
            "title": page_info.title
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/browser/screenshot")
async def browser_screenshot(browser_name: str = "default"):
    """浏览器截图（默认禁用，需 VORTOCODE_ENABLE_BROWSER=1）"""
    require_browser()
    try:
        browser = state.browser_manager.get_browser(browser_name)
        if not browser:
            return {"success": False, "error": "Browser not initialized"}
        
        screenshot = await browser.screenshot()
        
        return {
            "success": True,
            "screenshot": base64.b64encode(screenshot).decode()
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/browser/click")
async def browser_click(selector: str, browser_name: str = "default"):
    """浏览器点击（默认禁用，需 VORTOCODE_ENABLE_BROWSER=1）"""
    require_browser()
    try:
        browser = state.browser_manager.get_browser(browser_name)
        if not browser:
            return {"success": False, "error": "Browser not initialized"}
        
        await browser.click(selector)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.post("/api/browser/fill")
async def browser_fill(selector: str, value: str, browser_name: str = "default"):
    """浏览器填写表单（默认禁用，需 VORTOCODE_ENABLE_BROWSER=1）"""
    require_browser()
    try:
        browser = state.browser_manager.get_browser(browser_name)
        if not browser:
            return {"success": False, "error": "Browser not initialized"}
        
        await browser.fill(selector, value)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
