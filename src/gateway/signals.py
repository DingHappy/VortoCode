"""信号采集——**确定性、零 LLM**，给 scout 提供"最近真的发生了什么"。

## 为什么要有它

scout 原先靠 `web_search` 搜"今日科技新闻"这类泛关键词。真机产出长这样：

    topic: MCP 生态爆发…   why_now: 2024 年底开源，2025 上半年社区井喷

**那天是 2026-09-10。** 给的"时效证据"全是一年前的事，而且它自己标了 unverified。
这不是模型不行，是结构问题：搜索返回的是**摘要**，不是**事件**——模型没有能力判断
"这条是不是当下热的"。泛关键词搜出来的还大量是内容农场的 SEO 文。

所以把"发现"和"解读"拆开：本模块确定性地抓事件，scout 只负责解读。

## 两条判断决定了这个模块长什么样

**① 热度看增量，不是绝对值。** 一个 50k star 的仓库不是新闻，**这周涨了 2k 的**才是；
HN 上一条 800 分的老帖不如一条四小时涨到 300 分的。所以要存快照、算 delta——
这是它比 web_search 强的**唯一理由**，没有 delta 就退化成另一种"抓一堆东西回来"。

**② 去重是硬要求。** 没有"昨天已经报过这条"的记忆，scout 每天看到同一批东西，
选题会反复撞车。

## 其他刻意的处置

- **一个源挂了不拖垮整轮**：其余照常交付，坏的那个点名（同入站附件那条的处置）。
- **中文源只用 V2EX**：技术信号是英文先行的，中文科技媒体滞后 1-3 天且大量是翻译。
  用英文源意味着比中文圈早两天，那本身就是差异化角度。
- 不碰微博热搜/知乎热榜：没有官方 API，抓取脆弱且 ToS 灰色，不值得引入一个隔三差五坏的依赖。
- URL 过 `web_fetch._host_is_safe`：源是配置来的不是模型选的，但纵深防御不差这一道。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_TIMEOUT = 15
_RETRIES = 3                      # 传输层抖动的重试次数（不含 HTTP 错误码）
_RETRY_BACKOFF = 1.5              # 退避基数（秒）；第 n 次等 n * BACKOFF
_MAX_BYTES = 3_000_000
_UA = "VortoCode-signals/1.0"

HN_TOP = 15                       # HN 取前 N 条。**每条一条请求**——30 条就是 31 次往返，
                                  # 在抖动的链路上（× 重试 × 超时）能把一轮拖到十几分钟。
                                  # 前 15 条已经覆盖了首屏，长尾对"什么在热"没有贡献。
MAX_SECONDS = 180                 # 整轮时间预算。采集是定时作业，**不能无限拖**：
                                  # 真机 2026-09-10 一轮跑了十几分钟（失败源各重试 3×15s，
                                  # 加 HN 的 31 次往返）。超预算就停下并如实说停在哪，
                                  # 而不是把后面的源静默丢掉——scout 得知道自己看到的是一部分。
GITHUB_MIN_STARS = 200            # 新仓库的最低 star 门槛
GITHUB_WINDOW_DAYS = 14           # "新"的定义
MAX_PER_SOURCE = 15               # 单源交付上限
DEDUP_DAYS = 7                    # 报过的条目多久内不再报
_STATE_MAX = 4000                 # 快照最多记几条（防无限增长）


@dataclass
class Signal:
    """一条信号。`delta` 是**本轮相对上轮的增量**，没有上轮记录时为 None（首次见到）。"""
    id: str
    source: str
    title: str
    url: str = ""
    score: int = 0
    delta: Optional[int] = None
    kind: str = "discussion"          # discussion | release | paper | post
    at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectResult:
    signals: List[Signal] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    skipped_seen: int = 0             # 因为报过而被去重掉的条数


# ---------------------------------------------------------------- 取数（可注入）
def _fetch(url: str) -> bytes:
    """GET 一个 URL。走 urllib，**默认吃 HTTP(S)_PROXY**（build_opener 自带 ProxyHandler）。

    **抖一次要重试。** 真机 2026-09-10：同一轮里 4 个源同时 `SSL handshake timed out`，
    十分钟前它们还都是好的——出海链路本身就是间歇性的（那台代理机的节点在劣化，
    CI runner 同期也被同一件事判了 Abandoned）。单次尝试的后果是**整个源今天没了**，
    而 scout 看到的"什么在热"就少了一整块，它还不知道自己少看了什么。

    只重试**传输层的抖动**（超时/连接错）。HTTP 4xx/5xx 不重试——那是对方明确回答了，
    重试三次得到的还是同一个答案，纯属浪费一次窗口。
    """
    from urllib.parse import urlparse

    from src.agents.web_fetch import _host_is_safe, refusal_reason

    host = urlparse(url).hostname or ""
    if not _host_is_safe(host):
        raise RuntimeError(f"拒绝抓取：{refusal_reason(host) or host}")
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    last: Exception = RuntimeError("未尝试")
    for attempt in range(_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 —— 已过 SSRF 闸
                return resp.read(_MAX_BYTES)
        except urllib.error.HTTPError:
            raise                                   # 对方明确答了：别重试
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if attempt + 1 < _RETRIES:
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
    raise last


def _json(fetch: Callable[[str], bytes], url: str) -> Any:
    return json.loads(fetch(url).decode("utf-8", errors="replace"))


# ---------------------------------------------------------------- 各源
def _hacker_news(fetch: Callable[[str], bytes]) -> List[Signal]:
    """开发者当下在讨论什么。score 就是热度，官方 Firebase 接口、免 key。"""
    ids = _json(fetch, "https://hacker-news.firebaseio.com/v0/topstories.json")[:HN_TOP]
    out: List[Signal] = []
    deadline = time.monotonic() + MAX_SECONDS / 2      # 单个源最多吃掉半个预算
    for hid in ids:
        if time.monotonic() > deadline:
            break                                       # 拿到多少算多少，别把整轮耗在一个源上
        try:
            item = _json(fetch, f"https://hacker-news.firebaseio.com/v0/item/{hid}.json") or {}
        except Exception:  # noqa: BLE001 —— 单条挂了跳过，别让一条坏数据废掉整个源
            continue
        if item.get("type") != "story" or not item.get("title"):
            continue
        out.append(Signal(
            id=f"hn:{hid}", source="hackernews", title=str(item["title"]),
            url=str(item.get("url") or f"https://news.ycombinator.com/item?id={hid}"),
            score=int(item.get("score") or 0), kind="discussion",
            at=_iso(item.get("time")),
            extra={"comments": int(item.get("descendants") or 0),
                   "hn_url": f"https://news.ycombinator.com/item?id={hid}"}))
    return out


def _github_new_repos(fetch: Callable[[str], bytes]) -> List[Signal]:
    """最近**新建**且已经攒到一定 star 的仓库——"什么东西真的发布了"。"""
    since = (datetime.now(timezone.utc) - timedelta(days=GITHUB_WINDOW_DAYS)).strftime("%Y-%m-%d")
    url = ("https://api.github.com/search/repositories?q="
           f"created:%3E{since}+stars:%3E{GITHUB_MIN_STARS}&sort=stars&order=desc&per_page=20")
    data = _json(fetch, url) or {}
    out: List[Signal] = []
    for repo in (data.get("items") or []):
        full = str(repo.get("full_name") or "")
        if not full:
            continue
        out.append(Signal(
            id=f"gh:{full}", source="github", title=f"{full} — {repo.get('description') or ''}".strip(" —"),
            url=str(repo.get("html_url") or ""), score=int(repo.get("stargazers_count") or 0),
            kind="release", at=str(repo.get("created_at") or ""),
            extra={"language": repo.get("language") or "", "created": repo.get("created_at") or ""}))
    return out


def _v2ex_hot(fetch: Callable[[str], bytes]) -> List[Signal]:
    """中文开发者在关心什么。官方接口、免 key。"""
    out: List[Signal] = []
    for t in (_json(fetch, "https://www.v2ex.com/api/topics/hot.json") or []):
        tid = t.get("id")
        if not tid or not t.get("title"):
            continue
        out.append(Signal(
            id=f"v2ex:{tid}", source="v2ex", title=str(t["title"]),
            url=str(t.get("url") or ""), score=int(t.get("replies") or 0),
            kind="discussion", at=_iso(t.get("created")),
            extra={"node": ((t.get("node") or {}).get("title") or "")}))
    return out


# 官方/一手源。**加之前先真机验一次** ——2026-09-10 第一版里写的
# `anthropic.com/news/rss.xml` 是 404（Anthropic 目前不提供 RSS），单测全绿也照样是错的：
# 单测喂的是我自己造的格式，验不了"这个地址真的存在"。逐源容错让它只是少一个源、不是崩一轮，
# 但少的那个源不会有人告诉你——所以宁可实测过再加。
_RSS_FEEDS = {
    "openai": "https://openai.com/news/rss.xml",
    "huggingface": "https://huggingface.co/blog/feed.xml",
    "lobsters": "https://lobste.rs/rss",
    "simonwillison": "https://simonwillison.net/atom/everything/",   # AI 工程实践，信噪比高
}
_ITEM = re.compile(r"<(?:item|entry)\b.*?</(?:item|entry)>", re.S | re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_LINK = re.compile(r"<link[^>]*href=[\"']([^\"']+)|<link[^>]*>(.*?)</link>", re.S | re.I)
_DATE = re.compile(r"<(?:pubDate|updated|published)[^>]*>(.*?)</", re.S | re.I)


def _rss(fetch: Callable[[str], bytes], name: str, url: str) -> List[Signal]:
    """官方博客/社区 RSS——**一手消息**，没有二手转述的扭曲。

    用 stdlib 正则而不是引入 feedparser：只要标题/链接/时间三样，为此加一个依赖不划算，
    而且解析失败的那个源本来就该被单独跳过（见 collect 的逐源容错）。
    """
    body = fetch(url).decode("utf-8", errors="replace")
    out: List[Signal] = []
    for raw in _ITEM.findall(body)[:MAX_PER_SOURCE]:
        title = _clean(_TITLE.search(raw).group(1)) if _TITLE.search(raw) else ""
        if not title:
            continue
        m = _LINK.search(raw)
        link = _clean((m.group(1) or m.group(2) or "")) if m else ""
        at = _clean(_DATE.search(raw).group(1)) if _DATE.search(raw) else ""
        out.append(Signal(id=f"{name}:{link or title}", source=name, title=title,
                          url=link, kind="post", at=at))
    return out


def _clean(text: str) -> str:
    import html as _html
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text or "", flags=re.S)
    return _html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _iso(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        return ""


SOURCES: Dict[str, Callable[[Callable[[str], bytes]], List[Signal]]] = {
    "hackernews": _hacker_news,
    "github": _github_new_repos,
    "v2ex": _v2ex_hot,
    **{name: (lambda f, n=name, u=url: _rss(f, n, u)) for name, url in _RSS_FEEDS.items()},
}


# ---------------------------------------------------------------- 快照（算 delta + 去重）
def _state_path(repo_root: str) -> Path:
    return Path(repo_root) / ".vortocode" / "signals_state.json"


def _load_state(repo_root: str) -> Dict[str, Any]:
    try:
        data = json.loads(_state_path(repo_root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(repo_root: str, state: Dict[str, Any]) -> None:
    path = _state_path(repo_root)
    try:
        from src.agents.dev_plan import ensure_state_gitignore
        ensure_state_gitignore(repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 只留最近的：快照会随时间无限涨，而老条目对 delta 和去重都没用了。
        items = sorted(state.items(), key=lambda kv: str(kv[1].get("seen") or ""), reverse=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(dict(items[:_STATE_MAX]), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except (OSError, TypeError, ValueError):
        pass


def collect(repo_root: str, *, fetch: Optional[Callable[[str], bytes]] = None,
            sources: Optional[Dict[str, Callable]] = None,
            now: Optional[datetime] = None,
            budget: float = MAX_SECONDS) -> CollectResult:
    """跑一轮采集：逐源抓 → 算增量 → 去掉报过的 → 落快照。**不产出 Product**（那是调用方的事）。"""
    get = fetch or _fetch
    table = sources if sources is not None else SOURCES
    stamp = (now or datetime.now(timezone.utc))
    state = _load_state(repo_root)
    result = CollectResult()

    started = time.monotonic()
    for name, source in table.items():
        if time.monotonic() - started > budget:
            # 没跑到的源**点名说出来**，别静默少一块。
            result.failures.append(f"{name}：跳过（本轮已用满 {budget:.0f}s 时间预算）")
            continue
        try:
            items = source(get)
        except Exception as e:  # noqa: BLE001 —— 一个源挂了不拖垮整轮，但要点名
            result.failures.append(f"{name}：{type(e).__name__} {str(e)[:80]}")
            continue
        for sig in items[:MAX_PER_SOURCE]:
            prev = state.get(sig.id) or {}
            if prev:
                sig.delta = sig.score - int(prev.get("score") or 0)
            seen_at = str(prev.get("reported") or "")
            state[sig.id] = {"score": sig.score, "seen": stamp.isoformat(timespec="seconds"),
                             "reported": seen_at}
            if seen_at and _within(seen_at, stamp, DEDUP_DAYS):
                result.skipped_seen += 1        # 报过了：不再占 scout 的注意力
                continue
            state[sig.id]["reported"] = stamp.isoformat(timespec="seconds")
            result.signals.append(sig)

    # 热的排前面：**有增量的按增量排，没有的按绝对值**——首次见到的条目不该因为
    # delta 为空就沉底，它可能正是今天刚冒出来的那个。
    result.signals.sort(key=lambda s: (s.delta if s.delta is not None else s.score), reverse=True)
    _save_state(repo_root, state)
    return result


def _within(iso: str, now: datetime, days: int) -> bool:
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return False
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then) < timedelta(days=days)


def to_payload(result: CollectResult) -> Dict[str, Any]:
    """渲染成产出物 payload。**失败的源要出现在 payload 里**——scout 得知道自己看到的是
    全部还是一部分，否则它会拿半份数据当成全景去判断"什么在热"。"""
    return {
        "signals": [asdict(s) for s in result.signals],
        "sources_failed": result.failures,
        "deduped": result.skipped_seen,
    }


def summarize(result: CollectResult) -> str:
    by_source: Dict[str, int] = {}
    for s in result.signals:
        by_source[s.source] = by_source.get(s.source, 0) + 1
    parts = [f"{len(result.signals)} 条新信号"]
    if by_source:
        parts.append("（" + " ".join(f"{k}={v}" for k, v in sorted(by_source.items())) + "）")
    if result.skipped_seen:
        parts.append(f"· 去重 {result.skipped_seen}")
    if result.failures:
        parts.append(f"· ⚠ {len(result.failures)} 个源失败")
    return " ".join(parts)
