"""Browser Verify V1 deterministic tests without launching a real browser."""
import json
import time
from pathlib import Path

import pytest

from src.browser import verify as browser_verify


class _Route:
    def __init__(self):
        self.action = None

    def abort(self, reason):
        self.action = ("abort", reason)

    def continue_(self):
        self.action = ("continue", None)


class _Request:
    def __init__(self, url):
        self.url = url


class _WebSocket:
    def __init__(self, url):
        self.url = url
        self.action = None

    def close(self, **kwargs):
        self.action = ("close", kwargs)

    def connect_to_server(self):
        self.action = ("connect", None)


class _Response:
    status = 200


class _Page:
    def __init__(self, calls, *, url="http://127.0.0.1:3000/", title="Fixture",
                 console_error="", page_error="", status=200, goto_error="",
                 screenshot_error=""):
        self.calls = calls
        self.url = url
        self._title = title
        self._console_error = console_error
        self._page_error = page_error
        self._status = status
        self._goto_error = goto_error
        self._screenshot_error = screenshot_error
        self.handlers = {}
        self.closed = False

    def on(self, event, handler):
        self.calls.append(f"listen:{event}")
        self.handlers[event] = handler

    def set_default_timeout(self, timeout):
        self.calls.append(f"timeout:{timeout}")

    def goto(self, url, **kwargs):
        self.calls.append("goto")
        if self._goto_error:
            raise RuntimeError(self._goto_error)
        if self._console_error:
            msg = type("Console", (), {"type": "error", "text": self._console_error})()
            self.handlers["console"](msg)
        if self._page_error:
            self.handlers["pageerror"](RuntimeError(self._page_error))
        response = _Response()
        response.status = self._status
        return response

    def title(self):
        return self._title

    def screenshot(self, path, **kwargs):
        self.calls.append("screenshot")
        if self._screenshot_error:
            raise RuntimeError(self._screenshot_error)
        Path(path).write_bytes(b"png")

    def close(self):
        self.closed = True


class _Context:
    def __init__(self, calls, page):
        self.calls = calls
        self.page = page
        self.http_handler = None
        self.ws_handler = None
        self.closed = False

    def route(self, pattern, handler):
        self.calls.append("route:http")
        self.http_handler = handler

    def route_web_socket(self, pattern, handler):
        self.calls.append("route:ws")
        self.ws_handler = handler

    def new_page(self):
        self.calls.append("new_page")
        return self.page

    def close(self):
        self.closed = True


class _Browser:
    def __init__(self, calls, context):
        self.calls = calls
        self.context = context
        self.closed = False

    def new_context(self, **kwargs):
        self.calls.append("new_context")
        assert kwargs["service_workers"] == "block"
        return self.context

    def close(self):
        self.closed = True


class _Chromium:
    def __init__(self, calls, browser):
        self.calls = calls
        self.browser = browser

    def launch(self, **kwargs):
        self.calls.append("launch")
        return self.browser


def _fake_playwright(*, url="http://127.0.0.1:3000/", console_error="", page_error="",
                     status=200, goto_error="", screenshot_error=""):
    calls = []
    page = _Page(calls, url=url, console_error=console_error, page_error=page_error,
                 status=status, goto_error=goto_error, screenshot_error=screenshot_error)
    context = _Context(calls, page)
    browser = _Browser(calls, context)
    playwright = type("Playwright", (), {})()
    playwright.chromium = _Chromium(calls, browser)
    return playwright, calls, page, context, browser


def _config(**overrides):
    config = {
        "url": "http://127.0.0.1:3000/",
        "wait_until": "load",
        "full_page": True,
        "fail_on_console_error": True,
        "console_error_ignore": [],
    }
    config.update(overrides)
    return config


@pytest.mark.parametrize("url, valid", [
    ("http://127.0.0.1:3000/", True),
    ("https://localhost/", True),
    ("http://[::1]:8000/", True),
    ("https://example.com/", False),
    ("http://10.0.0.1/", False),
    ("file:///etc/passwd", False),
])
def test_loopback_url_validation(url, valid):
    assert (browser_verify.loopback_url_error(url) == "") is valid


def test_network_boundary_blocks_non_loopback_http_and_websocket():
    playwright, _calls, _page, context, _browser = _fake_playwright()
    blocked = []
    browser_verify._install_network_boundary(context, blocked)

    local_route = _Route()
    context.http_handler(local_route, _Request("http://127.0.0.1:3000/app.js"))
    external_route = _Route()
    context.http_handler(external_route, _Request("https://example.com/track"))
    local_ws = _WebSocket("ws://localhost:3000/hmr")
    context.ws_handler(local_ws)
    external_ws = _WebSocket("wss://example.com/socket")
    context.ws_handler(external_ws)

    assert local_route.action[0] == "continue" and local_ws.action[0] == "connect"
    assert external_route.action[0] == "abort" and external_ws.action[0] == "close"
    assert [item["kind"] for item in blocked] == ["request", "websocket"]


def test_probe_installs_boundaries_and_listeners_before_navigation(tmp_path):
    playwright, calls, page, context, browser = _fake_playwright()
    result = browser_verify._run_with_playwright(
        playwright, _config(), tmp_path / "shot.png", 2)

    assert result["ok"] is True
    assert calls.index("route:http") < calls.index("new_page") < calls.index("listen:console")
    assert calls.index("route:ws") < calls.index("goto")
    assert calls.index("listen:pageerror") < calls.index("goto")
    assert page.closed and context.closed and browser.closed
    assert json.loads(json.dumps(result))["screenshot_path"].endswith("shot.png")


