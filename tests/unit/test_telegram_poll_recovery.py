"""Telegram recovery keeps failure evidence without secrets or real Bot requests."""
import asyncio
import json

import aiohttp
import pytest

from src.im.telegram import TelegramAdapter, TelegramError

SECRET = "synthetic-bot-secret"


def response(data, status=200):
    class Response:
        async def __aenter__(self):
            self.status = status
            return self
        async def __aexit__(self, *args):
            pass
        async def json(self):
            if isinstance(data, Exception):
                raise data
            return data
    class Session:
        def post(self, *args, **kwargs):
            return Response()
    return Session()


@pytest.mark.parametrize("code,category", [(429, "rate_limited"), (401, "authorization"), (403, "authorization"),
                                          (409, "conflict"), (503, "server_error"), (400, "api_error")])
async def test_bot_errors_retain_safe_code_and_retry_metadata(code, category):
    adapter = TelegramAdapter(SECRET, "synthetic-owner")
    adapter._session = response({"ok": False, "error_code": code, "description": SECRET,
                                 "parameters": {"retry_after": 87, "private": SECRET}})
    with pytest.raises(TelegramError) as caught:
        await adapter._default_request("getUpdates", {})
    assert caught.value.category == category and caught.value.code == code
    assert caught.value.retry_after == (87 if code == 429 else None)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("failure,category", [(asyncio.TimeoutError(SECRET), "timeout"),
                                              (aiohttp.ClientConnectionError(SECRET), "transport_error"),
                                              (ValueError(SECRET), "invalid_response")])
async def test_transport_and_decode_errors_preserve_only_fixed_category(failure, category):
    adapter = TelegramAdapter(SECRET, "synthetic-owner")
    adapter._session = response(failure)
    with pytest.raises(TelegramError) as caught:
        await adapter._default_request("getUpdates", {})
    assert caught.value.category == category and SECRET not in str(caught.value)


async def test_non_json_http_error_keeps_status_without_response_body():
    adapter = TelegramAdapter(SECRET, "synthetic-owner")
    adapter._session = response(ValueError(SECRET), status=503)
    with pytest.raises(TelegramError) as caught:
        await adapter._default_request("getUpdates", {})
    assert caught.value.code == 503 and caught.value.category == "server_error"
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("retry_after", [True, -1, 0, "30", float("nan"), 2**63])
async def test_invalid_retry_delay_is_not_trusted(retry_after):
    adapter = TelegramAdapter(SECRET, "synthetic-owner")
    adapter._session = response({"ok": False, "error_code": 429, "parameters": {"retry_after": retry_after}})
    with pytest.raises(TelegramError) as caught:
        await adapter._default_request("getUpdates", {})
    assert caught.value.retry_after is None


async def test_rate_limit_waits_at_least_server_delay_and_retains_recovery_evidence(monkeypatch, caplog):
    import src.im.telegram as tg
    clock = [100.0]
    monkeypatch.setattr(tg, "_now", lambda: clock[0])
    sleeps, snapshots, calls = [], [], []
    async def sleep(delay):
        sleeps.append(delay)
        snapshots.append(adapter.liveness())
        clock[0] += delay
    monkeypatch.setattr(tg.asyncio, "sleep", sleep)
    async def request(method, payload):
        calls.append(payload["offset"])
        if len(calls) == 1:
            raise TelegramError(SECRET, code=429, retry_after=87)
        return [{"update_id": 5, "message": {"from": {"id": 1}, "text": "synthetic message"}}]
    adapter = TelegramAdapter(SECRET, "1", request_fn=request)
    monkeypatch.setattr(adapter, "claim_polling", lambda: None)
    stream = adapter.poll()
    event = await anext(stream)
    await stream.aclose()
    assert event.text == "synthetic message" and sleeps == [87] and calls == [0, 0]
    failed = snapshots[0]
    assert not failed["connected"] and failed["polling"]["retry_in"] == 87
    state = adapter.liveness()
    assert state["connected"] and state["polling"]["failures"] == 1
    assert state["polling"]["consecutive_failures"] == 0
    assert state["polling"]["last_error_category"] == "rate_limited"
    assert state["polling"]["last_recovery_age"] == 0
    assert SECRET not in json.dumps(state) and SECRET not in caplog.text


