import json

import pytest

from src.gateway import account_oauth_http as oauth
from src.gateway.account_service import AccountIssuerMismatch, AccountRejected, AccountUnavailable


def transport(monkeypatch, status, body, content_type="application/json"):
    calls = []

    class Response:
        def __init__(self):
            self.status, self.content_type, self.content = status, content_type, self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def iter_chunked(self, size):
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            for offset in range(0, len(raw), 3):
                yield raw[offset : offset + 3]

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False and kwargs["timeout"].total == 5

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def post(self, url, **kwargs):
            assert kwargs["allow_redirects"] is False
            calls.append(url)
            return Response()

    monkeypatch.setenv("VORTOCODE_ACCOUNT_URL", "https://account.test")
    monkeypatch.setattr(oauth.aiohttp, "ClientSession", Client)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body,content_type,error",
    [
        (401, {}, "application/json", AccountRejected),
        (302, {}, "application/json", AccountUnavailable),
        (200, {}, "text/html", AccountUnavailable),
        (200, [], "application/json", AccountUnavailable),
        (200, b"x" * 17000, "application/json", AccountUnavailable),
        (200, b"bad-json", "application/json", AccountUnavailable),
        (
            200,
            {"access_token": "a" * 43, "refresh_token": "b" * 43, "expires_in": True},
            "application/json",
            AccountUnavailable,
        ),
    ],
)
async def test_token_response_is_bounded_and_never_exposes_body(
    monkeypatch, status, body, content_type, error
):
    calls = transport(monkeypatch, status, body, content_type)
    with pytest.raises(error) as caught:
        await oauth.token_request("https://account.test", {"code": "private-code"})
    assert calls == ["https://account.test/oauth/token"]
    assert "private-code" not in str(caught.value)


@pytest.mark.asyncio
async def test_token_exchange_and_logout_stay_on_configured_provider(monkeypatch):
    calls = transport(
        monkeypatch, 200, {"access_token": "a" * 43, "refresh_token": "b" * 43, "expires_in": 900}
    )
    tokens = await oauth.token_request("https://account.test", {"code": "private-code"})
    assert tokens.expires_in == 900 and "a" * 43 not in repr(tokens)
    with pytest.raises(AccountIssuerMismatch):
        await oauth.token_request("https://other.test", {})
    await oauth.logout_provider("https://other.test", tokens.access)
    assert len(calls) == 1
    await oauth.logout_provider("https://account.test", tokens.access)
    assert calls[-1] == "https://account.test/api/logout"