@pytest.mark.parametrize("config, expected", [
    (_config(), False),
    (_config(fail_on_console_error=False), True),
    (_config(console_error_ignore=["known warning"]), True),
])
def test_console_error_policy_is_literal_and_configurable(tmp_path, config, expected):
    playwright, _calls, _page, _context, _browser = _fake_playwright(
        console_error="known warning [x.*] still literal")
    result = browser_verify._run_with_playwright(
        playwright, config, tmp_path / f"{expected}.png", 2)
    assert result["ok"] is expected


def test_page_error_always_fails(tmp_path):
    playwright, _calls, _page, _context, _browser = _fake_playwright(page_error="boom")
    result = browser_verify._run_with_playwright(
        playwright, _config(fail_on_console_error=False), tmp_path / "page-error.png", 2)
    assert result["ok"] is False and result["page_errors"] == ["boom"]


def test_main_document_http_error_fails(tmp_path):
    playwright, _calls, _page, _context, _browser = _fake_playwright(status=503)
    result = browser_verify._run_with_playwright(
        playwright, _config(), tmp_path / "http-error.png", 2)
    assert result["ok"] is False and "HTTP 503" in result["output"]


def test_warmup_http_error_console_does_not_poison_successful_retry(tmp_path):
    playwright, _calls, page, _context, _browser = _fake_playwright()
    attempts = iter([
        (503, "Failed to load resource: the server responded with a status of 503"),
        (200, ""),
    ])

    def goto_with_warmup(_url, **_kwargs):
        status, console_error = next(attempts)
        if console_error:
            msg = type("Console", (), {"type": "error", "text": console_error})()
            page.handlers["console"](msg)
        response = _Response()
        response.status = status
        return response

    page.goto = goto_with_warmup
    result = browser_verify._run_with_playwright(
        playwright, _config(), tmp_path / "warmup.png", 2)

    assert result["ok"] is True
    assert result["console_errors"] == []
    assert result["transient_console_errors"] == [
        "Failed to load resource: the server responded with a status of 503"]


def test_screenshot_failure_fails_and_still_cleans_up(tmp_path):
    playwright, _calls, page, context, browser = _fake_playwright(screenshot_error="disk full")
    result = browser_verify._run_with_playwright(
        playwright, _config(), tmp_path / "screenshot-error.png", 2)
    assert result["ok"] is False and "截图失败" in result["output"]
    assert page.closed and context.closed and browser.closed


def test_navigation_timeout_is_bounded(tmp_path):
    playwright, _calls, _page, _context, _browser = _fake_playwright(goto_error="not ready")
    started = time.monotonic()
    result = browser_verify._run_with_playwright(
        playwright, _config(), tmp_path / "timeout.png", 1)
    elapsed = time.monotonic() - started
    assert result["ok"] is False and "not ready" in result["output"]
    assert elapsed < 1.5


def test_redirect_away_from_loopback_fails(tmp_path):
    playwright, _calls, _page, _context, _browser = _fake_playwright(
        url="https://example.com/redirected")
    result = browser_verify._run_with_playwright(
        playwright, _config(), tmp_path / "redirect.png", 2)
    assert result["ok"] is False and "最终 URL 被拒绝" in result["output"]


def test_blocked_subrequest_marks_result_red(tmp_path):
    playwright, _calls, page, context, _browser = _fake_playwright()
    original_goto = page.goto

    def goto_with_external_request(url, **kwargs):
        route = _Route()
        context.http_handler(route, _Request("http://169.254.169.254/latest/meta-data"))
        return original_goto(url, **kwargs)

    page.goto = goto_with_external_request
    result = browser_verify._run_with_playwright(
        playwright, _config(), tmp_path / "blocked.png", 2)
    assert result["ok"] is False and result["blocked_requests"]


def test_safe_evidence_path_never_uses_raw_segments(tmp_path):
    path = browser_verify.safe_evidence_path(tmp_path, "../run", "../../profile name")
    relative = path.relative_to(tmp_path)
    assert relative.parts[:3] == (".vortocode", "artifacts", "browser-verify")
    assert ".." not in relative.parts
    assert path.suffix == ".png"


def test_browser_launch_error_has_exact_install_commands(tmp_path, monkeypatch):
    class _BrokenPlaywright:
        def __enter__(self):
            raise RuntimeError("Executable doesn't exist")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(browser_verify, "_load_sync_playwright", lambda: _BrokenPlaywright)
    result = browser_verify.run_browser_probe(
        _config(), tmp_path / "missing-browser.png", timeout_seconds=1)
    assert result["ok"] is False
    assert "pip install 'vortocode[browser]'" in result["output"]
    assert "playwright install chromium" in result["output"]


def test_missing_playwright_has_exact_install_commands(tmp_path, monkeypatch):
    def missing_playwright():
        raise ImportError("No module named 'playwright'")

    monkeypatch.setattr(browser_verify, "_load_sync_playwright", missing_playwright)
    result = browser_verify.run_browser_probe(
        _config(), tmp_path / "missing-playwright.png", timeout_seconds=1)

    assert result["ok"] is False
    assert result["screenshot_path"] == ""
    assert "pip install 'vortocode[browser]'" in result["output"]
    assert "playwright install chromium" in result["output"]
