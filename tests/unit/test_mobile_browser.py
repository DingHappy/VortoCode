import asyncio
import base64
import hashlib
import html
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from src.gateway.account_access import AccountIdentity
from src.gateway.account_registration import AccountRegistration
from src.gateway.account_service import AccountSession, AccountUnavailable
from src.gateway.account_oauth_http import OAuthTokens
from src.gateway.mobile_browser import BrowserDenied, MobileBrowserSessions, browser_config


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "browser-root-test")
    monkeypatch.setenv("VORTOCODE_WORKSPACE_SCOPE", "general")
    monkeypatch.setenv("VORTOCODE_ACCOUNT_URL", "https://account.test")
    monkeypatch.setenv("VORTOCODE_MOBILE_PUBLIC_ORIGIN", "https://gateway.test")
    owner = AccountIdentity("https://account.test", "12")
    AccountRegistration(str(tmp_path)).register(AccountSession(owner, 1), "助手", 0)
    from src.gateway import mobile_browser
    from src.web.server import app

    now, calls = [100], []
    sessions = MobileBrowserSessions(clock=lambda: now[0])
    monkeypatch.setattr(app.state, "mobile_browser", sessions, raising=False)

    async def tokens(issuer, form):
        calls.append(form["grant_type"])
        return OAuthTokens(
            "a" * 43, "b" * 43 if form["grant_type"] == "authorization_code" else "c" * 43, 900
        )

    async def verify(access, issuer):
        return AccountSession(owner, 2)

    async def logout(issuer, access):
        calls.append("logout")

    monkeypatch.setattr(mobile_browser, "token_request", tokens)
    monkeypatch.setattr(mobile_browser, "verify_account_access", verify)
    monkeypatch.setattr(mobile_browser, "logout_provider", logout)
    client = TestClient(app, base_url="https://gateway.test")
    yield client, sessions, now, calls, str(tmp_path), owner
    client.close()


ORIGIN = {"Origin": "https://gateway.test"}


