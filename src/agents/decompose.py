"""把一个大任务自动分解成"可并行的独立子任务"——给 dev_auto 喂进隔离 dev 流水线。

复用 orchestrator 既有的 `TaskAnalyzer`/`TaskDecomposer`（之前只服务批处理引擎，没接进交互式主
agent loop）：分析复杂度 → 分解成 SubTask（带依赖图）。这里只取**无依赖**的子任务作为可并行的
一批，其余（有依赖的）交回让上层提示"需后续处理"——因为 dev_parallel 的各块从同一 base 并发跑、
互不可见，依赖链不能简单并行。UI 无关、可注入假分解器测试。
"""

import re
from typing import Any, Dict, List, Optional


_VERIFY_ONLY = re.compile(
    r"(集成验证|最终确认|运行测试|跑一遍测试|执行测试|验证(改动|结果|是否)|"
    r"检查(是否生效|结果)|回归验证|确认无误|"
    r"^(run|execute)\s+(the\s+)?tests?$|^verify\b|^validate\b|^final\s+(check|verification))",
    re.I)


def is_verify_only(sub: Any) -> bool:
    """这个子任务是不是"只跑验证、不改文件"。

    流水线在**所有子任务完成后本来就会跑一遍集成验证**，分解器再规划一个就是重复劳动——
    而且它没有 diff，会被判 failed。真机 2026-08-03：一个「集成验证与最终确认」子任务
    烧掉 20 万 token（占全次 42 万的近一半），最后标成 failed，
    而它自己的结论写着「全部验证完成 ✅」。

    提示词里已经明令禁止（task_analyzer 的分解提示），这里是**兜底闸**：
    提示是软约束，模型不听时得有硬的。判据只看标题——描述里出现"验证"很正常
    （"改完要能通过测试"是合理的验收标准），标题就叫"运行测试"才是那种块。

    补测试的子任务**不算**（它产生新文件），只有"跑一遍看看"才算。
    """
    title = str(getattr(sub, "title", "") or "").strip()
    if not title:
        return False
    if re.search(r"(补|新增|添加|写|加)\s*(单元)?测试|add\s+tests?|write\s+tests?", title, re.I):
        return False                                   # 补测试是真活，有 diff
    return bool(_VERIFY_ONLY.search(title))


def describe_subtask(s: Any) -> str:
    """把一个 SubTask 渲染成给实现子 agent 的描述串（标题：描述。验收标准：…）。
    independent 与 deferred（依赖接力）共用，确保两路实现指令同构。"""
    title = (getattr(s, "title", "") or "").strip()
    desc = (getattr(s, "description", "") or "").strip()
    # 分解器对单句任务常把 title 与 description 设成同一句，直接拼就成了"X：X"——既污染给
    # 子 agent 的提示词，也让终报和 IM 推送里的每一行都翻倍（真机 2026-07-26 实测）。
    # 一方是另一方的前缀时只留信息更全的那个。
    if title and desc and (desc.startswith(title) or title.startswith(desc)):
        d = desc if len(desc) >= len(title) else title
    else:
        d = f"{title}：{desc}" if (title and desc) else (title or desc)
    ac = getattr(s, "acceptance_criteria", None) or []
    if ac:
        d += "。验收标准：" + "；".join(str(x) for x in ac)
    return d.strip()


def topo_order(subtasks: List[Any], satisfied_ids=()) -> List[Any]:
    """按依赖把子任务拓扑排序：每个排在其（本集合内）依赖之后。satisfied_ids 视为已满足。

    依赖无法满足的（环 / 依赖在集合外且未 satisfied）best-effort 附到末尾，照样尝试（自测会兜底）。
    给 dev_auto 的"依赖子任务逐个接力"定序——A 先于依赖它的 B。
    """
    done = set(satisfied_ids)
    remaining = list(subtasks)
    ordered: List[Any] = []
    progress = True
    while remaining and progress:
        progress = False
        still = []
        for s in remaining:
            deps = set(getattr(s, "dependencies", None) or [])
            if deps <= done:
                ordered.append(s)
                done.add(getattr(s, "id", None))
                progress = True
            else:
                still.append(s)
        remaining = still
    ordered.extend(remaining)          # 剩下的依赖兜不住 → 附末尾 best-effort
    return ordered


async def decompose_for_parallel(task: str, *, analyzer: Optional[Any] = None,
                                 decomposer: Optional[Any] = None,
                                 max_parallel: int = 5) -> Dict[str, Any]:
    """分解 task，返回 {descriptions, independent, deferred, total}。

    - descriptions：无依赖子任务拼成的实现描述串（含验收标准），最多 max_parallel 条 → 直接喂 dev_parallel。
    - independent / deferred：无依赖 / 有依赖的 SubTask 列表（deferred 不并行，交回上层提示）。
    - total：分解出的子任务总数。
    analyzer/decomposer 可注入（测试用）；默认用 orchestrator 的真实实现（带 key 走 LLM、否则规则版）。
    """
    from src.orchestrator.task_analyzer import TaskAnalyzer, TaskDecomposer
    analyzer = analyzer or TaskAnalyzer()
    decomposer = decomposer or TaskDecomposer()
    analysis = await analyzer.analyze(task)
    subs = await decomposer.decompose(task, analysis)

    # 兜底闸：滤掉"只跑验证不改文件"的子任务（见 is_verify_only）。放进去必然 failed，
    # 且真机上单个能烧掉 20 万 token。滤掉的记进返回值，让上层能如实说一句而不是悄悄吞掉。
    dropped = [s for s in subs if is_verify_only(s)]
    subs = [s for s in subs if s not in dropped]
    independent = [s for s in subs if not getattr(s, "dependencies", None)]
    deferred = [s for s in subs if getattr(s, "dependencies", None)]
    descriptions: List[str] = [d for s in independent[:max_parallel] if (d := describe_subtask(s))]
    return {"descriptions": descriptions, "independent": independent,
            "deferred": deferred, "total": len(subs),
            "dropped_verify_only": [str(getattr(s, "title", "") or "") for s in dropped]}
