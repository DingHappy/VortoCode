"""Actual loopback Appium protocol fixtures, without SDK, devices or models."""
from dataclasses import replace

from aiohttp import web
import pytest

from src.devices.android import AndroidBinding, AndroidError, AppiumAndroid


@pytest.mark.parametrize("mode", ["ok", "redirect", "oversize", "error", "unexpected_ack"])
async def test_bounded_loopback_transport_does_not_create_sessions_or_follow_redirects(mode, monkeypatch):
    requests = []
    async def handler(request):
        body = await request.json() if request.method == "POST" else None
        requests.append((request.method, request.path, body))
        if mode == "redirect":
            return web.Response(status=302, headers={"Location": "/must-not-follow"})
        if mode == "oversize":
            return web.json_response({"value": "synthetic-secret" * 50000})
        if mode == "error":
            return web.json_response({"value": {"error": "invalid session id", "message": "synthetic-secret"}})
        suffix = request.path.removeprefix("/session/synthetic")
        values = {"": {"platformName": "Android", "appium:automationName": "UiAutomator2", "appium:udid": "emulator-5554", "appium:appPackage": "com.example.demo"},
                  "/context": "NATIVE_APP", "/appium/device/current_package": "com.example.demo",
                  "/source": "<hierarchy/>", "/actions": True if mode == "unexpected_ack" else None}
        return web.json_response({"value": values[suffix]})
    application = web.Application()
    application.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(application)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    try:
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        binding = AndroidBinding(f"http://127.0.0.1:{port}", "synthetic", "emulator-5554", "com.example.demo", "g1", "测试")
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
        driver = AppiumAndroid(binding)
        if mode in {"ok", "unexpected_ack"}:
            assert await driver.observe() == "<hierarchy/>"
            if mode == "ok":
                await driver.tap(5, 10)
            else:
                with pytest.raises(AndroidError):
                    await driver.tap(5, 10)
            assert [item[:2] for item in requests if item[0] == "POST"] == [("POST", "/session/synthetic/actions")]
        else:
            with pytest.raises(AndroidError) as error:
                await driver.observe()
            assert "synthetic-secret" not in str(error.value) and len(requests) == 1
        assert all("cookie" not in path and path not in {"/session", "/sessions"} for _method, path, _body in requests)
        with pytest.raises(AndroidError):
            await driver._call("POST", "/execute/sync", {"script": "mobile: shell"})
        assert driver.binding == replace(binding)
    finally:
        await runner.cleanup()
