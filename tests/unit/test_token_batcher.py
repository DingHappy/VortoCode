"""流式 token 批量节流 TokenBatcher 测试。"""

import pytest

from src.web.streaming import TokenBatcher


@pytest.mark.asyncio
async def test_batches_until_threshold():
    out = []

    async def emit(t):
        out.append(t)

    b = TokenBatcher(emit, max_chars=10)
    for tok in ["ab", "cd", "ef", "gh"]:      # 8 字符 < 10，未触发
        await b.feed(tok)
    assert out == []

    await b.feed("ij")                         # 达到 10 → flush
    assert out == ["abcdefghij"]

    await b.feed("xx")
    await b.flush()                            # 末尾余量
    assert out == ["abcdefghij", "xx"]


@pytest.mark.asyncio
async def test_flushes_on_newline():
    out = []

    async def emit(t):
        out.append(t)

    b = TokenBatcher(emit, max_chars=100)
    await b.feed("hello")
    await b.feed("\n")                         # 换行 → flush
    assert out == ["hello\n"]


@pytest.mark.asyncio
async def test_empty_feed_and_flush_noop():
    out = []

    async def emit(t):
        out.append(t)

    b = TokenBatcher(emit, max_chars=10)
    await b.feed("")        # 空 token 不入缓冲
    await b.flush()         # 空缓冲 flush 不发
    assert out == []