def sign_in(client):
    assert client.get("/api/mobile/browser/status").json()["ready"]
    response = client.post("/api/mobile/account/login", headers=ORIGIN)
    assert response.status_code == 200
    state = parse_qs(urlsplit(response.json()["authorize_url"]).query)["state"][0]
    callback = client.get(
        "/api/mobile/account/callback",
        params={"state": state, "code": "fake-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 303 and callback.headers["location"] == "/mobile"
    return state


def test_account_cookie_login_reads_without_provider_tokens_in_browser_or_files(setup):
    client, sessions, now, calls, root, _ = setup
    state = sign_in(client)
    cookies = client.cookies
    assert not any("vortocode_session" == key for key in cookies)
    response = client.get("/api/mobile/browser/bootstrap")
    assert response.status_code == 200
    assert response.json()["capabilities"]["writes"] == []
    assert set(response.json()) == {"schema_version", "instance_id", "device", "capabilities"}
    assert not any(
        session.resource_token in response.text for session in sessions.sessions.values()
    )
    assert "a" * 43 not in response.text and "b" * 43 not in response.text
    snapshot = client.get("/api/mobile/browser/snapshot")
    assert snapshot.status_code == 200 and snapshot.headers["cache-control"] == "no-store"
    assert client.get("/api/mobile/account/registration").status_code == 401
    assert client.get("/api/mobile/v1/snapshot").status_code == 401
    text = AccountRegistration(root).path.read_text()
    assert "a" * 43 not in text and "b" * 43 not in text
    assert (
        client.get(
            "/api/mobile/account/callback", params={"state": state, "code": "fake-code"}
        ).status_code
        == 400
    )
    assert client.get("/api/mobile/browser/status").json()["signed_in"]


def test_login_requires_origin_context_and_pending_cookie(setup):
    client, sessions, *_ = setup
    assert client.post("/api/mobile/account/login", headers=ORIGIN).status_code == 401
    client.get("/api/mobile/browser/status")
    assert (
        client.post(
            "/api/mobile/account/login", headers={"Origin": "https://evil.test"}
        ).status_code
        == 401
    )
    response = client.post("/api/mobile/account/login", headers=ORIGIN)
    assert "httponly" in response.headers["set-cookie"].lower()
    state = parse_qs(urlsplit(response.json()["authorize_url"]).query)["state"][0]
    from src.web.server import app

    stranger = TestClient(app, base_url="https://gateway.test")
    assert (
        stranger.get(
            "/api/mobile/account/callback", params={"state": state, "code": "x"}
        ).status_code
        == 400
    )
    assert len(sessions.pending) == 1
    stranger.close()


def test_wrong_account_never_becomes_session(setup, monkeypatch):
    client, sessions, *_ = setup
    from src.gateway import mobile_browser

    async def other(*args):
        return AccountSession(AccountIdentity("https://account.test", "99"), 3)

    monkeypatch.setattr(mobile_browser, "verify_account_access", other)
    client.get("/api/mobile/browser/status")
    response = client.post("/api/mobile/account/login", headers=ORIGIN)
    state = parse_qs(urlsplit(response.json()["authorize_url"]).query)["state"][0]
    callback = client.get(
        "/api/mobile/account/callback", params={"state": state, "code": "x"}, follow_redirects=False
    )
    assert callback.headers["location"] == "/mobile?login=failed"
    assert not sessions.sessions
    assert client.get("/api/mobile/browser/snapshot").status_code == 401


def test_logout_revokes_session_and_pending_callback(setup):
    client, sessions, _, _, root, _ = setup
    sign_in(client)
    assert client.get("/api/mobile/browser/snapshot").status_code == 200
    assert (
        client.post(
            "/api/mobile/browser/logout", headers={"Origin": "https://evil.test"}
        ).status_code
        == 401
    )
    assert client.post("/api/mobile/browser/logout", headers=ORIGIN).status_code == 200
    assert client.get("/api/mobile/browser/snapshot").status_code == 401
    assert not sessions.sessions
    assert AccountRegistration(root).list()["devices"][0]["revoked_at"] is not None


def test_unbind_owner_invalidates_browser_read_immediately(setup):
    client, _, _, _, root, _ = setup
    sign_in(client)
    assert client.get("/api/mobile/browser/bootstrap").status_code == 200
    AccountRegistration(root).unregister(1)
    assert client.get("/api/mobile/browser/snapshot").status_code == 401


@pytest.mark.asyncio
async def test_logout_during_callback_cannot_restore_session(setup, monkeypatch):
    _, sessions, _, _, root, _ = setup
    from src.gateway import mobile_browser

    current = browser_config(root)
    context = sessions.context(current)
    url, proof = sessions.begin(current, context)
    pending = sessions.claim(current, parse_qs(urlsplit(url).query)["state"][0], proof)
    started, result = asyncio.Event(), asyncio.Future()

    async def blocked(*args):
        started.set()
        return await result

    monkeypatch.setattr(mobile_browser, "token_request", blocked)
    task = asyncio.create_task(sessions.complete(pending, "code"))
    await started.wait()
    sessions.invalidate_context(root, context)
    result.set_result(OAuthTokens("a" * 43, "b" * 43, 900))
    with pytest.raises(BrowserDenied):
        await task
    assert not sessions.sessions


@pytest.mark.asyncio
async def test_refresh_is_serial_and_uncertain_result_is_never_retried(setup, monkeypatch):
    _, sessions, now, calls, root, _ = setup
    from src.gateway import mobile_browser

    current = browser_config(root)
    context = sessions.context(current)
    url, proof = sessions.begin(current, context)
    pending = sessions.claim(current, parse_qs(urlsplit(url).query)["state"][0], proof)
    secret = await sessions.complete(pending, "code")
    session = sessions.find(root, {current.session_cookie: secret})
    await sessions.grant(session)
    now[0] = 1000
    await asyncio.gather(sessions.grant(session), sessions.grant(session))
    assert calls.count("refresh_token") == 1
    now[0] = 1900
    refreshes = []

    async def lost(*args):
        refreshes.append(True)
        raise AccountUnavailable()

    monkeypatch.setattr(mobile_browser, "token_request", lost)
    with pytest.raises(AccountUnavailable):
        await sessions.grant(session)
    with pytest.raises(BrowserDenied):
        await sessions.grant(session)
    assert len(refreshes) == 1 and not sessions.sessions


@pytest.mark.asyncio
async def test_logout_while_refreshing_revokes_late_rotated_credentials(setup, monkeypatch):
    _, sessions, now, _, root, _ = setup
    from src.gateway import mobile_browser

    current = browser_config(root)
    context = sessions.context(current)
    url, proof = sessions.begin(current, context)
    pending = sessions.claim(current, parse_qs(urlsplit(url).query)["state"][0], proof)
    secret = await sessions.complete(pending, "code")
    session = sessions.find(root, {current.session_cookie: secret})
    now[0] = 1000
    started, result, revoked = asyncio.Event(), asyncio.Future(), []

    async def blocked(*args):
        started.set()
        return await result

    async def logout(issuer, access):
        revoked.append(access)

    monkeypatch.setattr(mobile_browser, "token_request", blocked)
    monkeypatch.setattr(mobile_browser, "logout_provider", logout)
    read = asyncio.create_task(sessions.grant(session))
    await started.wait()
    exiting = asyncio.create_task(sessions.logout(session))
    await asyncio.sleep(0)
    result.set_result(OAuthTokens("c" * 43, "d" * 43, 900))
    with pytest.raises(BrowserDenied):
        await read
    await exiting
    assert "c" * 43 in revoked and not sessions.sessions


def test_cookie_security_expiry_and_logout_after_owner_unbind(setup):
    client, sessions, now, _, root, _ = setup
    sign_in(client)
    AccountRegistration(root).unregister(1)
    assert client.post("/api/mobile/browser/logout", headers=ORIGIN).status_code == 200
    assert not sessions.sessions
    assert not client.get("/api/mobile/browser/status").json()["ready"]


def test_absolute_expiry_drops_session_and_revokes_read_lease(setup):
    client, sessions, now, _, root, _ = setup
    sign_in(client)
    assert client.get("/api/mobile/browser/bootstrap").status_code == 200
    now[0] += 8 * 3600
    assert client.get("/api/mobile/browser/snapshot").status_code == 401
    assert not sessions.sessions
    assert AccountRegistration(root).list()["devices"][0]["revoked_at"] is not None


def test_missing_public_origin_and_wrong_host_do_not_advertise_login(setup, monkeypatch):
    client, *_ = setup
    assert not client.get("/api/mobile/browser/status", headers={"Host": "other.test"}).json()[
        "ready"
    ]
    monkeypatch.delenv("VORTOCODE_MOBILE_PUBLIC_ORIGIN")
    assert not client.get("/api/mobile/browser/status").json()["ready"]
    assert client.post("/api/mobile/account/login", headers=ORIGIN).status_code == 503


def test_mobile_account_login_requires_configured_admin_identity(setup, monkeypatch):
    client, *_ = setup
    monkeypatch.setenv("VORTOCODE_API_TOKEN", "")
    monkeypatch.setenv("AUTODEV_API_TOKEN", "")
    assert not client.get("/api/mobile/browser/status").json()["ready"]


def native_handoff(client):
    verifier, state = "v" * 43, "n" * 43
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    start = client.get(
        "/api/mobile/native/authorize",
        params={"challenge": challenge, "state": state},
        follow_redirects=False,
    )
    assert start.status_code == 303
    oauth_state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    response = client.get(
        "/api/mobile/account/callback",
        params={"state": oauth_state, "code": "code"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    callback = urlsplit(html.unescape(re.search(r'href="([^"]+)"', response.text)[1]))
    assert callback.scheme == "vortocode-mobile-auth" and callback.netloc == "callback"
    ticket = parse_qs(callback.query)["ticket"][0]
    return {"ticket": ticket, "state": state, "verifier": verifier}


def test_native_handoff_is_pkce_bound_one_time_and_read_only(setup):
    client, sessions, *_ = setup
    body = native_handoff(client)
    assert (
        client.post("/api/mobile/native/exchange", json={**body, "verifier": "z" * 43}).status_code
        == 401
    )
    result = client.post("/api/mobile/native/exchange", json=body)
    assert result.status_code == 200
    assert "a" * 43 not in result.text and "b" * 43 not in result.text
    token = result.json()["token"]
    assert token.startswith("vcn_")
    assert client.post("/api/mobile/native/exchange", json=body).status_code == 401
    headers = {"Authorization": "Bearer " + token}
    boot = client.get("/api/mobile/native/bootstrap", headers=headers)
    assert boot.status_code == 200 and set(boot.json()) == {
        "schema_version",
        "instance_id",
        "device",
        "capabilities",
    }
    assert token not in boot.text and "vcm_" not in boot.text
    assert client.get("/api/mobile/native/snapshot", headers=headers).status_code == 200
    assert client.get("/api/mobile/account/registration", headers=headers).status_code == 401
    assert client.get("/api/mobile/v1/snapshot", headers=headers).status_code == 401
    assert client.get("/api/mobile/native/snapshot").status_code == 401
    assert client.post("/api/mobile/native/logout", headers=headers).status_code == 200
    assert client.get("/api/mobile/native/snapshot", headers=headers).status_code == 401
    assert not sessions.sessions


def test_native_ticket_expiry_and_owner_unbind_prevent_exchange(setup):
    client, sessions, now, _, root, _ = setup
    body = native_handoff(client)
    now[0] += 61
    assert client.post("/api/mobile/native/exchange", json=body).status_code == 401
    body = native_handoff(client)
    AccountRegistration(root).unregister(1)
    assert client.post("/api/mobile/native/exchange", json=body).status_code == 401


def test_native_start_rejects_bad_state_duplicate_challenge_or_other_origin(setup):
    client, *_ = setup
    path = "/api/mobile/native/authorize"
    for query in [
        "challenge=a&state=b",
        "challenge=" + "c" * 43 + "&challenge=" + "d" * 43 + "&state=" + "s" * 43,
    ]:
        assert client.get(path + "?" + query, follow_redirects=False).status_code == 401
    assert (
        client.get(
            path, headers={"Origin": "https://evil.test"}, follow_redirects=False
        ).status_code
        == 401
    )
