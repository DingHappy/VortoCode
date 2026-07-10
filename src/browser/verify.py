"""Headless browser probe used by autonomous runtime verification.

Playwright is imported lazily so the core package stays usable without the
optional ``browser`` extra.  The probe is deliberately loopback-only: generated
pages run with host browser privileges, so subrequests and WebSockets must not
be able to reach the public internet or private network services.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import time
from pathlib import Path
from urllib.parse import urlparse

_LOCAL_HOSTS = {"localhost"}


def loopback_url_error(url: str, *, websocket: bool = False) -> str:
    """Return an explanatory error unless *url* is an HTTP(S)/WS(S) loopback URL."""
    try:
        parsed = urlparse(str(url or "").strip())
        _ = parsed.port  # force validation of malformed ports
    except (TypeError, ValueError):
        return "URL 无法解析"
    allowed = {"ws", "wss"} if websocket else {"http", "https"}
    if parsed.scheme.lower() not in allowed:
        return f"仅允许 {'WS(S)' if websocket else 'HTTP(S)'} loopback URL"
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return "URL 缺少主机名"
    if host in _LOCAL_HOSTS:
        return ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return f"主机不是 loopback 地址: {host}"
    return "" if ip.is_loopback else f"主机不是 loopback 地址: {host}"


def safe_evidence_path(repo_root: str | Path, run_id: str, profile_name: str) -> Path:
    """Build a collision-resistant screenshot path without raw user path segments."""
    from src.agents.dev_plan import ensure_state_gitignore

    root = Path(repo_root).resolve()
    ensure_state_gitignore(str(root))

    def _segment(value: str, fallback: str) -> str:
        raw = str(value or fallback)
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-_")[:48] or fallback
        digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:8]
        return f"{slug}-{digest}"

    run = _segment(run_id, "run")
    profile = _segment(profile_name, "runtime")
    return root / ".vortocode" / "artifacts" / "browser-verify" / run / f"{profile}.png"


def _base_result(config: dict, elapsed: float = 0.0) -> dict:
    # screenshot_path stays empty until a non-empty file is actually written, so
    # callers never report evidence that does not exist (missing Playwright,
    # launch failure, navigation that never reached the screenshot step).
    return {
        "ok": False,
        "requested_url": str(config.get("url") or ""),
        "final_url": "",
        "title": "",
        "screenshot_path": "",
        "console_errors": [],
        "page_errors": [],
        "blocked_requests": [],
        "elapsed_seconds": round(max(0.0, elapsed), 3),
        "output": "",
    }


def _install_network_boundary(context, blocked: list[dict]) -> None:
    """Install HTTP and WebSocket guards before any page is created/navigated."""

    def _route(route, request) -> None:
        url = str(getattr(request, "url", "") or "")
        reason = loopback_url_error(url)
        if reason:
            blocked.append({"kind": "request", "url": url, "reason": reason})
            route.abort("blockedbyclient")
        else:
            route.continue_()

    def _websocket(ws) -> None:
        url = str(getattr(ws, "url", "") or "")
        reason = loopback_url_error(url, websocket=True)
        if reason:
            blocked.append({"kind": "websocket", "url": url, "reason": reason})
            ws.close(code=1008, reason="VortoCode browser verify: non-loopback blocked")
        else:
            ws.connect_to_server()

    context.route("**/*", _route)
    route_ws = getattr(context, "route_web_socket", None)
    if not callable(route_ws):
        raise RuntimeError("Playwright 版本过旧，缺少 WebSocket 路由隔离；请安装 vortocode[browser]")
    route_ws("**/*", _websocket)


def _install_page_listeners(page, console_errors: list[str], page_errors: list[str]) -> None:
    """Attach listeners before goto so initial load failures are observable."""

    def _console(message) -> None:
        if str(getattr(message, "type", "") or "").lower() == "error":
            console_errors.append(str(getattr(message, "text", "") or message))

    page.on("console", _console)
    page.on("pageerror", lambda error: page_errors.append(str(error)))


def _summarize_failures(result: dict, *, nav_error: str, screenshot_error: str,
                        unignored_console: list[str], final_url_error: str,
                        response_error: str) -> str:
    reasons: list[str] = []
    for text in (nav_error, response_error, final_url_error):
        if text:
            reasons.append(text)
    if result["blocked_requests"]:
        first = result["blocked_requests"][0]
        reasons.append(f"阻止了非 loopback {first['kind']}: {first['url']}")
    if result["page_errors"]:
        reasons.append(f"页面异常: {result['page_errors'][0]}")
    if unignored_console:
        reasons.append(f"console.error: {unignored_console[0]}")
    if screenshot_error:
        reasons.append(f"截图失败: {screenshot_error}")
    return "；".join(reasons) if reasons else "browser verify passed"


def _run_with_playwright(playwright, config: dict, screenshot_path: Path,
                         timeout_seconds: int) -> dict:
    """Run the probe with an injected Playwright instance (also enables deterministic tests)."""
    started = time.monotonic()
    result = _base_result(config)
    browser = context = page = None
    nav_error = response_error = final_url_error = screenshot_error = ""
    navigated = False
    console_errors: list[str] = result["console_errors"]
    page_errors: list[str] = result["page_errors"]
    blocked: list[dict] = result["blocked_requests"]
    try:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(service_workers="block")
        _install_network_boundary(context, blocked)
        page = context.new_page()
        _install_page_listeners(page, console_errors, page_errors)
        page.set_default_timeout(max(1, int(timeout_seconds)) * 1000)

        url = str(config.get("url") or "")
        wait_until = str(config.get("wait_until") or "load")
        deadline = time.monotonic() + max(1, int(timeout_seconds))
        # Each goto gets the *full remaining* budget (not a hard 5s cap) so a
        # heavy dev bundle whose load event legitimately takes >5s can still
        # pass. Fast failures — connection refused, or a warm-up HTTP error like
        # a 503 while bundling — fall through and retry until the deadline, so
        # readiness does not depend on whether serve binds the socket before or
        # after it is actually ready. Each attempt reflects only its own state.
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            nav_error = response_error = ""
            try:
                response = page.goto(
                    url,
                    wait_until=wait_until,
                    timeout=max(50, int(remaining * 1000)),
                )
                if response is not None and int(getattr(response, "status", 200)) >= 400:
                    response_error = f"主文档返回 HTTP {response.status}"
                else:
                    navigated = True
                    break
            except Exception as exc:  # noqa: BLE001 - Playwright error classes are optional
                nav_error = str(exc)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.25, remaining))
        if not navigated and not response_error and not nav_error:
            nav_error = f"页面在 {max(1, int(timeout_seconds))} 秒内未完成加载"

        if page is not None:
            result["final_url"] = str(getattr(page, "url", "") or "")
            if navigated:
                final_url_error = loopback_url_error(result["final_url"])
                if final_url_error:
                    final_url_error = f"最终 URL 被拒绝: {final_url_error}"
                try:
                    result["title"] = str(page.title() or "")
                except Exception as exc:  # noqa: BLE001
                    page_errors.append(f"读取标题失败: {exc}")
            try:
                screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=str(screenshot_path),
                    full_page=bool(config.get("full_page", True)),
                    timeout=max(1, int(timeout_seconds)) * 1000,
                )
                if not screenshot_path.is_file() or screenshot_path.stat().st_size <= 0:
                    screenshot_error = "截图文件为空"
            except Exception as exc:  # noqa: BLE001
                screenshot_error = str(exc)
    finally:
        for item in (page, context, browser):
            if item is None:
                continue
            try:
                item.close()
            except Exception:  # noqa: BLE001 - cleanup must not mask verification evidence
                pass

    ignores = [str(v) for v in (config.get("console_error_ignore") or [])]
    unignored = [msg for msg in console_errors if not any(part in msg for part in ignores)]
    console_red = bool(config.get("fail_on_console_error", True) and unignored)
    result["ok"] = bool(
        navigated
        and not nav_error
        and not response_error
        and not final_url_error
        and not blocked
        and not page_errors
        and not console_red
        and not screenshot_error
    )
    result["elapsed_seconds"] = round(max(0.0, time.monotonic() - started), 3)
    result["screenshot_path"] = (
        str(screenshot_path)
        if screenshot_path.is_file() and screenshot_path.stat().st_size > 0
        else ""
    )
    result["output"] = _summarize_failures(
        result,
        nav_error=nav_error if not navigated else "",
        screenshot_error=screenshot_error,
        unignored_console=unignored if console_red else [],
        final_url_error=final_url_error,
        response_error=response_error,
    )
    return result


def run_browser_probe(config: dict, screenshot_path: str | Path,
                      timeout_seconds: int = 30) -> dict:
    """Run one Chromium probe and always return a JSON-serializable result."""
    path = Path(screenshot_path)
    started = time.monotonic()
    url_error = loopback_url_error(str(config.get("url") or ""))
    if url_error:
        result = _base_result(config, time.monotonic() - started)
        result["output"] = f"browser.url 被拒绝: {url_error}"
        return result
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result = _base_result(config, time.monotonic() - started)
        result["output"] = (
            "缺少 Playwright。请运行: pip install 'vortocode[browser]' && "
            "playwright install chromium"
        )
        return result
    try:
        with sync_playwright() as playwright:
            return _run_with_playwright(playwright, config, path, timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - optional runtime must fail as evidence, not crash pipeline
        result = _base_result(config, time.monotonic() - started)
        result["output"] = (
            f"Playwright/Chromium 无法启动: {exc}. 修复命令: "
            "pip install 'vortocode[browser]' && playwright install chromium"
        )
        return result
