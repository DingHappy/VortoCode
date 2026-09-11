"""渠道数据查询——**确定性、零 LLM**，给 measure 提供真实的表现数据。

## 为什么不是"手动填数字"

手填能用，但每轮要人记得去查、去算、去打字，这种事第三天就没人做了。**你只需要给一次链接**，
数字由这里定期去查——发完文章顺手发一句 `/url <链接>`，之后的事不用你管。

## 最要紧的一条：查不到要说"查不到"，绝不能报 0

**0 次阅读和查不到是两件完全不同的事。** 把查不到当成 0 喂给 measure，它会得出"这个选题
失败了"，而那个结论会经 metrics 回流给下一轮 scout——假数据进了闭环会自我强化。
所以每条记录都带 `supported`，查不到的明确写清原因。

## 各渠道的真实情况（2026-09-10 实测，不是查文档）

    B站            公开接口给 view/like/reply/favorite      ✅ 真机验过
    掘金           content_api/article/detail 拿真实
                   article_id 也返回「内容为空」——要登录态    ❌
    微信公众号      页面能打开但**阅读数不在页面里**——
                   read_num 字段没有、连"阅读"两个字都没有      ❌
    知乎           403                                       ❌

不支持的不是"还没做"，是**公网确实查不到**。把它们写进来只会得到一串假零。

**只放真机验过的适配器。** 今天已经栽过一次：信号采集里写了个 anthropic 的 RSS 地址，
14 条单测全绿而那地址是 404——单测喂的是自己造的格式，验不了"这个接口真的能用"。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_TIMEOUT = 12
_RETRIES = 3
_UA = "Mozilla/5.0 (compatible; VortoCode-stats/1.0)"
MAX_URLS = 60                     # 台账上限；更早的轮次对"下一轮写什么"没有帮助
BUDGET_SECONDS = 90


@dataclass
class ChannelStat:
    url: str
    channel: str
    supported: bool
    title: str = ""
    metrics: Dict[str, int] = field(default_factory=dict)
    reason: str = ""              # supported=False 时**必须**说清为什么
    at: str = ""
    run_id: str = ""


def _fetch(url: str) -> bytes:
    from src.agents.web_fetch import _host_is_safe, refusal_reason
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    if not _host_is_safe(host):
        raise RuntimeError(f"拒绝抓取：{refusal_reason(host) or host}")
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    last: Exception = RuntimeError("未尝试")
    for attempt in range(_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 —— 已过闸
                return resp.read(2_000_000)
        except urllib.error.HTTPError:
            raise                                   # 对方明确答了：别重试
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if attempt + 1 < _RETRIES:
                time.sleep(1.0 * (attempt + 1))
    raise last


# ---------------------------------------------------------------- 各渠道适配器
_BV = re.compile(r"(BV[0-9A-Za-z]{10})")
_JUEJIN = re.compile(r"juejin\.cn/post/(\d+)")


def _bilibili(url: str, fetch: Callable[[str], bytes]) -> Optional[ChannelStat]:
    m = _BV.search(url)
    if not m:
        return None
    data = json.loads(fetch(
        f"https://api.bilibili.com/x/web-interface/view?bvid={m.group(1)}").decode("utf-8", "replace"))
    if data.get("code") != 0:
        return ChannelStat(url=url, channel="bilibili", supported=False,
                           reason=f"接口返回 code={data.get('code')}：{data.get('message')}")
    body = data.get("data") or {}
    stat = body.get("stat") or {}
    return ChannelStat(url=url, channel="bilibili", supported=True,
                       title=str(body.get("title") or ""),
                       metrics={k: int(stat.get(k) or 0)
                                for k in ("view", "like", "reply", "favorite", "coin", "share")})


# 公网确实查不到的渠道。**写清为什么**——"不支持"和"还没做"在排查时是两回事。
_NO_PUBLIC_DATA = {
    "mp.weixin.qq.com": "微信公众号的阅读数不在页面里，必须在微信客户端带登录态才拿得到",
    "juejin.cn": "掘金要登录态：content_api/v1/article/detail 拿真实 article_id 也返回"
                 "「内容为空」（2026-09-10 实测）",
    "zhihu.com": "知乎要登录态：对非登录请求返回 403",
    "xiaohongshu.com": "小红书没有公开数据接口",
}

# **只放真机验过的适配器。** 今天已经栽过一次（信号采集里写了个 anthropic RSS 地址，
# 14 条单测全绿而那地址是 404）——单测喂的是我自己造的格式，验不了"这个接口真的能用"。
ADAPTERS: List[Callable[[str, Callable[[str], bytes]], Optional[ChannelStat]]] = [
    _bilibili,
]


def _known_unsupported(url: str) -> Optional[ChannelStat]:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    # 逐级往上找：`zhuanlan.zhihu.com` 要能命中 `zhihu.com`，否则每加一个子域就得补一条，
    # 而漏掉的那个会得到一句"没有对应的查询适配器"——听起来像还没做，其实是根本查不到。
    parts = host.split(".")
    for i in range(len(parts) - 1):
        reason = _NO_PUBLIC_DATA.get(".".join(parts[i:]))
        if reason is not None:
            return ChannelStat(url=url, channel=host, supported=False, reason=reason)
    return None


def fetch_one(url: str, fetch: Callable[[str], bytes]) -> ChannelStat:
    """查一条。**任何情况下都返回一条记录**——查不到也要留痕，而不是从结果里消失。"""
    known = _known_unsupported(url)
    if known is not None:
        return known
    for adapter in ADAPTERS:
        try:
            hit = adapter(url, fetch)
        except Exception as e:  # noqa: BLE001 —— 单条失败不影响其它条，但要说清死因
            return ChannelStat(url=url, channel="?", supported=False,
                               reason=f"{type(e).__name__}: {str(e)[:80]}")
        if hit is not None:
            return hit
    from urllib.parse import urlparse
    return ChannelStat(url=url, channel=(urlparse(url).hostname or "?"), supported=False,
                       reason="没有对应的查询适配器（这个渠道暂时查不到数据）")


# ---------------------------------------------------------------- 已发布链接台账
def _registry_path(repo_root: str) -> Path:
    return Path(repo_root) / ".vortocode" / "published_urls.json"


def load_urls(repo_root: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(_registry_path(repo_root).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def register_url(repo_root: str, url: str, *, run_id: str = "", note: str = "") -> bool:
    """登记一条已发布的链接。**你只需要给一次链接**，数字之后自动查。"""
    url = str(url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return False
    items = [it for it in load_urls(repo_root) if it.get("url") != url]   # 同一条只留最新
    items.append({"url": url, "run_id": run_id, "note": note,
                  "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    path = _registry_path(repo_root)
    try:
        from src.agents.dev_plan import ensure_state_gitignore
        ensure_state_gitignore(repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items[-MAX_URLS:], ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(path)
        return True
    except (OSError, TypeError, ValueError):
        return False


def collect_stats(repo_root: str, *, fetch: Optional[Callable[[str], bytes]] = None,
                  budget: float = BUDGET_SECONDS) -> List[ChannelStat]:
    """把台账里的链接逐条查一遍。超预算的**点名**，不静默丢。"""
    get = fetch or _fetch
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    started = time.monotonic()
    out: List[ChannelStat] = []
    for item in load_urls(repo_root):
        url = str(item.get("url") or "")
        if not url:
            continue
        if time.monotonic() - started > budget:
            out.append(ChannelStat(url=url, channel="?", supported=False,
                                   reason=f"跳过（本轮已用满 {budget:.0f}s 时间预算）",
                                   at=stamp, run_id=str(item.get("run_id") or "")))
            continue
        stat = fetch_one(url, get)
        stat.at = stamp
        stat.run_id = str(item.get("run_id") or "")
        out.append(stat)
    return out


def to_payload(stats: List[ChannelStat]) -> Dict[str, Any]:
    ok = [s for s in stats if s.supported]
    return {
        "stats": [asdict(s) for s in stats],
        "queried": len(ok),
        "unqueryable": [{"url": s.url, "reason": s.reason} for s in stats if not s.supported],
        # 写给 measure 看的一句话。**别让它把"查不到"读成"表现差"。**
        "caveat": ("其中查不到数据的条目见 unqueryable——**那和「0 次阅读」是两件事**，"
                   "不要把它们当成表现差的证据。"),
    }


def summarize(stats: List[ChannelStat]) -> str:
    ok = sum(1 for s in stats if s.supported)
    bad = len(stats) - ok
    if not stats:
        return "台账里还没有已发布的链接（用 /url <链接> 登记）"
    return f"查到 {ok} 条" + (f"· {bad} 条查不到" if bad else "")
