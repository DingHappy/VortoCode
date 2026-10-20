"""请求根本没发出去（连接阶段失败）就自动重试一次；发出去之后的超时 / 断连照旧不重试，免得重复计费。全部离线。"""
from __future__ import annotations

import httpx
import openai
import pytest

from src.llm import client as llm_client


def _request():
    return httpx.Request("POST", "https://relay.invalid/v1/chat/completions")


def _connect_failed():
    try:
        raise httpx.ConnectError("connection refused")
    except httpx.ConnectError as cause:
        error = openai.APIConnectionError(request=_request())
        error.__cause__ = cause
        return error


def _read_timed_out():
    try:
        raise httpx.ReadTimeout("read timed out")
    except httpx.ReadTimeout as cause:
        error = openai.APITimeoutError(request=_request())
        error.__cause__ = cause
        return error


class FakeCompletions:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    async def create(self, **_kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, completions):
        self.chat = type("Chat", (), {"completions": completions})()


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(llm_client, "UNSENT_RETRY_DELAY", 0)


async def test_unsent_request_is_retried_once():
    completions = FakeCompletions(_connect_failed(), "ok")
    assert await llm_client._create(FakeClient(completions), model="m") == "ok" and completions.calls == 2


async def test_only_one_retry_even_if_the_connection_keeps_failing():
    completions = FakeCompletions(_connect_failed(), _connect_failed(), "never")
    with pytest.raises(openai.APIConnectionError):
        await llm_client._create(FakeClient(completions), model="m")
    assert completions.calls == 2


@pytest.mark.parametrize("error", [_read_timed_out(), RuntimeError("500 upstream")])
async def test_sent_requests_are_not_retried(error):
    completions = FakeCompletions(error, "should not be used")
    with pytest.raises(type(error)):
        await llm_client._create(FakeClient(completions), model="m")
    assert completions.calls == 1


def test_connect_timeout_counts_as_unsent_but_read_errors_do_not():
    assert llm_client._never_sent(httpx.ConnectTimeout("t"))
    assert not llm_client._never_sent(httpx.RemoteProtocolError("server disconnected"))
    assert not llm_client._never_sent(_read_timed_out())
