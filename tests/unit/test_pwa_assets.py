"""PWA 静态件（P3）的语义网——手机入口能不能装、装完打开的是不是对话台，取决于这几条。"""

import json
from pathlib import Path

_WEB = Path(__file__).parent.parent.parent / "web"


def test_manifest_parses_and_opens_the_agent_page():
    m = json.loads((_WEB / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert m["start_url"] == "/agent", "加到主屏后打开的必须是对话台"
    assert m["display"] == "standalone"
    assert m["scope"] == "/"
    srcs = [icon["src"] for icon in m["icons"]]
    for src in srcs:
        assert (_WEB / src.lstrip("/")).exists(), f"manifest 引用的 {src} 不存在——装出来就是破图标"


def test_service_worker_never_caches():
    """SW 只为'可安装'存在。缓存哪怕一条，都会变成'改了代码浏览器还是旧的'排查成本。"""
    sw = (_WEB / "sw.js").read_text(encoding="utf-8")
    assert "fetch" in sw, "没有 fetch 监听器就不满足 Chrome 的安装条件"
    for banned in ("caches.open", "cache.put", "respondWith"):
        assert banned not in sw, f"sw.js 出现 {banned}——它承诺过什么都不缓存"


def test_agent_page_declares_the_pwa_head():
    html = (_WEB / "agent.html").read_text(encoding="utf-8")
    assert 'rel="manifest"' in html
    assert 'name="theme-color"' in html
    assert "serviceWorker" in html and '"/sw.js"' in html.replace("'", '"')
    assert "viewport-fit=cover" in html, "刘海屏 safe-area 变量依赖它才生效"


def test_mobile_css_keeps_ios_from_zooming_the_composer():
    """iOS 上输入框字号 <16px 聚焦会强制放大整页——这条丢了手机端就没法打字。"""
    html = (_WEB / "agent.html").read_text(encoding="utf-8")
    assert "font-size: 16px" in html
    assert "safe-area-inset-bottom" in html
    assert "100dvh" in html, "100vh 在手机上会被地址栏吃掉一截，composer 沉到屏幕外"
