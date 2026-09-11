"""`vc pipeline` —— 流水线的人类/脚本入口。

三个入口共用同一个动作（`pipeline.advance`），这里是其中之一：

* **定时** —— cron 的 `command:` 档直接跑 `vc pipeline advance <run>`。cron 作业本身是
  确定性零 LLM 的（与 relay_duty 同款），LLM 只在工序内部上场；
* **常驻** —— heartbeat 同理；
* **手动** —— 你在终端敲，或 IM/Desktop 后续接同一条命令。

因为 `advance` 幂等，这三条路重复调用互不干扰，也不需要各写一套。

**退出码**是给 cron 与脚本看的：0=推进了或本来就没事干，1=需要人处理（等人批/失败/配置有问题）。
"半夜推不动"和"半夜炸了"必须能被区分——否则台账里全是绿的，人永远不知道该去看哪一条。

`tick` 是**例外，且是故意的**：它等人批也返回 0，因为该说的话它已经从通知通道说过了
（见 `pipeline_tick` 模块头）。cron 再按非零退出码报一次红，人会收到两条同一件事的消息。
"""
from __future__ import annotations

import sys
from pathlib import Path

from src.gateway.pipeline import (
    PipelineStore, advance, load_definition, review,
)
from src.gateway.products import ProductStore, render_payload


def _repo() -> str:
    return str(Path.cwd())


def _print_run(run, *, verbose: bool = False) -> None:
    """终端里的细节视图。等人批那一道摊开内容，与 IM 的 `/pipe <run>` 同一份渲染——
    同一件事在两个入口长得不一样，人就得两边都看一遍。"""
    print(run.summary())
    if not verbose:
        return
    store = ProductStore(_repo())
    for stage in run.stages:
        line = f"  {stage.status:>16}  {stage.id}"
        if stage.attempts > 1:
            line += f"  （第 {stage.attempts} 次）"
        if stage.tokens:
            line += f"  {stage.tokens} tok"
        print(line)
        if stage.product_id:
            product = store.load(stage.product_id)
            if product is not None:
                mark = " ⚠外部来源" if product.tainted else ""
                print(f"{'':>18}  ↳ {product.kind} {product.id}{mark}：{product.summary}")
                if stage.status == "awaiting_review":
                    body = render_payload(product.payload)
                    if body:
                        print()
                        for ln in body.splitlines():
                            print(f"{'':>20}{ln}")
                        print()
        if stage.note:
            print(f"{'':>18}  ↳ {stage.note}")


async def run_pipeline_cli(
    action: str,
    name: str = "",
    *,
    verdict: str = "",
    comment: str = "",
    rollback_to: str = "",
    max_stages: int = 1,
    yes: bool = False,
    if_idle: bool = False,
) -> int:
    repo = _repo()
    store = PipelineStore(repo)

    if action == "list":
        runs = store.list()
        if not runs:
            print("（还没有流水线运行。`vc pipeline start <名字>` 开一轮。）")
            return 0
        for run in runs:
            print(run.summary())
        # 有等人批的就非零退出：cron 据此把"该你看了"顶到台账/IM 上，而不是静静绿着。
        return 1 if any(r.status == "awaiting_review" for r in runs) else 0

    if action == "show":
        run = store.load(name)
        if run is None:
            print(f"无此运行：{name}", file=sys.stderr)
            return 1
        _print_run(run, verbose=True)
        return 1 if run.status in {"awaiting_review", "failed"} else 0

    if action == "start":
        definition = load_definition(repo, name)
        if definition is None:
            print(f"读不到流水线定义 .vortocode/pipelines/{name}.yaml", file=sys.stderr)
            return 1
        if if_idle:
            # 定时开新一轮必须挡住堆积：昨天那轮还等你批，今天又开一轮，两轮抢同一个人的注意力，
            # 而 signals 去重会让第二轮拿到的素材更差。**有活着的就不开**，等你把上一轮处理完。
            live = [r for r in store.list(pipeline=name) if r.status not in {"done", "abandoned"}]
            if live:
                print(f"已有 {len(live)} 轮在跑（{live[0].run_id} · {live[0].status}），本次不开新的。")
                return 0
        run = store.start(definition)
        print(f"已开一轮：{run.run_id}（{len(run.stages)} 道工序）")
        print("下一步：vc pipeline advance " + run.run_id)
        return 0

    if action == "advance":
        run = store.load(name)
        if run is None:
            print(f"无此运行：{name}", file=sys.stderr)
            return 1
        from src.agents.pipeline_exec import build_stage_executor

        # 无 --yes 时不给确认通道：outbound 工序会被 fail-closed 拒绝，而不是在没人看着的
        # 终端里静默发出去。这与 headless agent 的既定取向一致。
        async def approve(_msg: str) -> bool:
            return True

        execute = build_stage_executor(
            repo, confirm=approve if yes else None,
            on_progress=lambda m: print(m, file=sys.stderr))
        result = await advance(repo, name, execute=execute, max_stages=max_stages)
        print(f"{result.status}"
              + (f" · 跑了 {'、'.join(result.ran)}" if result.ran else " · 本次没有可跑的工序")
              + (f" · 卡在 {result.blocked_on}" if result.blocked_on else "")
              + (f" · {result.reason}" if result.reason else ""))
        return 0 if result.status in {"running", "done"} else 1

    if action == "tick":
        # 无人值守入口：扫一遍活着的运行各推一道，状态有变化就走三路投递器通报。
        # 这里**不给 confirm**——半夜没人看着，对外动作一律不做（连试都不试，见 pipeline_tick）。
        from src.gateway.notices import make_notifier
        from src.gateway.pipeline_tick import tick

        outcomes = await tick(repo, notify=make_notifier(repo, trigger="scheduler"),
                              max_stages=max_stages, pipeline=name)
        if not outcomes:
            print("（没有活着的流水线运行。）")
            return 0
        for item in outcomes:
            print(f"{item.run_id} · {item.status}"
                  + (f" · 跑了 {'、'.join(item.ran)}" if item.ran else "")
                  + (f" · 卡在 {item.blocked_on}" if item.blocked_on else "")
                  + ("  → 已通报" if item.announced else "")
                  + (f" · {item.reason}" if item.reason else ""))
        return 0

    if action == "review":
        if verdict not in {"approve", "reject", "defer"}:
            print("review 需要 --approve / --reject / --defer 之一", file=sys.stderr)
            return 1
        result = review(repo, name, verdict=verdict, comment=comment,
                        rollback_to=rollback_to, reviewer="cli")
        print(f"{result.status} · {result.reason}")
        return 0 if result.status in {"running", "done"} else 1

    print(f"未知动作：{action}", file=sys.stderr)
    return 2
