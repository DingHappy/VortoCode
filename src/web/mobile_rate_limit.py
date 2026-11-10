"""单进程手机网关的有界来源限频；来源由服务器传输层提供。"""

from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
import math
from threading import Lock
from typing import Callable

WINDOW_SECONDS = 60
REQUEST_LIMIT = 120
FAILURE_LIMIT = 5
INITIAL_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 15 * 60
MAX_SOURCES = 4096
IDLE_SECONDS = MAX_BACKOFF_SECONDS + WINDOW_SECONDS


@dataclass
class _Source:
    last_seen: float
    requests: deque[float] = field(default_factory=deque)
    failures: deque[float] = field(default_factory=deque)
    backoff_seconds: int = 0
    blocked_until: float = 0


class MobileRateLimiter:
    """原子计入准入请求；认证失败单独退避，成功请求不冲掉失败记录。"""

    def __init__(self, *, clock: Callable[[], float]):
        self._clock = clock
        self._sources: OrderedDict[str, _Source] = OrderedDict()
        self._lock = Lock()

    def admit(self, source: str) -> int:
        """返回需等待的秒数；零表示已消费本次准入额度。"""
        with self._lock:
            now = self._clock()
            while self._sources:
                oldest = next(iter(self._sources))
                if now - self._sources[oldest].last_seen < IDLE_SECONDS:
                    break
                self._sources.pop(oldest)
            state = self._sources.get(source)
            if state is None:
                # 容量已满时不淘汰仍有效的限制，也不继续分配内存。
                if len(self._sources) >= MAX_SOURCES:
                    return WINDOW_SECONDS
                state = self._sources[source] = _Source(now)
            state.last_seen = now
            self._sources.move_to_end(source)
            if state.blocked_until > now:
                return max(1, math.ceil(state.blocked_until - now))
            while state.requests and state.requests[0] <= now - WINDOW_SECONDS:
                state.requests.popleft()
            if len(state.requests) >= REQUEST_LIMIT:
                return max(1, math.ceil(state.requests[0] + WINDOW_SECONDS - now))
            state.requests.append(now)
            return 0

    def authentication_failed(self, source: str) -> None:
        with self._lock:
            state = self._sources.get(source)
            if state is None:
                return
            now = self._clock()
            # 已在途的失败响应不能叠加延长同一次退避。
            if state.blocked_until > now:
                return
            while state.failures and state.failures[0] <= now - WINDOW_SECONDS:
                state.failures.popleft()
            state.failures.append(now)
            if len(state.failures) >= FAILURE_LIMIT:
                state.backoff_seconds = min(MAX_BACKOFF_SECONDS,
                                            max(INITIAL_BACKOFF_SECONDS, state.backoff_seconds * 2))
                state.blocked_until = now + state.backoff_seconds
                state.failures.clear()
