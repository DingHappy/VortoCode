"""The managed observer never turns its virtual origin into a TCP destination."""

from contextlib import asynccontextmanager
from pathlib import Path
import socket
import tempfile

from aiohttp import web
import pytest

from src.scheduling import codex_observer as observer


@asynccontextmanager
async def resident_socket(monkeypatch):
    # macOS sockaddr_un has a short path limit; pytest's nested tmp_path can exceed it.
    with tempfile.TemporaryDirectory(prefix="vorto-cdx-", dir=Path("/tmp").resolve()) as folder:
        path = Path(folder) / "service.sock"
        requests = []

        async def handle(request):
            requests.append((request.method, request.path, request.headers.get("Authorization"),
                             await request.json() if request.can_read_body else None))
            if "/redirect" in request.path:
                raise web.HTTPTemporaryRedirect(location="/unexpected")
            return web.json_response({"received": request.path})

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handle)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            await web.UnixSite(runner, str(path)).start()
            path.chmod(0o600)
            monkeypatch.setenv("VORTOONE_CODEX_REPORT_URL", "http://127.0.0.1:1")
            monkeypatch.setenv("VORTOONE_CODEX_REPORT_TOKEN", "fixture-observer-secret")
            monkeypatch.setenv("VORTOONE_CODEX_REPORT_SOCKET", str(path))
            monkeypatch.setattr(observer, "user_idle_seconds", lambda: None)
            yield path, requests
        finally:
            await runner.cleanup()


async def test_managed_report_and_action_requests_reach_only_private_unix_socket(monkeypatch):
    async with resident_socket(monkeypatch) as (_, requests):
        assert await observer.post_report([{"id": "fixture-thread"}])
        assert await observer.resident_call("GET", "/api/codex/status") == {
            "received": "/api/codex/status"}
        assert await observer.resident_call("POST", "/api/codex/actions/cdx-fixture/claim", {"claim": True}) == {
            "received": "/api/codex/actions/cdx-fixture/claim"}
        assert [item[:2] for item in requests] == [
            ("POST", "/api/codex/report"), ("GET", "/api/codex/status"),
            ("POST", "/api/codex/actions/cdx-fixture/claim")]
        assert all(item[2] == "Bearer fixture-observer-secret" for item in requests)
        assert requests[0][3]["threads"] == [{"id": "fixture-thread"}]
        assert requests[2][3] == {"claim": True}


async def test_managed_socket_requests_never_follow_redirects(monkeypatch):
    async with resident_socket(monkeypatch) as (_, requests):
        monkeypatch.setenv("VORTOONE_CODEX_REPORT_URL", "http://127.0.0.1:1/api/codex/redirect")
        with pytest.raises(observer.ResidentHTTPError) as failure:
            await observer.resident_call("POST", "", {"fixture": True})
        assert failure.value.status == 307
        assert [item[1] for item in requests] == ["/api/codex/redirect"]
        assert await observer.post_report([]) is False
        assert [item[1] for item in requests] == [
            "/api/codex/redirect", "/api/codex/redirect/api/codex/report"]


@pytest.mark.parametrize("call", ["report", "action"])
async def test_missing_managed_socket_fails_without_creating_tcp_connector(monkeypatch, call):
    import aiohttp

    with tempfile.TemporaryDirectory(prefix="vorto-cdx-", dir=Path("/tmp").resolve()) as folder:
        monkeypatch.setenv("VORTOONE_CODEX_REPORT_URL", "http://127.0.0.1:1")
        monkeypatch.setenv("VORTOONE_CODEX_REPORT_TOKEN", "fixture-observer-secret")
        monkeypatch.setenv("VORTOONE_CODEX_REPORT_SOCKET", str(Path(folder) / "missing.sock"))
        def forbidden_tcp(*args, **kwargs):
            pytest.fail("managed requests must never create a TCP connector")
        monkeypatch.setattr(aiohttp, "TCPConnector", forbidden_tcp)
        with pytest.raises(RuntimeError) as failure:
            if call == "report":
                await observer.post_report([])
            else:
                await observer.resident_call("GET", "/api/codex/status")
        assert "fixture-observer-secret" not in str(failure.value)


async def test_private_socket_connection_failure_never_falls_back_to_tcp(monkeypatch):
    import aiohttp

    with tempfile.TemporaryDirectory(prefix="vorto-cdx-", dir=Path("/tmp").resolve()) as folder:
        path = Path(folder) / "closed.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as endpoint:
            endpoint.bind(str(path))
        path.chmod(0o600)
        monkeypatch.setenv("VORTOONE_CODEX_REPORT_URL", "http://127.0.0.1:1")
        monkeypatch.setenv("VORTOONE_CODEX_REPORT_TOKEN", "fixture-observer-secret")
        monkeypatch.setenv("VORTOONE_CODEX_REPORT_SOCKET", str(path))
        def forbidden_tcp(*args, **kwargs):
            pytest.fail("failed UDS must never fall back to TCP")
        monkeypatch.setattr(aiohttp, "TCPConnector", forbidden_tcp)
        with pytest.raises(RuntimeError) as failure:
            await observer.resident_call("GET", "/api/codex/status")
        assert "fixture-observer-secret" not in str(failure.value)


@pytest.mark.parametrize("unsafe", ["shared_socket", "shared_parent", "linked_socket"])
async def test_unsafe_managed_socket_is_rejected_before_sending_credentials(monkeypatch, unsafe):
    async with resident_socket(monkeypatch) as (path, requests):
        if unsafe == "shared_socket":
            path.chmod(0o666)
        elif unsafe == "shared_parent":
            path.parent.chmod(0o755)
        else:
            link = path.with_name("linked.sock")
            link.symlink_to(path)
            monkeypatch.setenv("VORTOONE_CODEX_REPORT_SOCKET", str(link))
        with pytest.raises(RuntimeError):
            await observer.resident_call("GET", "/api/codex/status")
        assert requests == []
