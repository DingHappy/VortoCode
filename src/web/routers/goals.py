"""Goal contract REST API.

The API deliberately separates execution from acceptance: starting a goal creates
a linked background task, while ``achieved`` can only be reached by recording
passing evidence for every acceptance criterion.
"""
from __future__ import annotations

import asyncio
import os
from functools import wraps
from pathlib import Path
from typing import Any, List

from fastapi import APIRouter, HTTPException

from src.workflow.workbench.goals import Goal, GoalLedger, evaluate_file_verifier, evidence_revision
from src.workflow.pipeline.pipeline_storage import PipelineBusy
from src.workflow.tasks.goal_submission import GoalSubmissionConflict, goal_contract, submit_goal

router = APIRouter()


def _storage_errors(method):
    @wraps(method)
    async def guarded(*args, **kwargs):
        try:
            return await method(*args, **kwargs)
        except OSError:
            raise HTTPException(status_code=503, detail="目标或任务状态未能保存；请先刷新核对现有记录，勿重复创建执行。") from None
        except PipelineBusy:
            raise HTTPException(status_code=409, detail="目标正在更新，请刷新后重试") from None
    return guarded


def _ledger() -> GoalLedger:
    return GoalLedger(os.getcwd())


def _list_field(value: Any) -> List[str]:
    if isinstance(value, str):
        return [line.strip().lstrip("- ").strip() for line in value.splitlines() if line.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _view(goal: Goal) -> dict:
    data = goal.to_dict()
    data["progress"] = {
        "passed": sum(1 for item in goal.acceptance_criteria if item.status == "passed"),
        "failed": sum(1 for item in goal.acceptance_criteria if item.status == "failed"),
        "total": len(goal.acceptance_criteria),
    }
    return data


def _require_goal(goal_id: str) -> Goal:
    goal = _ledger().load(goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail=f"无此目标 {goal_id}")
    return goal


async def _start_goal(goal: Goal, *, resume: bool = False, previous_task_id: str = ""):
    from src.web.routers.tasks import get_runner

    runner = get_runner()
    if Path(runner.repo_root).resolve() != Path(os.getcwd()).resolve():
        raise HTTPException(status_code=409, detail="目标与任务运行端不一致")
    try:
        return submit_goal(runner, goal.id, previous_task_id=previous_task_id, resume=resume,
                           expected_contract=goal_contract(goal))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"无此目标 {goal.id}") from None
    except GoalSubmissionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@router.post("/api/goals")
