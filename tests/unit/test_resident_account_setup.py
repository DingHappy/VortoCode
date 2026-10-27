"""Protect one-time owner setup, OAuth state, and temporary device cleanup."""
import importlib.util
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
import urllib.request

import pytest

spec = importlib.util.spec_from_file_location(
    "resident_setup", Path(__file__).resolve().parents[2] / "scripts/link-resident-account.py"
)
resident_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resident_setup)


def started():
    flow = resident_setup.Enrollment("test-host", "Test assistant")
    destination = flow.begin(flow.csrf, "http://127.0.0.1:49123/callback")
    return flow, parse_qs(urlsplit(destination).query)


def test_pkce_and_start_cannot_be_replayed():
    flow, query = started()
    assert query["client_id"] == ["vortocode-desktop"]
    assert query["code_challenge_method"] == ["S256"]
    assert flow.verifier not in json.dumps(query)
    with pytest.raises(ValueError):
        flow.begin(flow.csrf, "http://127.0.0.1:49123/callback")


@pytest.mark.parametrize("state", [[], ["wrong"], ["汉字"], ["a", "b"]])
def test_unbound_callbacks_cannot_claim_owner(state):
    flow, _ = started()
    with pytest.raises(ValueError):
        flow.complete({"state": state, "code": ["c" * 43]}, "http://127.0.0.1:49123/callback")
    assert not flow.claimed and not flow.finished


@pytest.mark.parametrize("enrollment_fails", [False, True])
def test_temporary_device_is_revoked_even_when_enrollment_fails(monkeypatch, enrollment_fails):
    flow, _ = started()
    calls = []

    def provider(path, *, access=None, form=None):
        calls.append(path)
        if path == "/oauth/token":
            return {"access_token": "a" * 43, "refresh_token": "r" * 43}
        if path == "/api/logout":
            return {"ok": True}
        if path == "/api/me" and calls.count(path) == 1:
            return {"user_id": 17, "device": {"id": 12}}
        raise HTTPError("https://account.example/api/me", 401, "Unauthorized", {}, None)

    def enroll(*args):
        if enrollment_fails:
            raise ValueError("Failure")
        return {"ok": True, "owner_registered": True, "revision": 1}

    monkeypatch.setattr(resident_setup, "provider", provider)
    monkeypatch.setattr(resident_setup, "enroll", enroll)
    flow.complete({"state": [flow.state], "code": ["c" * 43]}, "http://127.0.0.1:49123/callback")
    assert calls[-2:] == ["/api/logout", "/api/me"]
    assert flow.finished and not flow.verifier
    assert bool(flow.error) == enrollment_fails
    if not enrollment_fails:
        assert flow.receipt["temporary_device_revoked"]
    with pytest.raises(ValueError):
        flow.complete({"state": [flow.state], "code": ["c" * 43]}, "http://127.0.0.1:49123/callback")


def test_http_bridge_rejects_cross_origin_and_cleans_callback_url():
    flow = resident_setup.Enrollment("test-host", "Test")
    with resident_setup.HTTPServer(("127.0.0.1", 0), resident_setup.make_handler(flow)) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            origin = "http://127.0.0.1:" + str(server.server_port)
            client = urllib.request.build_opener(resident_setup.NoRedirect())
            with client.open(origin) as response:
                assert response.headers["Referrer-Policy"] == "same-origin"
                assert response.headers["Cache-Control"] == "no-store"
            request = urllib.request.Request(origin + "/start", data=b"csrf=wrong", headers={
                "Origin": "https://other.example", "Content-Type": "application/x-www-form-urlencoded"
            })
            with pytest.raises(HTTPError) as denied:
                client.open(request)
            assert denied.value.code == 403 and not flow.started
            flow.begin(flow.csrf, origin + "/callback")
            with pytest.raises(HTTPError) as redirect:
                client.open(origin + "/callback?state=" + flow.state + "&error=access_denied")
            assert redirect.value.code == 303 and redirect.value.headers["Location"] == "/done"
            assert redirect.value.headers["Cache-Control"] == "no-store"
            with client.open(origin + "/done") as response:
                assert b"access_denied" not in response.read()
        finally:
            server.shutdown()
            worker.join(3)


def test_expired_authorization_has_explicit_safe_outcome(monkeypatch):
    flow, _ = started()
    monkeypatch.setattr(resident_setup.time, "monotonic", lambda: flow.deadline + 1)
    with pytest.raises(ValueError):
        flow.complete({"state": [flow.state], "code": ["c" * 43]}, "http://127.0.0.1:49123/callback")
    receipt = flow.outcome()
    assert not receipt["ok"] and receipt["status"] == "expired" and receipt["error"]
    assert flow.state not in json.dumps(receipt) and flow.verifier not in json.dumps(receipt)


def test_cleanup_uncertainty_cannot_report_success(monkeypatch):
    flow, _ = started()

    def provider(path, **kwargs):
        if path == "/oauth/token":
            return {"access_token": "a" * 43}
        if path == "/api/me":
            return {"user_id": 17}
        raise OSError("Logout unavailable")

    monkeypatch.setattr(resident_setup, "provider", provider)
    monkeypatch.setattr(resident_setup, "enroll", lambda *args: {"ok": True, "owner_registered": True})
    flow.complete({"state": [flow.state], "code": ["c" * 43]}, "http://127.0.0.1:49123/callback")
    receipt = flow.outcome()
    assert receipt["owner_registered"] is True and receipt["ok"] is False
    assert receipt["status"] == "failed" and receipt["error"]


def test_expired_callback_cleans_url_and_cannot_be_revived():
    flow, _ = started()
    flow.deadline = 0
    with resident_setup.HTTPServer(("127.0.0.1", 0), resident_setup.make_handler(flow)) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            origin = "http://127.0.0.1:" + str(server.server_port)
            client = urllib.request.build_opener(resident_setup.NoRedirect())
            with pytest.raises(HTTPError) as redirect:
                client.open(origin + "/callback?state=" + flow.state + "&code=expired-code")
            assert redirect.value.code == 303 and redirect.value.headers["Location"] == "/done"
            with client.open(origin + "/done") as response:
                body = response.read().decode()
                assert "过期" in body and "expired-code" not in body
            assert flow.finished and not flow.verifier
            flow.deadline = resident_setup.time.monotonic() + 20
            with pytest.raises(ValueError):
                flow.complete({"state": [flow.state], "code": ["c" * 43]}, origin + "/callback")
        finally:
            server.shutdown()
            worker.join(3)
