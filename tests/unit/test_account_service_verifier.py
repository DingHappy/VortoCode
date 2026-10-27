import json
import asyncio

import pytest

from src.gateway import account_service
from src.gateway.account_service import AccountRejected, AccountUnavailable


class Response:
    def __init__(self, status, body, content_type="application/json"):
        self.status, self.body, self.content_type = status, body, content_type
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        raw = self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()
        for offset in range(0, len(raw), 3):  # Deliberately fragmented, not a single read.
            yield raw[offset : offset + 3]


def transport(monkeypatch, response):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["timeout"].total == 5

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def get(self, url, *, headers, allow_redirects):
            assert allow_redirects is False
            assert set(headers) == {"Authorization", "Accept"}
            calls.append(url)
            return response

    monkeypatch.setattr(account_service.aiohttp, "ClientSession", Client)
    monkeypatch.setenv("VORTOCODE_ACCOUNT_URL", "https://account.test")
    return calls


@pytest.mark.asyncio
async def test_verifier_reads_fragmented_bounded_body_from_server_owned_origin(monkeypatch):
    calls = transport(
        monkeypatch,
        Response(200, {"user_id": 12, "email": "display@example.test", "device": {"id": 3}}),
    )
    result = await account_service.verify_account_access("a" * 43, "https://account.test")
    assert result.identity.subject == "12" and result.device_id == 3
    assert calls == ["https://account.test/api/me"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body,content_type,expected",
    [
        (401, {}, "application/json", AccountRejected),
        (403, {}, "application/json", AccountRejected),
        (302, {}, "application/json", AccountUnavailable),
        (500, {}, "application/json", AccountUnavailable),
        (200, {}, "text/html", AccountUnavailable),
        (
            200,
            {"email": "legacy@example.test", "device": {"id": 3}},
            "application/json",
            AccountUnavailable,
        ),
        (200, {"user_id": True, "device": {"id": 3}}, "application/json", AccountUnavailable),
        (200, {"user_id": 12, "device": {"id": True}}, "application/json", AccountUnavailable),
        (200, b"x" * 17000, "application/json", AccountUnavailable),
        (200, b"not-json", "application/json", AccountUnavailable),
    ],
)
async def test_bad_or_unavailable_identity_never_becomes_authority(
    monkeypatch, status, body, content_type, expected
):
    transport(monkeypatch, Response(status, body, content_type))
    with pytest.raises(expected) as caught:
        await account_service.verify_account_access("a" * 43, "https://account.test")
    assert "a" * 43 not in str(caught.value)


@pytest.mark.asyncio
async def test_invalid_credential_never_contacts_provider(monkeypatch):
    calls = transport(monkeypatch, Response(200, {}))
    with pytest.raises(AccountRejected):
        await account_service.verify_account_access("invalid credential", "https://account.test")
    assert not calls


@pytest.mark.asyncio
async def test_issuer_change_never_forwards_a_credential_to_another_provider(monkeypatch):
    calls = transport(monkeypatch, Response(200, {}))
    with pytest.raises(account_service.AccountIssuerMismatch):
        await account_service.verify_account_access("a" * 43, "https://other.test")
    assert not calls


@pytest.mark.asyncio
async def test_timeout_is_unavailable_not_signed_out(monkeypatch):
    class TimeoutResponse(Response):
        async def __aenter__(self):
            raise asyncio.TimeoutError()

    transport(monkeypatch, TimeoutResponse(200, {}))
    with pytest.raises(AccountUnavailable):
        await account_service.verify_account_access("a" * 43, "https://account.test")
