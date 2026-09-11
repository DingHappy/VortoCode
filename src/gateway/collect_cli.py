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
