"""每天的模型用量台账：按来源（对话、早晚报、Codex 判断……）累计调用次数和 token。

只记数字，不记提示词或回复。早报据此附一行「昨天用了多少」，巡检据此在某天用量暴涨时提醒一次，
免得哪个后台任务失控、悄悄把额度烧光。

文件在 `~/.vortocode/usage/daily.json`（`VORTOCODE_USAGE_DIR` 可改），同机多个进程共用，写时加文件锁；
只留最近 60 天。记账失败不影响模型调用。
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterator, Optional

logger = logging.getLogger(__name__)

DEFAULT_SOURCE = "对话"
KEEP_DAYS = 60
BASELINE_DAYS = 7
RUNAWAY_FLOOR = 2_000_000        # 一天低于这么多 token 不算失控，免得平时很闲的日子稍多一点就报
RUNAWAY_FACTOR = 3               # 比前 7 天日均多这么多倍才算

_source: "contextvars.ContextVar[str]" = contextvars.ContextVar("vc_usage_source", default="")


@contextmanager
def usage_source(label: str) -> Iterator[None]:
    """这段代码里的模型调用记到 label 名下（没标的算「对话」）。"""
    token = _source.set(label)
    try:
        yield
    finally:
        _source.reset(token)


def _path() -> Path:
    return Path(os.getenv("VORTOCODE_USAGE_DIR") or Path.home() / ".vortocode" / "usage") / "daily.json"


def _read(path: Path) -> Dict[str, Dict[str, Dict[str, int]]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record(prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0, *,
           source: Optional[str] = None, today: Optional[date] = None) -> None:
    day = (today or date.today()).isoformat()
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.parent / ".lock", "a", encoding="utf-8") as lock:
            try:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            except ImportError:      # 非 POSIX：不加锁，最多偶尔少记一笔
                pass
            data = _read(path)
            bucket = data.setdefault(day, {}).setdefault(source or _source.get() or DEFAULT_SOURCE,
                                                         {"calls": 0, "prompt": 0, "completion": 0, "cached": 0})
            bucket["calls"] = int(bucket.get("calls", 0)) + 1
            bucket["prompt"] = int(bucket.get("prompt", 0)) + max(0, int(prompt_tokens or 0))
            bucket["completion"] = int(bucket.get("completion", 0)) + max(0, int(completion_tokens or 0))
            bucket["cached"] = int(bucket.get("cached", 0)) + max(0, int(cached_tokens or 0))
            cutoff = (date.fromisoformat(day) - timedelta(days=KEEP_DAYS)).isoformat()
            data = {key: value for key, value in data.items() if key >= cutoff}
            tmp = path.with_name(".daily.json.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, sort_keys=True)
            os.replace(tmp, path)
    except (OSError, ValueError, TypeError) as error:
        logger.debug("用量台账没记上：%s", type(error).__name__)


def day_usage(day: date) -> Dict[str, Dict[str, int]]:
    """某天各来源的用量。"""
    sources = _read(_path()).get(day.isoformat()) or {}
    return {name: bucket for name, bucket in sources.items() if isinstance(bucket, dict)}


def _tokens(bucket: Dict[str, int]) -> int:
    return int(bucket.get("prompt", 0) or 0) + int(bucket.get("completion", 0) or 0)


def _total(sources: Dict[str, Dict[str, int]]) -> int:
    return sum(_tokens(bucket) for bucket in sources.values())


def _amount(tokens: int) -> str:
    if tokens >= 10_000:
        return f"{tokens / 10_000:.1f} 万".replace(".0 万", " 万")
    return f"{tokens:,}"


def _baseline(day: date) -> Optional[float]:
    """前 7 天里有用量的那些天的日均；不到 3 天的数据就不比。"""
    data = _read(_path())
    totals = [_total(data.get((day - timedelta(days=offset)).isoformat()) or {})
              for offset in range(1, BASELINE_DAYS + 1)]
    used = [value for value in totals if value > 0]
    return sum(used) / len(used) if len(used) >= 3 else None


def summary_line(now: datetime) -> str:
    """早报附的一行：昨天各来源调用次数和 token，按 token 从多到少。没用过就不写。"""
    yesterday = now.date() - timedelta(days=1)
    sources = day_usage(yesterday)
    total = _total(sources)
    if not sources:
        return ""
    calls = sum(int(bucket.get("calls", 0) or 0) for bucket in sources.values())
    parts = [f"{name} {int(bucket.get('calls', 0) or 0)} 次 · {_amount(_tokens(bucket))}"
             for name, bucket in sorted(sources.items(), key=lambda item: _tokens(item[1]), reverse=True)[:4]]
    line = f"🔢 昨天模型用量：{calls} 次，{_amount(total)} token（{'；'.join(parts)}）"
    baseline = _baseline(yesterday)
    if baseline and total >= RUNAWAY_FACTOR * baseline:
        line += f"\n⚠️ 是前 7 天日均的 {total / baseline:.0f} 倍"
    return line


def runaway(now: datetime) -> str:
    """今天到现在的用量已经明显失控：返回一句提醒，否则空串。调用方负责一天只推一次。"""
    today = now.date()
    sources = day_usage(today)
    total = _total(sources)
    baseline = _baseline(today)
    if total < RUNAWAY_FLOOR or (baseline is not None and total < RUNAWAY_FACTOR * baseline):
        return ""
    top, bucket = max(sources.items(), key=lambda item: _tokens(item[1]))
    compared = f"，是前 7 天日均的 {total / baseline:.0f} 倍" if baseline else ""
    return (f"⚠️ 今天模型用量已到 {_amount(total)} token{compared}。"
            f"最多的是「{top}」：{int(bucket.get('calls', 0) or 0)} 次、{_amount(_tokens(bucket))}。"
            "如果不是你在大量使用，可能有后台任务在反复调用，去看一眼。")
