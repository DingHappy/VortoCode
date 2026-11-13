"""The managed Unix listener must retain the real account router's owner checks."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import socket as unix_socket
import tempfile

import aiohttp
import httpx
import pytest
import uvicorn

from src.gateway.account_access import AccountIdentity
from src.gateway.account_service import AccountSession

ORIGIN = "http://127.0.0.1:18731"
PATH = "/api/mobile/account/registration"
OWNER = {"Authorization": "Bearer fixture-resident-owner"}


@pytest.fixture
def account_api(monkeypatch):
    from src.web import server
    from src.web.routers import account_access

    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("VORTOONE_API_TOKEN", "fixture-resident-owner")
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    monkeypatch.setenv("VORTOONE_ACCOUNT_URL", "https://account.test")
    monkeypatch.setenv("VORTOONE_IM", "")
    calls = []
    identity = AccountSession(AccountIdentity("https://account.test", "fixture-owner"), 7)
    record = {"schema_version": 1, "instance_id": None, "revision": 0, "registration": None}

    async def verify(token, issuer):
        calls.append(("verify", token, issuer))
        return identity

    async def operation(method, *args):
        calls.append((method, *args))
        if method == "register":
            assert args == (identity, "Fixture assistant", 0, None)
            record.update(instance_id="a" * 32, revision=1, registration={"name": args[1]})
        elif method == "unregister":
            assert args == (1, "a" * 32)
            record.update(revision=2, registration=None)
        else:
            assert method == "read"
        return dict(record)

    # Keep the actual router and dependencies; only business storage/provider I/O is replaced.
    monkeypatch.setattr(account_access, "verify_account_access", verify)
    monkeypatch.setattr(account_access, "_operation", operation)
    return server, calls


def launched_app(monkeypatch, server, socket):
    launched = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: launched.append((app, kwargs)))
    server.start_server(uds=str(socket))
    app, options = launched.pop()
    assert options == {"uds": str(socket), "log_level": "warning", "access_log": False}
    return app


@asynccontextmanager
async def unix_account_client(monkeypatch, server, *, managed=True):
    # Short, canonical paths also work on macOS; this is an isolated fixture, not a runtime.
    with tempfile.TemporaryDirectory(prefix="vo-account-", dir=Path("/tmp").resolve()) as root:
        socket = Path(root) / "s"
        with unix_socket.socket(unix_socket.AF_UNIX, unix_socket.SOCK_STREAM) as placeholder:
            placeholder.bind(str(socket))
            socket.chmod(0o600)
        app = launched_app(monkeypatch, server, socket) if managed else server.app
        config = uvicorn.Config(
            app, uds=str(socket), lifespan="off", log_level="critical", access_log=False
        )
        fixture = uvicorn.Server(config)
        task = asyncio.create_task(fixture.serve())

        async def ready():
            while not fixture.started:
                if task.done():
                    await task
                    pytest.fail("Unix fixture stopped before accepting requests")
                await asyncio.sleep(0.01)

        try:
            await asyncio.wait_for(ready(), timeout=3)
            async with aiohttp.ClientSession(
                connector=aiohttp.UnixConnector(path=str(socket))
            ) as client:
                yield client
        finally:
            fixture.should_exit = True
            await asyncio.wait_for(task, timeout=3)


async def test_managed_unix_account_registration_retains_real_owner_dependencies(
    account_api, monkeypatch
):
    server, calls = account_api
    async with unix_account_client(monkeypatch, server) as client:
        async with client.get(ORIGIN + PATH, headers=OWNER) as response:
            assert response.status == 200, await response.text()
            assert (await response.json())["revision"] == 0
            assert response.headers["Cache-Control"] == "no-store"
        body = {
            "access_token": "a" * 43,
            "issuer": "https://account.test",
            "name": "Fixture assistant",
            "revision": 0,
            "instance_id": None,
        }
        async with client.put(
            ORIGIN + PATH, headers={**OWNER, "Origin": ORIGIN}, json=body
        ) as response:
            assert response.status == 200, await response.text()
            assert (await response.json())["revision"] == 1
        async with client.delete(
            ORIGIN + PATH, headers=OWNER, json={"revision": 1, "instance_id": "a" * 32}
        ) as response:
            assert response.status == 200, await response.text()
            assert (await response.json())["registration"] is None
    assert [call[0] for call in calls] == ["read", "verify", "register", "unregister"]


async def test_private_unix_transport_does_not_approve_missing_credentials_or_cross_origin(
    account_api, monkeypatch
):
    server, calls = account_api
    async with unix_account_client(monkeypatch, server) as client:
        for headers, expected in [
            ({}, 401),
            ({"Authorization": "Bearer wrong"}, 401),
            ({**OWNER, "Origin": "https://other.test"}, 403),
        ]:
            async with client.get(ORIGIN + PATH, headers=headers) as response:
                assert response.status == expected
                assert response.headers["Cache-Control"] == "no-store"
    assert not calls


async def test_unmanaged_unix_listener_is_not_implicitly_trusted(account_api, monkeypatch):
    server, calls = account_api
    async with unix_account_client(monkeypatch, server, managed=False) as client:
        async with client.get(ORIGIN + PATH, headers=OWNER) as response:
            assert response.status == 403
            assert (await response.json())["detail"] == "设备接口需要 HTTPS"
    assert not calls


async def test_managed_listener_does_not_change_a_later_network_listener(account_api, monkeypatch):
    server, calls = account_api
    async with unix_account_client(monkeypatch, server) as client:
        async with client.get(ORIGIN + PATH, headers=OWNER) as response:
            assert response.status == 200
        launched = []
        monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: launched.append((app, kwargs)))
        server.start_server(host="127.0.0.1", port=18731)
        network_app, options = launched.pop()
        assert options == {
            "host": "127.0.0.1",
            "port": 18731,
            "log_level": "warning",
            "access_log": False,
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=network_app, client=None), base_url=ORIGIN
        ) as http:
            response = await http.get(PATH, headers=OWNER)
        assert response.status_code == 403
    assert [call[0] for call in calls] == ["read"]


@pytest.mark.parametrize("client", [None, ("192.0.2.10", 12345)])
async def test_http_cannot_forge_private_transport_in_headers_or_query(account_api, client):
    server, calls = account_api
    headers = {**OWNER, "X-VortoOne-Private-Unix-Transport": "1", "X-Forwarded-Proto": "https"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app, client=client), base_url=ORIGIN
    ) as http:
        response = await http.get(PATH + "?private_unix_transport=true", headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "设备接口需要 HTTPS"
    assert not calls


@pytest.mark.parametrize("forged", [True, "true", object()])
async def test_untrusted_scope_value_cannot_supply_transport_evidence(account_api, forged):
    server, calls = account_api

    async def untrusted_app(scope, receive, send):
        scope = {**scope, "vortoone.private_unix_transport": forged}
        await server.app(scope, receive, send)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=untrusted_app, client=None), base_url=ORIGIN
    ) as http:
        response = await http.get(PATH, headers=OWNER)
    assert response.status_code == 403
    assert not calls


async def test_ordinary_loopback_account_transport_still_works(account_api):
    server, calls = account_api
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app, client=("127.0.0.1", 12345)), base_url=ORIGIN
    ) as http:
        response = await http.get(PATH, headers=OWNER)
    assert response.status_code == 200
    assert [call[0] for call in calls] == ["read"]
