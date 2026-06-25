"""LLM 用量观测 + 客户端韧性（src/llm/client.py）单元测试。"""

import asyncio

import pytest

from src.llm.client import _account, add_usage, estimate_tokens, get_usage, reset_usage


def test_estimate_tokens_cjk_vs_ascii():
    assert estimate_tokens("") == 0
    assert estimate_tokens("你好世界") == 4              # CJK ~1 token/字
    assert estimate_tokens("hello world " * 4) == 12      # ASCII ~1 token/4 字符
    assert estimate_tokens("a") == 1                      # 非空至少 1


def test_add_get_reset_usage():
    reset_usage()
    add_usage(100, 50)
    add_usage(10, 5)
    assert get_usage() == {"calls": 2, "prompt_tokens": 110,
                           "completion_tokens": 55, "total_tokens": 165}
    reset_usage()
    assert get_usage() == {"calls": 0, "prompt_tokens": 0,
                           "completion_tokens": 0, "total_tokens": 0}


def test_account_prefers_exact_usage():
    reset_usage()
    _account([{"content": "嗨"}], "回复内容", {"prompt_tokens": 7, "completion_tokens": 3})
    u = get_usage()
    assert u["total_tokens"] == 10 and u["calls"] == 1     # 用了 API 精确值


def test_account_estimates_when_no_usage():
    reset_usage()
    _account([{"content": "你好世界"}], "你好", None)        # 估算：prompt=4, completion=2
    u = get_usage()
    assert u["prompt_tokens"] == 4 and u["completion_tokens"] == 2 and u["calls"] == 1


# ---- 客户端韧性：可调重试 + aiohttp 降级路径的退避重试 ----

def test_max_retries_from_env(monkeypatch):
    from src.llm.client import LLMConfig, _int_env
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "7")
    assert LLMConfig().max_retries == 7
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "garbage")
    assert LLMConfig().max_retries == 3                        # 坏值 → 默认 3
    monkeypatch.delenv("OPENAI_MAX_RETRIES")
    assert LLMConfig().max_retries == 3                        # 缺省 → 默认 3
    assert _int_env("DEFINITELY_UNSET_VAR", 9) == 9


class _FakeResp:
    """伪 aiohttp 响应；既当 session.post(...) 返回的 async-cm，又当 response 本体。"""

    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self):
        return self._body

    async def text(self):
        return str(self._body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _RaisingCM:
    """session.post(...) 的 async-cm，进入时抛异常（模拟连接/超时）。"""

    def __init__(self, exc):
        self.exc = exc

    async def __aenter__(self):
        raise self.exc

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    """伪 aiohttp.ClientSession：按序吐预设响应/异常，记录 post 次数。"""

    def __init__(self, items):
        self._items = list(items)
        self.posts = 0

    def post(self, url, json=None, headers=None):
        item = self._items[min(self.posts, len(self._items) - 1)]
        self.posts += 1
        return _RaisingCM(item) if isinstance(item, BaseException) else item

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _client(max_retries):
    from src.llm.client import LLMClient, LLMConfig
    return LLMClient(LLMConfig(api_key="x", max_retries=max_retries, retry_base_delay=0))


@pytest.mark.asyncio
async def test_fallback_retries_transient_then_succeeds(monkeypatch):
    import aiohttp
    reset_usage()
    ok = {"choices": [{"message": {"content": "hi"}}],
          "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "model": "m"}
    session = _FakeSession([_FakeResp(503, "bad gw"), _FakeResp(502, "bad gw"), _FakeResp(200, ok)])
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: session)
    out = await _client(3)._chat_with_requests([{"content": "yo"}])
    assert out["content"] == "hi"
    assert session.posts == 3                                  # 两次暂时性 + 第三次成功


@pytest.mark.asyncio
async def test_fallback_permanent_error_no_retry(monkeypatch):
    import aiohttp
    session = _FakeSession([_FakeResp(401, "unauthorized")])
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: session)
    with pytest.raises(Exception) as ei:
        await _client(3)._chat_with_requests([{"content": "yo"}])
    assert "401" in str(ei.value)
    assert session.posts == 1                                  # 永久性 → 一次就抛、不重试


@pytest.mark.asyncio
async def test_fallback_exhausts_retries_then_raises(monkeypatch):
    import aiohttp
    session = _FakeSession([_FakeResp(502, "bad")])            # 永远 502
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: session)
    with pytest.raises(Exception) as ei:
        await _client(2)._chat_with_requests([{"content": "yo"}])
    assert "502" in str(ei.value)
    assert session.posts == 3                                  # 1 初始 + 2 重试


@pytest.mark.asyncio
async def test_fallback_retries_on_timeout(monkeypatch):
    import aiohttp
    ok = {"choices": [{"message": {"content": "ok"}}], "usage": {}, "model": "m"}
    session = _FakeSession([asyncio.TimeoutError(), _FakeResp(200, ok)])
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: session)
    out = await _client(2)._chat_with_requests([{"content": "yo"}])
    assert out["content"] == "ok"
    assert session.posts == 2                                  # 超时也算暂时性 → 重试后成功
