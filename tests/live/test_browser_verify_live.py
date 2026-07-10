"""Opt-in real Chromium smoke test for Browser Verify V1."""
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.browser.verify import run_browser_probe


pytestmark = pytest.mark.skipif(
    os.getenv("VORTOCODE_LIVE_BROWSER") != "1",
    reason="set VORTOCODE_LIVE_BROWSER=1 after installing Playwright Chromium",
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<!doctype html><title>Browser Verify Fixture</title><h1>ready</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        return


class _WarmupHandler(_Handler):
    requests = 0

    def do_GET(self):
        type(self).requests += 1
        if type(self).requests == 1:
            body = b"warming"
            self.send_response(503)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


def test_live_browser_verify_writes_nonempty_screenshot(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        screenshot = tmp_path / "live.png"
        result = run_browser_probe({
            "url": f"http://127.0.0.1:{server.server_port}/",
            "wait_until": "load",
            "full_page": True,
            "fail_on_console_error": True,
            "console_error_ignore": [],
        }, screenshot, timeout_seconds=10)
        assert result["ok"], result
        assert screenshot.stat().st_size > 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_live_browser_verify_discards_warmup_console_error_for_final_result(tmp_path):
    _WarmupHandler.requests = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _WarmupHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_browser_probe({
            "url": f"http://127.0.0.1:{server.server_port}/",
            "wait_until": "load",
            "full_page": True,
            "fail_on_console_error": True,
            "console_error_ignore": [],
        }, tmp_path / "warmup.png", timeout_seconds=10)
        assert result["ok"], result
        assert result["console_errors"] == []
        assert any("503" in error for error in result["transient_console_errors"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
