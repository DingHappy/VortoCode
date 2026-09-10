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
