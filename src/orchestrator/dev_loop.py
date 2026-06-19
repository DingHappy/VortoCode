"""迭代开发闭环：开发 → 测试 → 审查 →（失败则反馈修复）→ 再测。

这是 README 所述"第一版只闭环'开发→审核→修改→测试'"的实现：
每轮让 developer 在同一工作区实现/修复，tester 真实跑测试，reviewer 给裁决；
只要测试未过或审查 request_changes，就把失败信息与审查意见组织成反馈喂回 developer，
重复直到「测试通过 且 审查 approve」或达到 max_iterations。
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class IterationRecord(BaseModel):
    """单轮记录。"""
    iteration: int
    files: List[str] = Field(default_factory=list)
    tests_passed: bool = False
    test_summary: str = ""
    review_verdict: str = ""
    review_summary: str = ""
    # 本轮 developer/reviewer 的推理链（推理型模型才有）。跨迭代串起来即可审计的推理链。
    dev_reasoning: str = ""
    review_reasoning: str = ""


class DevLoopResult(BaseModel):
    """迭代开发结果。"""
    success: bool
    iterations: int
    workspace: str
    files: List[str] = Field(default_factory=list)
    history: List[IterationRecord] = Field(default_factory=list)
    reason: str = ""


def _compose_feedback(test_out: Optional[dict], review_out: Optional[dict]) -> str:
    """把测试失败与审查意见组织成给 developer 的修复反馈。"""
    parts: List[str] = []
    if isinstance(test_out, dict) and not test_out.get("passed"):
        detail = (test_out.get("output") or test_out.get("reason") or "")[-1500:]
        parts.append(
            f"【测试未通过】失败 {test_out.get('failed_count', '?')} / 错误 "
            f"{test_out.get('error_count', '?')}。输出片段：\n{detail}"
        )
    if isinstance(review_out, dict) and review_out.get("verdict") != "approve":
        findings = review_out.get("findings") or []
        fs = "\n".join(
            f"- [{f.get('severity', '?')}] {f.get('file', '')}: "
            f"{f.get('message', '')} → 建议：{f.get('suggestion', '')}"
            for f in findings[:10] if isinstance(f, dict)
        )
        parts.append(f"【审查要求修改】{review_out.get('summary', '')}\n{fs}")
    return "\n\n".join(parts)


class IterativeDevLoop:
    """开发→测试→审查→修复 的迭代闭环。

    developer / tester / reviewer 为任意实现了 async execute(task, context=...) 的 Agent。
    """

    def __init__(self, developer, tester, reviewer, max_iterations: int = 3):
        self.developer = developer
        self.tester = tester
        self.reviewer = reviewer
        self.max_iterations = max_iterations

    async def run(
        self,
        task: str,
        workspace: Optional[str] = None,
        spec: Optional[Dict[str, Any]] = None,
        architecture: Optional[Dict[str, Any]] = None,
        on_iteration: Optional[Callable[[IterationRecord], Any]] = None,
        on_token: Optional[Callable[[str], Any]] = None,
    ) -> DevLoopResult:
        from ..core.tracing import ensure_trace_id
        ensure_trace_id()

        ws = workspace or str(Path(".vortocode") / "workspaces" / f"devloop-{uuid.uuid4().hex[:8]}")
        Path(ws).mkdir(parents=True, exist_ok=True)

        ctx: Dict[str, Any] = {"task": task, "workspace": ws, "artifacts": {}}
        if on_token is not None:
            ctx["on_token"] = on_token   # developer 生成代码时按 token 流式回调
        if spec:
            ctx["spec"] = spec
            ctx["artifacts"]["product"] = spec
        if architecture:
            ctx["architecture"] = architecture
            ctx["artifacts"]["architect"] = architecture

        history: List[IterationRecord] = []
        all_files: List[str] = []
        feedback: Optional[str] = None

        from ..core.monitoring import metrics
        metrics.increment("devloop.runs")

        for i in range(1, self.max_iterations + 1):
            # 1. 开发（首轮实现，之后带反馈修复）
            dev_task = task if feedback is None else (
                f"{task}\n\n上一轮的测试/审查反馈如下，请据此修复代码（保持已通过的部分不变）：\n{feedback}"
            )
            dev_res = await self.developer.execute(dev_task, context=ctx)
            if dev_res.success and dev_res.output is not None:
                ctx["artifacts"]["developer"] = dev_res.output
            for f in (getattr(dev_res, "files_created", None) or []):
                if f not in all_files:
                    all_files.append(f)

            # 2. 测试（真实运行）
            test_res = await self.tester.execute(task, context=ctx)
            test_out = test_res.output if isinstance(test_res.output, dict) else {}
            tests_passed = bool(test_res.success)

            # 3. 审查
            review_res = await self.reviewer.execute(task, context=ctx)
            review_out = review_res.output if isinstance(review_res.output, dict) else {}
            verdict = review_out.get("verdict", "")
            review_ok = verdict == "approve"

            record = IterationRecord(
                iteration=i,
                files=list(all_files),
                tests_passed=tests_passed,
                test_summary=(test_out.get("reason") or
                              f"passed={test_out.get('passed_count', 0)} failed={test_out.get('failed_count', 0)}"),
                review_verdict=verdict,
                review_summary=review_out.get("summary", ""),
                dev_reasoning=getattr(dev_res, "reasoning", None) or "",
                review_reasoning=getattr(review_res, "reasoning", None) or "",
            )
            history.append(record)
            metrics.increment("devloop.iterations")
            logger.info("DevLoop 第 %d 轮: tests_passed=%s verdict=%s", i, tests_passed, verdict)

            # 可观测回调（如 Web 层广播本轮进度）
            if on_iteration is not None:
                res = on_iteration(record)
                if asyncio.iscoroutine(res):
                    await res

            if tests_passed and review_ok:
                return DevLoopResult(
                    success=True, iterations=i, workspace=ws,
                    files=all_files, history=history, reason="测试通过且审查通过",
                )

            # 4. 组织反馈，进入下一轮修复
            feedback = _compose_feedback(test_out, review_out)
            if not feedback:
                # 无具体反馈但仍未达标（如审查非 approve 却无 findings）：补一句通用反馈
                feedback = f"测试通过={tests_passed}，审查裁决={verdict or '未知'}，请完善实现使其通过测试与审查。"

        return DevLoopResult(
            success=False, iterations=self.max_iterations, workspace=ws,
            files=all_files, history=history,
            reason=f"达到最大迭代次数 {self.max_iterations} 仍未通过",
        )


async def run_iterative_development(
    task: str,
    workspace: Optional[str] = None,
    max_iterations: int = 3,
) -> DevLoopResult:
    """便捷入口：自动构建 developer/tester/reviewer 并跑迭代闭环。"""
    from ..agents.roles import DeveloperAgent, TesterAgent, ReviewerAgent
    loop = IterativeDevLoop(
        DeveloperAgent(), TesterAgent(), ReviewerAgent(), max_iterations=max_iterations
    )
    return await loop.run(task, workspace=workspace)


class AutonomousCodingResult(BaseModel):
    """长程编码自治结果。"""
    success: bool
    goal: str
    workspace: str
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    files: List[str] = Field(default_factory=list)
    reason: str = ""


async def _plan_coding_steps(planner, goal: str, max_steps: int) -> List[str]:
    """用通用 Agent 把目标拆成有序编码步骤；失败则降级为单步=目标。"""
    from ..agents.base import extract_json
    prompt = (
        f"把下面的编码目标拆成有序的实现步骤（最多 {max_steps} 个），"
        f"每步是一个可独立开发+测试的小任务。\n目标：{goal}\n"
        '返回 JSON 字符串数组，如 ["实现 X 的核心函数","为 X 写测试"]。只返回 JSON。'
    )
    try:
        res = await planner.execute(prompt)
        data = extract_json(getattr(res, "output", "") or "")
        if isinstance(data, list) and data:
            return [str(s) for s in data][:max_steps]
    except Exception as e:
        logger.warning("规划编码步骤失败，降级单步: %s", e)
    return [goal]


async def run_autonomous_coding(
    goal: str,
    workspace: Optional[str] = None,
    max_steps: int = 5,
    max_iterations_per_step: int = 2,
    planner=None,
    dev_loop_factory: Optional[Callable[[], "IterativeDevLoop"]] = None,
    on_step: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> AutonomousCodingResult:
    """长程编码自治：把目标拆成多步，逐步在同一工作区跑迭代开发闭环并聚合。

    planner / dev_loop_factory 可注入（便于离线测试）；默认用通用 LLMAgent 规划、
    真实 developer/tester/reviewer 跑闭环。
    """
    if planner is None:
        from ..agents.general import LLMAgent
        planner = LLMAgent()
    if dev_loop_factory is None:
        from ..agents.roles import DeveloperAgent, TesterAgent, ReviewerAgent

        def dev_loop_factory():
            return IterativeDevLoop(
                DeveloperAgent(), TesterAgent(), ReviewerAgent(),
                max_iterations=max_iterations_per_step,
            )

    ws = workspace or str(Path(".vortocode") / "workspaces" / f"coding-{uuid.uuid4().hex[:8]}")
    steps = await _plan_coding_steps(planner, goal, max_steps)

    step_results: List[Dict[str, Any]] = []
    all_files: List[str] = []
    for i, step in enumerate(steps, 1):
        loop = dev_loop_factory()
        r = await loop.run(step, workspace=ws)
        rec = {"step": i, "title": step, "success": r.success,
               "iterations": r.iterations, "files": r.files, "reason": r.reason}
        step_results.append(rec)
        for f in r.files:
            if f not in all_files:
                all_files.append(f)
        logger.info("长程编码 第%d/%d 步: success=%s (%s)", i, len(steps), r.success, step)
        if on_step is not None:
            res = on_step(rec)
            if asyncio.iscoroutine(res):
                await res

    success = bool(step_results) and all(s["success"] for s in step_results)
    return AutonomousCodingResult(
        success=success, goal=goal, workspace=ws, steps=step_results, files=all_files,
        reason="全部步骤通过" if success else "部分步骤未通过",
    )
