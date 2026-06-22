"""把一个大任务自动分解成"可并行的独立子任务"——给 dev_auto 喂进隔离 dev 流水线。

复用 orchestrator 既有的 `TaskAnalyzer`/`TaskDecomposer`（之前只服务批处理引擎，没接进交互式主
agent loop）：分析复杂度 → 分解成 SubTask（带依赖图）。这里只取**无依赖**的子任务作为可并行的
一批，其余（有依赖的）交回让上层提示"需后续处理"——因为 dev_parallel 的各块从同一 base 并发跑、
互不可见，依赖链不能简单并行。UI 无关、可注入假分解器测试。
"""

from typing import Any, Dict, List, Optional


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

    independent = [s for s in subs if not getattr(s, "dependencies", None)]
    deferred = [s for s in subs if getattr(s, "dependencies", None)]
    descriptions: List[str] = []
    for s in independent[:max_parallel]:
        title = (getattr(s, "title", "") or "").strip()
        desc = (getattr(s, "description", "") or "").strip()
        d = f"{title}：{desc}" if (title and desc) else (title or desc)
        ac = getattr(s, "acceptance_criteria", None) or []
        if ac:
            d += "。验收标准：" + "；".join(str(x) for x in ac)
        if d.strip():
            descriptions.append(d.strip())
    return {"descriptions": descriptions, "independent": independent,
            "deferred": deferred, "total": len(subs)}
