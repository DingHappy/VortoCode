"""LLM 超时语义（src/llm/client.py）—— 回归：长生成被"总时长"掐死。

真机（2026-09-17，Desktop 隔离 dev 流水线）：子 agent 写代码的一轮跑过 30 秒就被判
TimeoutError，改动明明已经写出来（diff 30–90 行）却一律判红丢弃，界面上显示"试了 2 次仍未过"。
根因是 `aiohttp.ClientTimeout(total=...)` —— total 包含模型生成全程，拿它当"读超时"用，
等于给长生成判死刑。真正对应"多久没动静"的是 sock_read。
"""

import pytest

from src.llm.client import LLMConfig, _aiohttp_timeout, _sdk_timeout


@pytest.fixture
def config(monkeypatch):
    monkeypatch.delenv("OPENAI_TIMEOUT", raising=False)
    monkeypatch.delenv("OPENAI_REQUEST_TIMEOUT", raising=False)
    return LLMConfig(api_key="k", timeout=30.0, request_timeout=900.0)


def test_idle_timeout_is_sock_read_not_total(config):
    """30 秒衡量的是"没动静"，不是"总共花了多久"。"""
    timeout = _aiohttp_timeout(config)
    assert timeout.sock_read == 30.0
    assert timeout.total == 900.0, "total 不能等于空闲超时，否则长生成必被掐断"


def test_connect_stays_short_even_when_idle_budget_is_large(config):
    config.timeout = 600.0
    assert _aiohttp_timeout(config).sock_connect == 15.0


def test_total_never_falls_below_the_idle_budget(config):
    """配置写反了（上限 < 空闲）也不能让 total 反过来成为更严的那个。"""
    config.timeout = 120.0
    config.request_timeout = 60.0
    assert _aiohttp_timeout(config).total == 120.0


def test_sdk_timeout_reads_idle_and_connects_fast(config):
    httpx = pytest.importorskip("httpx")
    timeout = _sdk_timeout(config)
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == 30.0
    assert timeout.connect == 15.0


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("OPENAI_TIMEOUT", "45")
    monkeypatch.setenv("OPENAI_REQUEST_TIMEOUT", "1200")
    config = LLMConfig(api_key="k")
    assert (config.timeout, config.request_timeout) == (45.0, 1200.0)
