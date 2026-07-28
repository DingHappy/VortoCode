"""出网面工具：web_fetch / web_search / 无头截图。

这三个都申报 `untrusted_source=True`——抓回来的东西是不可信外部内容，摄入即给本回合
打污点（D0）。改这里前先读 src/agents/taint.py 的约定。
"""

from __future__ import annotations


from src.agents.tool import Tool
from src.agents.tools._common import _truthy


def build_web_tools() -> list[Tool]:
    """联网工具：`web_fetch`（按 URL 抓正文）+ `web_search`（按查询找网页）。

    两者都只读但外向。web_fetch 抓公网 http(s) URL 正文（SSRF 防护/封顶/超时/HTML→正文，
    见 src/agents/web_fetch.py）。web_search 走 DuckDuckGo HTML 端点把查询变成结果列表
    （无需 API key，见 src/agents/web_search.py），典型用法：web_search 找链接 → web_fetch 深读。
    read_only=True → plan 也可用、无需逐条确认（GET 不改状态，风险靠 SSRF/封顶/超时挡）。"""
    async def _web_fetch(args: dict) -> str:
        import asyncio
        from src.agents.web_fetch import fetch_url
        url = str(args.get("url") or args.get("href") or "").strip()
        return await asyncio.to_thread(fetch_url, url)

    async def _web_search(args: dict) -> str:
        import asyncio
        from src.agents.web_search import web_search
        query = str(args.get("query") or args.get("q") or "").strip()
        return await asyncio.to_thread(web_search, query)

    # untrusted_source=True：抓来的网页/搜索结果是**不可信外部内容**，摄入即给本回合打污点，
    # 之后同回合的对外动作（run_command/open_pr）会被提升确认等级（D0 防提示注入外发）。
    return [Tool("web_fetch",
                 "抓取一个公网 http(s) 网址的正文（查文档/issue/报错页/API 说明）：限 http/https、"
                 "拒私网与环回(SSRF 防护)、下载封顶、HTML 自动转正文。只读、无需确认",
                 {"url": "要抓取的 http(s) 网址"}, _web_fetch, read_only=True,
                 untrusted_source=True, external_content=True),
            Tool("web_search",
                 "联网搜索（DuckDuckGo，无需 key）：给查询返回若干「标题/URL/摘要」，再用 web_fetch "
                 "深读感兴趣的链接。查最新信息/报错/库用法时先搜后读。只读、无需确认",
                 {"query": "搜索关键词/问题"}, _web_search, read_only=True,
                 untrusted_source=True, external_content=True)]


def build_screenshot_tool(repo_root: str) -> list[Tool]:
    """给一个网址，截一张整页图，返回本地路径（配合 read_file 看图 / send_image 发人）。

    为什么要有：`web_fetch` 只拿得到文字。图表、仪表盘、排版、"这页长什么样"——文字转述丢掉的
    正是这些。真机 2026-07-27 用户问"截一张谷歌首页"，agent 只能如实说做不到：Playwright 的
    截图能力在机器上验通过，却从没接成工具（**这次它没撒谎，是真没有**）。

    与 browser/verify.py 的**运行时验证探针**刻意分开：那个 loopback-only（只准访问本机 serve），
    是给流水线验证用的；这个才是"上公网看页面"。两者混用会把验证探针的网络边界拆掉。

    风险面与 web_fetch 同级，所以复用它的护栏：只准 http(s)、拒私网与环回（SSRF）、超时封顶。
    untrusted_source=True —— 页面内容（连同图里写的字）是不可信外部输入，摄入即打污点。
    """
    from pathlib import Path

    base = Path(repo_root).resolve()

    async def _shot(args: dict) -> str:
        import time
        import uuid
        from urllib.parse import urlparse

        from src.agents.web_fetch import _host_is_safe

        url = str(args.get("url") or "").strip()
        if not url:
            return "screenshot_page 需要 url。"
        pr = urlparse(url)
        if pr.scheme not in ("http", "https"):
            return f"只支持 http/https：{url}"
        if not _host_is_safe(pr.hostname or ""):
            # 把解析结果一并给出：SSRF 防护分不清"真内网"和"被投毒的解析"，但人/模型能。
            # 真机 2026-07-27：www.google.com 在国内被投毒成 Facebook IP + Teredo 保留段，
            # 于是被这道闸拦下——闸没错，是环境如此。只说"拒绝私网"会让人以为是配置问题。
            import socket as _s
            try:
                got = sorted({i[4][0] for i in _s.getaddrinfo(pr.hostname, None)})[:4]
            except Exception:  # noqa: BLE001
                got = ["（本地解析失败）"]
            return (f"拒绝访问：{pr.hostname} 解析到私网/保留地址（SSRF 防护）→ {got}\n"
                    f"若该域名本应是公网站点，多半是本地 DNS 被投毒/劫持；换个域名或先用 "
                    f"web_search 找可达的镜像页。")
        full = _truthy(args.get("full_page", True))
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return ("未安装 playwright，无法截图。装：pip install playwright && "
                    "python -m playwright install chromium")

        out_dir = base / ".vortocode" / "shots" / time.strftime("%Y%m%d")
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:6]}.png"
        # 真实 UA + 中文 locale：无头浏览器会被不少站点反爬挡掉（真机撞过 403），
        # 这不是绕过风控，是让它表现得像普通浏览器。
        ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")
        try:
            # Playwright **不认** HTTP(S)_PROXY 环境变量，得显式传。不传的话在需要代理的
            # 环境里只会超时，而超时的报错完全看不出"其实是没走代理"（今天踩过同类坑）。
            import os as _os
            proxy_url = (_os.getenv("HTTPS_PROXY") or _os.getenv("HTTP_PROXY")
                         or _os.getenv("https_proxy") or _os.getenv("http_proxy") or "")
            launch_kw: dict = {"headless": True}
            if proxy_url:
                launch_kw["proxy"] = {"server": proxy_url,
                                      "bypass": _os.getenv("NO_PROXY", "")}
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(**launch_kw)
                try:
                    page = await browser.new_page(viewport={"width": 1280, "height": 900},
                                                  user_agent=ua, locale="zh-CN")
                    await page.goto(url, wait_until="domcontentloaded", timeout=40000)
                    await page.wait_for_timeout(2000)          # 等异步渲染落定
                    await page.screenshot(path=str(path), full_page=full)
                    title = (await page.title() or "").strip()
                finally:
                    await browser.close()
        except Exception as e:  # noqa: BLE001 —— 截图失败要给真原因，别只说"失败了"
            return f"截图失败：{type(e).__name__}: {' '.join(str(e).split())[:180]}"
        kb = path.stat().st_size // 1024
        return (f"✅ 已截图：{path}（{kb} KB，标题：{title[:60]}）\n"
                f"用 read_file 看内容，或 send_image 发给主人。")

    return [Tool("screenshot_page",
                 "给网址截一张页面图并落盘，返回路径。web_fetch 只拿得到文字；图表、仪表盘、"
                 "排版、'这页长什么样'要用它。拒私网/环回(SSRF 防护)、超时封顶。只读、无需确认",
                 {"url": "要截图的 http(s) 网址", "full_page": "可选，默认 true=整页；false=仅首屏"},
                 _shot, read_only=True, untrusted_source=True, external_content=True)]
