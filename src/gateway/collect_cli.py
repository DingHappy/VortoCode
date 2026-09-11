"""`vc collect` —— 跑一轮信号采集，落成一条 `signals` 产出物。

**确定性作业、零 LLM**（与 relay_duty 同款）。cron 的 `command:` 档直接跑它；
LLM 只在下游的 scout 工序上场，负责解读而不负责发现。

产出物**不挂在任何一条流水线名下**：signals 谁都能读，硬塞进某条流水线的名下，
第二条流水线就读不到了（`resolve_inputs` 的第三级回退正为此而设）。

**退出码**：0 = 采到了（哪怕有个别源失败）；1 = 一条都没采到，或落盘失败。
个别源挂掉不该报红——那会让台账里全是红的，人反而不看了；失败的源名会进产出物，
scout 据此知道自己看到的是全部还是一部分。
"""
from __future__ import annotations

import sys
from pathlib import Path


def run_collect_cli(*, quiet: bool = False) -> int:
    from src.gateway.products import ProductStore
    from src.gateway.signals import collect, summarize, to_payload

    repo = str(Path.cwd())
    result = collect(repo)
    line = summarize(result)

    for fail in result.failures:
        print(f"⚠ 源失败：{fail}", file=sys.stderr)

    if not result.signals:
        print(f"没采到新信号（{line}）", file=sys.stderr)
        return 1

    product = ProductStore(repo).create(
        "signals", payload=to_payload(result), summary=line,
        # 采集是从公网抓的 → **打污点**。它会沿血缘一路传到发布口，
        # 这正是"发布必须人批"的来由；采来的标题里可能带指令性文本。
        tainted=True, taint_reason="来自公网信号采集（HN/GitHub/RSS/V2EX）")
    if not quiet:
        print(f"{line} → {product.id}")
        for s in result.signals[:8]:
            d = f" (+{s.delta})" if s.delta else ""
            print(f"  [{s.source}] {s.score}{d} · {s.title[:70]}")
    return 0


def run_stats_cli(*, quiet: bool = False) -> int:
    """`vc stats` —— 把已登记的发布链接查一遍，落成 channel_stats 产出物。

    **退出码 0 即使有查不到的**：微信公众号那类是公网确实查不到，不是故障；报红会让台账里
    全是红的、人反而不看了。查不到的条目会进产出物的 unqueryable，measure 据此知道
    "这条没数据"而不是"这条表现差"。
    """
    from src.gateway.channel_stats import collect_stats, summarize, to_payload
    from src.gateway.products import ProductStore

    repo = str(Path.cwd())
    stats = collect_stats(repo)
    line = summarize(stats)
    if not stats:
        print(line, file=sys.stderr)
        return 1

    product = ProductStore(repo).create(
        "channel_stats", payload=to_payload(stats), summary=line,
        tainted=True, taint_reason="来自公网渠道数据查询")
    if not quiet:
        print(f"{line} → {product.id}")
        for s in stats[:10]:
            if s.supported:
                print(f"  [{s.channel}] {s.metrics} · {s.title[:44]}")
            else:
                print(f"  [{s.channel}] 查不到：{s.reason[:60]}")
    return 0


def run_digest_cli(*, limit: int = 12) -> int:
    """`vc digest` —— 把最新一份 signals 渲染成给人读的早报。**零 LLM。**

    原来这活是个 `prompt:` 作业：让模型 `web_search` 搜"今日科技新闻"这类泛关键词，再挑 5 条
    写摘要。那正是 scout 今天治好的毛病——搜索返回的是**摘要**不是**事件**，泛关键词搜出来
    大量是内容农场的 SEO 文，模型分不清哪条是真的新。

    而 signals 里已经带着分数、增量、标题、链接。**按分数挑是确定性的**：比让模型转述一遍更准、
    零 token、而且编不了。LLM 该留给真正需要判断的地方（scout 选题、analyst 读数据），
    不是格式化。

    退出码：0=出了早报；1=还没有 signals（采集没跑过）。cron 据此知道该不该报红。
    """
    from src.gateway.products import ProductStore

    repo = str(Path.cwd())
    product = ProductStore(repo).latest("signals")
    if product is None:
        print("还没有 signals 产出物（先跑 vc collect）", file=sys.stderr)
        return 1
    print(render_digest(product, limit=limit))
    return 0


def render_digest(product, *, limit: int = 12) -> str:
    """把 signals 产出物渲染成早报。**按来源分组，组内按热度排。**

    热度口径与采集器一致：有增量的用增量（50k star 的仓库不是新闻，这周涨 2k 的才是），
    没有增量的用绝对值（首次见到的可能正是今天刚冒出来的）。
    """
    rows = list((product.payload or {}).get("signals") or [])
    failed = list((product.payload or {}).get("sources_failed") or [])
    if not rows:
        return "📰 今天没有新信号。" + (f"\n⚠ {len(failed)} 个源失败" if failed else "")

    by_source: dict = {}
    for row in rows:
        by_source.setdefault(str(row.get("source") or "?"), []).append(row)

    def heat(row):
        delta = row.get("delta")
        return delta if delta is not None else int(row.get("score") or 0)

    # **每个源公平分配名额**，不按分数排序抢位。真机 2026-09-11 的第一版早报：
    #     【v2ex】129 如何充值 chatgpt？救救孩子吧     ← 129 是回帖数
    #     【hackernews】18 Thelio Mira AI Workstation  ← 18 是投票分
    # 129 > 18 纯粹因为数字大、量纲不同，结果 v2ex 的闲聊吃掉了 10 个位置里的 5 个。
    # 源内比热度是有意义的（同一把尺子），跨源只能均分。
    lines = [f"📰 今日信号（{len(rows)} 条）"]
    share = max(1, limit // max(1, len(by_source)))
    budget = limit
    for source in sorted(by_source):
        if budget <= 0:
            break
        items = sorted(by_source[source], key=heat, reverse=True)[:min(share, budget)]
        if not items:
            continue
        lines.append(f"\n【{source}】")
        for row in items:
            budget -= 1
            delta = row.get("delta")
            # **增量和绝对值要能分辨**：+320 和 320 在"值不值得看"上是两回事。
            heat_text = f"+{delta}" if delta else str(row.get("score") or "")
            title = str(row.get("title") or "")[:78]
            lines.append(f"· {heat_text} {title}")
            url = str(row.get("url") or "")
            if url:
                lines.append(f"  {url}")
    if failed:
        # **失败的源要说出来**：少了一块而不说，人会以为今天就这么点事。
        lines.append(f"\n⚠ {len(failed)} 个源没取到：" + "；".join(f[:40] for f in failed[:3]))
    return "\n".join(lines)
