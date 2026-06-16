"""流式 token 批量节流。

逐 token 广播会产生成百上千条 WebSocket 消息（洪泛）。TokenBatcher 累积 token，
达到阈值字符数或遇到换行才 flush 一次，末尾再 flush 余量，显著降低消息数而不丢内容。
"""

from typing import Awaitable, Callable, List


class TokenBatcher:
    def __init__(self, emit: Callable[[str], Awaitable], max_chars: int = 48):
        self._emit = emit          # async callable(text)
        self.max_chars = max_chars
        self._buf: List[str] = []
        self._len = 0

    async def feed(self, tok: str) -> None:
        if not tok:
            return
        self._buf.append(tok)
        self._len += len(tok)
        if self._len >= self.max_chars or "\n" in tok:
            await self.flush()

    async def flush(self) -> None:
        if not self._buf:
            return
        text = "".join(self._buf)
        self._buf = []
        self._len = 0
        await self._emit(text)