@_storage_errors
async def create_goal(body: dict):
    payload = body or {}
    objective = str(payload.get("objective") or "").strip()
    criteria = _list_field(payload.get("acceptance_criteria"))
    if not objective:
        raise HTTPException(status_code=400, detail="缺少 objective（目标）")
    if not criteria:
        raise HTTPException(status_code=400, detail="至少需要一条 acceptance_criteria（验收标准）")
    if payload.get("start") is True and not payload.get("request_id"):
        raise HTTPException(status_code=400, detail="创建并执行需要固定 request_id；也可先保存目标再确认执行")
    try:
        goal = _ledger().create(
            objective, criteria,
            constraints=_list_field(payload.get("constraints")),
            non_goals=_list_field(payload.get("non_goals")),
            request_id=payload.get("request_id"),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    task = None
    # 默认只保存草稿；执行必须由显式 start=true 或随后 /run 确认，避免一创建就擅自跑 LLM/代码。
    if payload.get("start", False) is True:
        if (goal.objective != objective or [item.text for item in goal.acceptance_criteria] != criteria
                or goal.constraints != _list_field(payload.get("constraints"))
                or goal.non_goals != _list_field(payload.get("non_goals"))
                or any(item.verifier is not None for item in goal.acceptance_criteria)):
            raise HTTPException(status_code=409, detail="目标合同已变化，请刷新目标后确认执行")
        task, _replayed = await _start_goal(goal)
        goal = _require_goal(goal.id)
    result = _view(goal)
    if task is not None:
        result["started_task_id"] = task.id
    return result


@router.get("/api/goals")
async def list_goals():
    return {"goals": [_view(goal) for goal in _ledger().list()]}


@router.get("/api/goals/{goal_id}")
async def get_goal(goal_id: str):
    return _view(_require_goal(goal_id))


@router.patch("/api/goals/{goal_id}")
@_storage_errors
async def update_goal(goal_id: str, body: dict):
    current = _require_goal(goal_id)
    payload = body or {}
    objective = str(payload.get("objective", current.objective)).strip()
    criteria = (
        _list_field(payload.get("acceptance_criteria"))
        if "acceptance_criteria" in payload
        else [item.text for item in current.acceptance_criteria]
    )
    constraints = (
        _list_field(payload.get("constraints"))
        if "constraints" in payload else current.constraints
    )
    non_goals = (
        _list_field(payload.get("non_goals"))
        if "non_goals" in payload else current.non_goals
    )
    try:
        goal = _ledger().update_contract(
            goal_id,
            objective=objective,
            acceptance_criteria=criteria,
            constraints=constraints,
            non_goals=non_goals,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"无此目标 {goal_id}") from None
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return _view(goal)


@router.delete("/api/goals/{goal_id}")
@_storage_errors
async def delete_goal(goal_id: str):
    try:
        deleted = _ledger().delete_draft(goal_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"无此目标 {goal_id}") from None
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    if not deleted:
        raise HTTPException(status_code=500, detail="目标草稿删除失败")
    return {"ok": True, "id": goal_id}


@router.post("/api/goals/{goal_id}/run")
@_storage_errors
async def run_goal(goal_id: str, body: dict | None = None):
    goal = _require_goal(goal_id)
    payload = body or {}
    if "resume" in payload and type(payload["resume"]) is not bool:
        raise HTTPException(status_code=400, detail="resume 必须是 boolean")
    resume = payload.get("resume", False) or payload.get("mode") == "resume"
    task, replayed = await _start_goal(goal, resume=resume, previous_task_id=payload.get("expected_task_id", ""))
    return {"goal": _view(_require_goal(goal_id)), "task": task.to_dict(), "replayed": replayed}


@router.put("/api/goals/{goal_id}/criteria/{criterion_id}/verifier")
@_storage_errors
async def configure_goal_verifier(goal_id: str, criterion_id: str, body: dict):
    try:
        goal = _ledger().set_verifier(goal_id, criterion_id, body or None)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"无此目标 {goal_id}") from None
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return _view(goal)