async def test_exponential_backoff_remains_bounded_and_cancellation_is_not_a_failure(monkeypatch):
    import src.im.telegram as tg
    delays = []
    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 8:
            raise asyncio.CancelledError
    monkeypatch.setattr(tg.asyncio, "sleep", sleep)
    async def broken(*args):
        raise RuntimeError(SECRET)
    adapter = TelegramAdapter(SECRET, "1", request_fn=broken)
    monkeypatch.setattr(adapter, "claim_polling", lambda: None)
    with pytest.raises(asyncio.CancelledError):
        await anext(adapter.poll())
    assert delays == [1, 2, 4, 8, 16, 30, 30, 30]
    assert adapter.liveness()["polling"]["failures"] == 8
    async def cancelled(*args):
        raise asyncio.CancelledError
    fresh = TelegramAdapter(SECRET, "1", request_fn=cancelled)
    monkeypatch.setattr(fresh, "claim_polling", lambda: None)
    with pytest.raises(asyncio.CancelledError):
        await anext(fresh.poll())
    assert fresh.liveness()["polling"]["failures"] == 0


async def test_empty_poll_proves_recovery_but_successful_send_does_not(monkeypatch):
    import src.im.telegram as tg
    polls = 0
    async def request(method, payload):
        nonlocal polls
        if method == "sendMessage":
            return {"message_id": 1}
        polls += 1
        if polls == 1:
            raise TelegramError(SECRET, category="timeout")
        if polls == 2:
            return []
        raise asyncio.CancelledError
    async def sleep(delay):
        await adapter.send_text("synthetic notification")
        assert not adapter.liveness()["connected"]
        assert adapter.liveness()["polling"]["consecutive_failures"] == 1
    adapter = TelegramAdapter(SECRET, "1", request_fn=request)
    monkeypatch.setattr(adapter, "claim_polling", lambda: None)
    monkeypatch.setattr(tg.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        await anext(adapter.poll())
    state = adapter.liveness()
    assert state["connected"] and state["last_frame_age"] is not None
    assert state["polling"]["consecutive_failures"] == 0
    assert state["polling"]["last_recovery_age"] is not None
    assert state["polling"]["retry_in"] == 0


@pytest.mark.parametrize("bad", [None, "not-an-update", {"update_id": "bad"}, {"update_id": True},
                                 {"update_id": -1}, {"update_id": 1, "message": ["malformed"]}])
async def test_one_malformed_update_does_not_end_the_receiver(tmp_path, monkeypatch, bad):
    async def request(*args):
        return [bad, {"update_id": 6, "message": {"from": {"id": 1}, "text": "valid"}}]
    adapter = TelegramAdapter(SECRET, "1", request_fn=request, inbox_dir=str(tmp_path))
    monkeypatch.setattr(adapter, "claim_polling", lambda: None)
    stream = adapter.poll()
    assert (await anext(stream)).text == "valid"
    await stream.aclose()
    assert adapter._offset == 7 and adapter.liveness()["polling"]["malformed_updates"] == 1


async def test_invalid_batch_is_retried_without_acknowledging_updates(monkeypatch):
    import src.im.telegram as tg
    calls, delays = [], []
    async def request(method, payload):
        calls.append(payload["offset"])
        return {"message": SECRET} if len(calls) == 1 else [{"update_id": 2, "message": {"from": {"id": 1}, "text": "valid"}}]
    async def sleep(delay):
        delays.append(delay)
    monkeypatch.setattr(tg.asyncio, "sleep", sleep)
    adapter = TelegramAdapter(SECRET, "1", request_fn=request)
    monkeypatch.setattr(adapter, "claim_polling", lambda: None)
    stream = adapter.poll()
    assert (await anext(stream)).text == "valid"
    await stream.aclose()
    assert calls == [0, 0] and delays == [1]
    assert adapter.liveness()["polling"]["last_error_category"] == "invalid_response"


def test_sampling_only_retains_validated_diagnostic_fields():
    from src.gateway.runtime_acceptance import probe, Target
    def fetch(url, *args):
        if url.endswith("quick"):
            return {"status": "healthy"}
        if url.endswith("sessions"):
            return {"sessions": []}
        return {"bridge": True, "connected": False, "polling": {
            "failures": 3, "consecutive_failures": 1, "last_error_category": "timeout", "last_error_code": 429,
            "last_failure_age": 2.5, "last_recovery_age": None, "retry_in": 4.0,
            "private": SECRET, "raw_error": SECRET}}
    sampled = probe(Target("assistant", "http://localhost", True), fetch=fetch, require_im=True)
    assert sampled["im"]["polling"]["last_error_category"] == "timeout"
    assert "im_disconnected" in sampled["problems"] and SECRET not in json.dumps(sampled)
    def malformed(url, *args):
        data = fetch(url)
        if "polling" in data:
            data["polling"] = {"failures": -1, "last_error_category": SECRET, "retry_in": float("inf"), "last_error_code": True}
        return data
    sampled = probe(Target("assistant", "http://localhost", True), fetch=malformed, require_im=True)
    assert sampled["im"].get("polling", {}) == {}
