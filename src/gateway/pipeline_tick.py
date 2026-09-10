"""定时推进：让流水线从"人盯着才动"变成"到点自己走，卡住了叫人"。

`advance` 是**一次推进一条**的动作；无人值守要的是另一件事——扫一遍所有活着的运行，各推一道，
然后把"该人管的"顶到人眼前。这两件事分开写，是因为它们的判据完全相反：

* `advance` 的退出码 1 = "需要人处理"，给终端里的人和脚本看；
* `tick` **等人批也退出 0**。通知已经从专门通道（台账 + WS + IM）发出去了，再让 cron 按非零
  退出码报一次红，人手机上会收到两条说同一件事的消息，而 run 台账里会多一条**不是失败的失败**。
  今天已经栽过两次"系统报的和实际发生的不一样"，不再自己造第三次。

## 只在状态**变化**时通报

半小时一次的 cron，一条挂了三天的等批会推 144 遍。所以每个运行记一个 `notified` 签名
（`awaiting_review:scout:<产出物>` / `failed:publish:3` / `done`…），签名没变就不再吭声。
签名认的是**产出物**不是工序名：驳回重做后再次等批，那是新的一次等批，该说。

## 无人值守就要真的是无人值守那一档

tick 是**按构造**无人值守的（cron 驱动，没人在旁边）。所以它必须：

* 用 `UNATTENDED_PROFILE` 起工序——能力集虽然与 external 相同，但**档名本身是审计证据**
  （capabilities.py 的注释写明了单独命名就是为了这个）。挂 external 的名跑 cron，
  事后翻台账根本看不出这一轮没人看着。
* **不给出网**。`pipeline.py` 的 StageDef.web 注释一直写着"cron 驱动的工序即使申报了也拿不到网"，
  但第一版 tick 传 `capabilities=None` 且照传 `with_web`，那句话对这条路径是**假的**。
  出网既是信息入口也是外传通道（web_fetch 的 GET query 就能带走东西），无人值守下尤其如此。

## 对外工序：不试，也不烧重试次数

无人值守没有确认通道，outbound 工序必然 fail-closed 失败。要是照常调 `advance`，三个夜里就把
`MAX_STAGE_ATTEMPTS` 烧光，等你早上想推的时候它已经永久卡死了——**明知做不到还去试三次，
把自己废掉**。所以这里先看一眼下一道工序：是对外动作又没有确认通道，就根本不动它，如实说
"这一步要你带授权来"。重试次数一次都不消耗。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from src.gateway.pipeline import (
    PipelineStore, advance, load_definition, next_stage,
)
from src.gateway.products import ProductStore

# 一次 tick 最多碰几个运行。每道工序都是一次真 LLM 回合，五个部门同时在跑时，
# 一次全推等于一次不受控的预算支出——宁可下一轮再补，也不要半夜一口气烧完。
MAX_RUNS_PER_TICK = 5

_TERMINAL = {"done", "abandoned"}


@dataclass
class TickOutcome:
    """一个运行在这次 tick 里的遭遇。`announced` 表示这轮是否真往外推了通知。"""
    run_id: str
    pipeline: str = ""
    status: str = ""
    ran: List[str] = field(default_factory=list)
    blocked_on: str = ""
    reason: str = ""
    announced: bool = False
    skipped: bool = False           # 本轮压根没调 advance（对外工序无授权 / 超出条数上限）


def _product_line(repo_root: str, product_id: str) -> str:
    """把产出物摘成一句给人看的话。读不到就说读不到——别让通知假装什么都好。"""
    if not product_id:
        return ""
    product = ProductStore(repo_root).load(product_id)
    if product is None:
        return f"（产出物 {product_id} 读不到）"
    mark = " ⚠外部来源" if product.tainted else ""
    return f"{product.summary or product.kind}{mark}"


def _message(repo_root: str, run, outcome: TickOutcome) -> str:
    """通知正文。**带上下一步怎么做**——只说"卡住了"的通知等于让人再查一遍。"""
    head = f"{run.pipeline} · {run.run_id}"
    if outcome.status == "awaiting_review":
        stage = run.stage(outcome.blocked_on)
        detail = _product_line(repo_root, stage.product_id if stage else "")
        # IM 回法放前面、终端命令放后面：这条通知最常见的读者是**手机上的你**，
        # 让人抄一串 prun-hex 到终端才能批，等于把闸门修在人够不着的地方。
        return (f"📋 {head}\n工序「{outcome.blocked_on}」已产出，等你批：{detail}\n"
                f"回 /ok 批 · /no <意见> 驳回 · /later 挂起\n"
                f"（终端：vc pipeline review {run.run_id} --approve）")
    if outcome.status == "needs_human":
        return (f"🌐 {head}\n工序「{outcome.blocked_on}」要出网，无人值守这一档不给网"
                f"（出网也是外传通道）。\n"
                f"回 /go 推进（IM 这条路有你在，能出网）\n"
                f"（终端：vc pipeline advance {run.run_id}）")
    if outcome.status == "needs_auth":
        return (f"🔒 {head}\n工序「{outcome.blocked_on}」是对外动作，无人值守不会替你发。\n"
                f"回 /go 推进（对外那一步会弹按钮让你点）\n"
                f"（终端：vc pipeline advance {run.run_id} --yes）")
    if outcome.status == "failed":
        return f"⛔ {head}\n卡在工序「{outcome.blocked_on}」：{outcome.reason or '工序失败'}"
    return f"✅ {head}\n全部工序完成" + (f"（本轮跑了 {'、'.join(outcome.ran)}）" if outcome.ran else "")


def _signature(outcome: TickOutcome, run) -> str:
    """这次的状态签名。签名相同 = 人已经知道了，别再说一遍。"""
    if outcome.status == "awaiting_review":
        stage = run.stage(outcome.blocked_on)
        # 认**产出物**而不是工序名：驳回重做后再次等批，产出物换了一版，那就是新的一次等批，
        # 该再说一遍。（第一版按"回到 running 时清空签名"做，结果同一次 tick 里
        # pending→awaiting_review 一步走完，那个中间态根本没有哪次 tick 看得见，
        # 于是重做完的第二次等批被当成旧消息吞了——人不知道该回来看。）
        return f"awaiting_review:{outcome.blocked_on}:{stage.product_id if stage else ''}"
    if outcome.status in {"needs_auth", "needs_human"}:
        return f"{outcome.status}:{outcome.blocked_on}"
    if outcome.status == "failed":
        stage = run.stage(outcome.blocked_on)
        # 带上第几次：试了一次没过和试满三次停下，是两条不同的消息。
        return f"failed:{outcome.blocked_on}:{stage.attempts if stage else 0}"
    if outcome.status == "done":
        return "done"
    return ""                        # running / 无事可说：不占签名


def _accepts_source(notify: Any) -> bool:
    """投递器吃不吃 source 关键字（与 cron._accepts_attribution 同款探测，不靠异常试探）。"""
    import inspect
    try:
        params = inspect.signature(notify).parameters
    except (TypeError, ValueError):
        return False
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return True
    return "source" in params


async def tick(
    repo_root: str,
    *,
    confirm: Optional[Callable] = None,
    notify: Optional[Callable] = None,
    execute: Optional[Callable] = None,
    max_stages: int = 1,
    limit: int = MAX_RUNS_PER_TICK,
    pipeline: str = "",
) -> List[TickOutcome]:
    """扫一遍活着的运行，各推一道工序，状态有变化就通报。

    `confirm` 是**唯一**的对外动作授权来源：为 None 时 outbound 工序不试也不烧重试次数
    （见模块头）。`execute` 只为测试注入；不给就按 `confirm` 造真执行器。
    `notify` 拿 `notices.make_notifier` 造的那个三路投递器；不给就只推进不通报。
    """
    store = PipelineStore(repo_root)
    live = [r for r in store.list(pipeline=pipeline) if r.status not in _TERMINAL]
    outcomes: List[TickOutcome] = []

    if execute is None:
        from src.agents.capabilities import UNATTENDED_PROFILE, SessionCapabilities
        from src.agents.pipeline_exec import build_stage_executor
        # 档名是审计证据：事后翻台账要看得出这一轮没人看着（见模块头）。
        execute = build_stage_executor(
            repo_root, confirm=confirm,
            capabilities=SessionCapabilities.for_profile(UNATTENDED_PROFILE, repo_root))

    for index, listed in enumerate(live):
        run = store.load(listed.run_id) or listed
        outcome = TickOutcome(run_id=run.run_id, pipeline=run.pipeline, status=run.status)

        if index >= max(0, int(limit)):
            # 超出上限的如实标出来，不要在日志里假装它被处理过了。
            outcome.skipped = True
            outcome.reason = "超出本轮条数上限，下一轮再推"
            outcomes.append(outcome)
            continue

        definition = load_definition(repo_root, run.pipeline)
        pending = next_stage(definition, run) if definition is not None else None
        stage_def = definition.stage(pending.id) if (definition and pending) else None

        if stage_def is not None and confirm is None and (stage_def.outbound or stage_def.web):
            # 两者同源：都是"这一步得有人在"。分开报，是因为人要做的事不一样——
            # 一个是点头放行，一个是换个有网的入口来推。
            outcome.status = "needs_auth" if stage_def.outbound else "needs_human"
            outcome.blocked_on = pending.id
            outcome.skipped = True
        else:
            result = await advance(repo_root, run.run_id, execute=execute,
                                   definition=definition, max_stages=max_stages)
            outcome.status = result.status
            outcome.ran = list(result.ran)
            outcome.blocked_on = result.blocked_on
            outcome.reason = result.reason
            run = store.load(run.run_id) or run

        signature = _signature(outcome, run)
        if signature != (run.notified or ""):
            if signature and notify is not None:
                text = _message(repo_root, run, outcome)
                # 归属写成 pipeline:<名字>：台账上"哪条流水线出的"要一眼看得出，与 cron 的
                # source="cron:<作业名>" 同口径（record_notice 注释里记过抹掉归属的教训）。
                if _accepts_source(notify):
                    await notify(text, source=f"pipeline:{run.pipeline}")
                else:
                    await notify(text)
                outcome.announced = True
            run.notified = signature
            store.save(run)
        outcomes.append(outcome)

    return outcomes