@router.post("/api/goals/{goal_id}/verify")
@_storage_errors
async def verify_goal(goal_id: str):
    from src.web.routers.runs import get_run_manager
    from src.web.routers.tasks import get_runner

    goal = _require_goal(goal_id)
    if goal.status == "draft":
        raise HTTPException(status_code=409, detail="请先确认并执行目标合同，再运行自动验收")
    if goal.status == "achieved":
        raise HTTPException(status_code=409, detail="目标已经通过全部验收")
    runner = get_runner()
    if any(runner.is_active(task_id) for task_id in goal.task_ids):
        raise HTTPException(status_code=409, detail="开发任务仍在执行，结束后才能运行自动验收")

    manager = get_run_manager()
    active_pairs = {
        (run.goal_id, run.criterion_id)
        for run in manager.list()
        if run.status in {"queued", "running", "cancelling"}
    }
    # 一次请求取一次版本快照：逐个 criterion 各跑一遍 git 既慢又没有意义（同一时刻同一份代码），
    # 而且 evidence_revision 是阻塞的 git 子进程，不能直接压在事件循环上。
    revision = await asyncio.to_thread(evidence_revision, os.getcwd(), goal.branch)
    scheduled = []
    skipped_manual = 0
    skipped_active = 0
    for criterion in goal.acceptance_criteria:
        verifier = criterion.verifier
        if verifier is None:
            skipped_manual += 1
            continue
        if (goal.id, criterion.id) in active_pairs:
            skipped_active += 1
            continue
        if verifier.kind == "file":
            commit, verification_error = revision
            passed, summary = evaluate_file_verifier(os.getcwd(), verifier)
            _ledger().record_evidence(
                goal.id,
                criterion.id,
                kind="file",
                summary=summary,
                passed=passed,
                source=f"verifier:file:{criterion.id}",
                verified_commit=commit,
                verification_error=verification_error,
            )
            continue
        try:
            run = await manager.submit(
                verifier.command,
                kind="test",
                goal_id=goal.id,
                criterion_id=criterion.id,
                evidence_kind=verifier.kind,
                require_isolation=True,
                timeout_seconds=verifier.timeout,
            )
            scheduled.append(run)
        except ValueError as error:
            _ledger().record_evidence(
                goal.id,
                criterion.id,
                kind=verifier.kind,
                summary=f"自动验收器拒绝启动：{error}",
                passed=False,
                source=f"verifier:launch:{criterion.id}",
            )

    current = _require_goal(goal.id)
    if any(run.status in {"queued", "running"} for run in scheduled) and current.status == "active":
        with _ledger().transaction(current.id):
            latest = _require_goal(current.id)
            latest.next_action = "自动验收正在隔离环境中执行"
            _ledger().save_required(latest)
    return {
        "goal": _view(_require_goal(goal.id)),
        "runs": [run.to_dict() for run in scheduled],
        "skipped_manual": skipped_manual,
        "skipped_active": skipped_active,
    }


@router.post("/api/goals/{goal_id}/criteria/{criterion_id}/evidence")
@_storage_errors
async def record_goal_evidence(goal_id: str, criterion_id: str, body: dict):
    payload = body or {}
    # Adopt stored evidence by identity, never stamp an old copied summary with
    # the current commit. Manual observations still bind at recording time.
    adopted = {}
    if payload.get("run_id") and payload.get("evidence_id"):
        raise HTTPException(status_code=400, detail="只能选择一种证据来源")
    if payload.get("run_id"):
        from src.workflow.workbench.runs import RunLedger

        run = RunLedger(os.getcwd()).load(str(payload["run_id"]))
        if run is None or run.kind != "test" or run.status not in {"done", "failed"}:
            raise HTTPException(status_code=409, detail="测试记录不存在或尚未完成")
        payload = {
            "passed": run.status == "done" and run.code == 0,
            "kind": run.evidence_kind or "test",
            "summary": f"{run.command}（退出码 {run.code}）\n{run.output[-1200:]}",
            "source": f"run:{run.id}",
        }
        adopted = {"verified_commit": run.verified_commit, "verification_error": run.verification_error}
    elif payload.get("evidence_id"):
        goal = _require_goal(goal_id)
        evidence = next((item for item in goal.evidence if item.id == str(payload["evidence_id"])), None)
        if evidence is None:
            raise HTTPException(status_code=404, detail="证据不存在")
        payload = {
            "passed": evidence.passed, "kind": evidence.kind,
            "summary": evidence.summary, "source": f"evidence:{evidence.id}",
        }
        adopted = {"verified_commit": evidence.verified_commit, "verification_error": evidence.stale_reason}
    passed = payload.get("passed")
    if not isinstance(passed, bool):
        raise HTTPException(status_code=400, detail="passed 必须是 boolean")
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        raise HTTPException(status_code=400, detail="缺少 evidence summary（证据摘要）")
    try:
        goal = _ledger().record_evidence(
            goal_id,
            criterion_id,
            kind=str(payload.get("kind") or "manual"),
            summary=summary,
            passed=passed,
            source=str(payload.get("source") or "desktop"),
            **adopted,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"无此目标 {goal_id}") from None
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    return _view(goal)
