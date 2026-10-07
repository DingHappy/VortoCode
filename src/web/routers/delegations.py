"""Thin authenticated HTTP adapter for the durable dispatch service."""
from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from src.gateway.collaboration import CollaborationConflict
from src.gateway.dispatch import DispatchBusy
from src.web.task_dispatch import get_dispatch_service

router = APIRouter(prefix="/api/delegations", tags=["Task dispatch"])


class Dependency(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    task_id: str = Field(min_length=1, max_length=80, pattern=r"^task-[A-Za-z0-9_-]+$")
    round: int = Field(ge=1, le=3)


class ChainLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tasks: int = Field(ge=1, le=32)
    rounds: int = Field(ge=1, le=96)
    steps: int = Field(ge=1, le=1152)
    timeout_seconds: int = Field(ge=1, le=57600)


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session: str = Field(min_length=1, max_length=124)
    request_id: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=4000)
    agent: str = Field(default="", max_length=120)
    acceptance: list[str] = Field(default_factory=list, max_length=20)
    max_steps: int = Field(default=12, ge=1, le=12)
    timeout_seconds: int = Field(default=300, ge=1, le=600)
    depends_on: list[Dependency] = Field(default_factory=list, max_length=8)
    chain_limits: ChainLimits | None = None


class RoundAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session: str = Field(min_length=1, max_length=124)
    round: int = Field(ge=1, le=3)


class Review(RoundAction):
    verdict: Literal["accept", "rework"]
    note: str = Field(min_length=1, max_length=2000)


class CoveragePage(RoundAction):
    request_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    mode: Literal["discover", "reconcile"] = "reconcile"
    cursor: str | None = Field(default=None, max_length=180)
    page_size: int = Field(default=512, ge=1, le=512)


class CoverageArchive(RoundAction):
    request_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    checkpoint_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class CoverageRelease(RoundAction):
    archive_id: str = Field(pattern=r"^archive-[a-f0-9]{24}-[a-f0-9]{24}$")
    archive_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    checkpoint_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class CoverageVerify(RoundAction):
    artifact: dict


class CoverageObservationRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scan_id: str = Field(pattern=r"^scan-[a-f0-9]{24}$")
    round: int = Field(ge=1, le=3)
    checkpoint_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    snapshot_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    sequence: int = Field(ge=0, le=1025)


class CoverageObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session: str = Field(min_length=1, max_length=124)
    workspace: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")
    checkpoints: list[CoverageObservationRef] = Field(min_length=1, max_length=4)


class Followup(RoundAction):
    message: str = Field(min_length=1, max_length=4000)


class QuestionAnswer(RoundAction):
    question_id: str = Field(min_length=1, max_length=80, pattern=r"^question-[A-Za-z0-9_-]+$")
    answer: str = Field(min_length=1, max_length=2000)


def _http_error(error):
    status = 429 if isinstance(error, DispatchBusy) else 409 if isinstance(error, CollaborationConflict) else 503 if isinstance(error, OSError) else 400
    return HTTPException(status_code=status, detail=str(error))


def _view(task) -> dict:
    from src.gateway.handoffs import completion_state
    from src.gateway.task_questions import question_views
    from src.gateway.task_dependencies import dependency_view
    from src.gateway.task_chain_budget import budget_view
    from src.gateway.tasks import TaskLedger
    return {"id": task.id, "status": task.status, "session": task.owner_session,
            "prompt": task.prompt, "agent": task.collaboration["assignee"],
            "round": task.collaboration["round"], "review": task.collaboration["review"],
            "acceptance": task.collaboration["acceptance"], "result": task.result, "error": task.error,
            "messages": task.collaboration["messages"], "created": task.created, "updated": task.updated,
            "handling": completion_state(task),
            "questions": question_views(task),
            "dependencies": dependency_view(task, TaskLedger(os.getcwd())),
            "chain_budget": budget_view(task, TaskLedger(os.getcwd())),
            "limits": {key: task.collaboration["dispatch"][key] for key in ("max_steps", "timeout_seconds")},
            "result_url": f"/api/delegations/{task.id}?session={task.owner_session}"}


@router.get("/capabilities")
async def capabilities():
    from dataclasses import asdict
    from src.agents.subagents import registry_for
    from src.llm.client import LLMClient
    from src.llm.hard_budget import CheckLimits, check_budget_support
    roles = registry_for(os.getcwd()).specs.values()
    # The production factory still returns the generic client. Never infer a
    # reliable token bound from context_usage or SDK/OpenAI compatibility.
    check_support = check_budget_support(LLMClient)
    from src.gateway.task_chain_budget import MAX_LIMITS, DEFAULT_LIMITS
    return {"version": 1, "mode": "read", "default_agent": "read-only researcher",
            "questions": {"available": True, "max_questions": 2, "expires_seconds": 86400,
                          "answer_uses_next_round": True, "answerer": "owner"},
            "dependencies": {"available": True, "mode": "explicit_release", "max_dependencies": 8,
                             "max_depth": 8, "max_graph_tasks": 32, "waiting_tasks": 100,
                             "requires_review": "accepted", "automatic_release": False,
                             "execution_checkpoints": True, "explicit_reconcile": True,
                             "reverse_discovery": True, "event_reconcile": True, "startup_audit": True, "turn_audit": True,
                             "reverse_limits": {"scan_entries": 512, "record_bytes": 262144,
                                                "dependents": 32, "depth": 8, "edge_visits": 128},
                             "paged_coverage": {"available": True, "order": "filename_utf8_bytes_ascending",
                                                "inventory_entries": 8192, "page_entries": 512,
                                                "record_bytes": 262144, "page_bytes": 16777216, "total_bytes": 67108864,
                                                "page_dependents": 32, "graph_nodes": 1024, "graph_edges": 8192,
                                                "depth": 8, "pages": 1024, "checkpoints": 32, "checkpoint_bytes": 8388608,
                                                "retention": {"available": True, "archives": 128, "archive_bytes": 8454144,
                                                              "archive_page_size": 16, "explicit_release": True,
                                                              "historical_current_coverage": False,
                                                              "scan_retirement": {"available": True, "records": 128,
                                                                                  "record_bytes": 8192, "required_before_active_release": True,
                                                                                  "history_release": False, "import": False},
                                                              "portable_file_verify": {"available": True, "raw_json": True,
                                                                                       "file_bytes": 8454144,
                                                                                       "durable_copy_confirmed": False,
                                                                                       "history_release": False},
                                                              "local_preservation": {"available": True, "write_entry": "explicit_cli",
                                                                                     "groups": 128, "receipt_bytes": 65536,
                                                                                     "uploaded_receipt_verify": True,
                                                                                     "history_release": False}},
                                                "external_observation": {"available": True, "explicit": True,
                                                                         "checkpoints": 4, "checkpoint_bytes": 16777216,
                                                                         "pages_advanced": 0, "task_graph_reconciled": False}}},
            "chain_budget": {"available": True, "mode": "execution_allowance", "max_limits": MAX_LIMITS,
                             "defaults": DEFAULT_LIMITS, "inherited": True, "refund": False,
                             "token_cost_hard_limit": False},
            "automatic_check": {**check_support, "available": False,
                                "limits": asdict(CheckLimits()),
                                "integration": "qualified_provider_required"},
            "agents": [{"name": role.name, "description": role.description}
                       for role in roles if role.tools == "read"],
            "limits": {"max_steps": 12, "timeout_seconds": 600, "max_rounds": 3,
                       "active_tasks": 100}, "development_endpoint": "/api/tasks"}


@router.post("", status_code=202)
async def submit(body: Submission):
    try:
        task, replayed = get_dispatch_service().submit(**body.model_dump())
        return {**_view(task), "replayed": replayed}
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.get("")
async def list_delegations(session: str = Query(min_length=1, max_length=124)):
    try:
        return {"tasks": [_view(task) for task in get_dispatch_service().list(session)]}
    except ValueError as error:
        raise _http_error(error) from error


@router.get("/{task_id}")
async def get(task_id: str, session: str = Query(min_length=1, max_length=124)):
    try:
        return _view(get_dispatch_service().get(session, task_id))
    except CollaborationConflict as error:
        raise HTTPException(status_code=404, detail="任务不存在或不属于该会话") from error
    except ValueError as error:
        raise _http_error(error) from error


@router.post("/{task_id}/review")
async def review(task_id: str, body: Review):
    try:
        return _view(get_dispatch_service().review(body.session, task_id, body.round, body.verdict, body.note))
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.get("/{task_id}/dependents")
async def dependents(task_id: str, session: str = Query(min_length=1, max_length=124),
                     round: int = Query(ge=1, le=3)):
    try:
        return get_dispatch_service().dependents(session, task_id, round)
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.post("/{task_id}/reconcile-dependents", status_code=202)
async def reconcile_dependents(task_id: str, body: RoundAction):
    try:
        return get_dispatch_service().reconcile_dependents(body.session, task_id, body.round)
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.post("/{task_id}/dependency-scans", status_code=202)
async def dependency_scan(task_id: str, body: CoveragePage):
    try:
        return get_dispatch_service().dependency_scan(task_id=task_id, round_number=body.round,
                                                     **body.model_dump(exclude={"round"}))
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.get("/{task_id}/dependency-scans")
async def latest_dependency_scan(task_id: str, session: str = Query(min_length=1, max_length=124),
                                 round: int = Query(ge=1, le=3)):
    try:
        return get_dispatch_service().dependency_scan_status(session, task_id, round)
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.get("/{task_id}/dependency-scans/{scan_id}")
async def dependency_scan_status(task_id: str, scan_id: str, session: str = Query(min_length=1, max_length=124),
                                 round: int = Query(ge=1, le=3)):
    try:
        return get_dispatch_service().dependency_scan_status(session, task_id, round, scan_id)
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.get("/{task_id}/coverage-records")
async def coverage_records(task_id: str, session: str = Query(min_length=1, max_length=124),
                           offset: int = Query(default=0, ge=0, le=128), retirement_offset: int = Query(default=0, ge=0, le=128)):
    try:
        return get_dispatch_service().coverage_records(session, task_id, offset=offset, retirement_offset=retirement_offset)
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.post("/{task_id}/dependency-scans/{scan_id}/archive")
async def archive_coverage(task_id: str, scan_id: str, body: CoverageArchive):
    try:
        return get_dispatch_service().archive_coverage(body.session, task_id, body.round, scan_id,
                                                       **body.model_dump(exclude={"session", "round"}))
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.post("/{task_id}/coverage-observations")
async def observe_coverage(task_id: str, body: CoverageObservation):
    try:
        return get_dispatch_service().observe_coverage(body.session, task_id, **body.model_dump(exclude={"session"}))
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.post("/{task_id}/dependency-scans/{scan_id}/release")
async def release_coverage(task_id: str, scan_id: str, body: CoverageRelease):
    try:
        return get_dispatch_service().release_coverage(body.session, task_id, body.round, scan_id,
                                                       **body.model_dump(exclude={"session", "round"}))
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.get("/{task_id}/dependency-scans/{scan_id}/retirement")
async def coverage_retirement(task_id: str, scan_id: str, session: str = Query(min_length=1, max_length=124),
                              round: int = Query(ge=1, le=3)):
    try:
        return get_dispatch_service().coverage_retirement(session, task_id, round, scan_id)
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.get("/{task_id}/coverage-archives/{archive_id}/export")
async def export_coverage(task_id: str, archive_id: str, session: str = Query(min_length=1, max_length=124),
                          round: int = Query(ge=1, le=3)):
    try:
        return get_dispatch_service().export_coverage(session, task_id, round, archive_id)
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.get("/{task_id}/coverage-archives/{archive_id}/verify")
async def verify_stored_coverage(task_id: str, archive_id: str, session: str = Query(min_length=1, max_length=124),
                                round: int = Query(ge=1, le=3)):
    from src.gateway.task_scan import archive_view
    try:
        return archive_view(get_dispatch_service().export_coverage(session, task_id, round, archive_id))
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.post("/{task_id}/coverage-archives/{archive_id}/verify")
async def verify_exported_coverage(task_id: str, archive_id: str, body: CoverageVerify):
    from src.gateway.task_scan import archive_view
    try:
        return archive_view(get_dispatch_service().export_coverage(body.session, task_id, body.round,
                                                                    archive_id, body.artifact))
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.post("/{task_id}/coverage-archives/{archive_id}/verify-file")
async def verify_coverage_file(task_id: str, archive_id: str, request: Request,
                               session: str = Query(min_length=1, max_length=124), round: int = Query(ge=1, le=3),
                               archive_digest: str = Query(pattern=r"^[a-f0-9]{64}$"),
                               file_digest: str = Query(pattern=r"^[a-f0-9]{64}$")):
    from src.gateway.task_scan import MAX_ARCHIVE_BYTES
    if (set(request.query_params) != {"session", "round", "archive_digest", "file_digest"}
            or len(request.query_params.multi_items()) != 4):
        raise HTTPException(422, "文件核验参数缺失、重复或包含额外字段")
    payload = await _read_coverage_bytes(request, MAX_ARCHIVE_BYTES)
    try:
        return get_dispatch_service().verify_coverage_file(session, task_id, round, archive_id,
            archive_digest=archive_digest, file_digest=file_digest, payload=payload)
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


async def _read_coverage_bytes(request, maximum):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "文件回验需要原始 application/json 正文")
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        try:
            length = int(raw_length)
            if length < 0:
                raise ValueError()
        except ValueError as error:
            raise HTTPException(400, "无效文件长度") from error
        if length > maximum:
            raise HTTPException(413, f"历史文件超过 {maximum} 字节上限")
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > maximum:
            raise HTTPException(413, f"历史文件超过 {maximum} 字节上限")
        payload.extend(chunk)
    return bytes(payload)


@router.post("/{task_id}/coverage-archives/{archive_id}/verify-preservation")
async def verify_coverage_preservation(task_id: str, archive_id: str, request: Request,
        session: str = Query(min_length=1, max_length=124), round: int = Query(ge=1, le=3),
        archive_digest: str = Query(pattern=r"^[a-f0-9]{64}$"), file_digest: str = Query(pattern=r"^[a-f0-9]{64}$"),
        receipt_file_digest: str = Query(pattern=r"^[a-f0-9]{64}$")):
    from src.gateway.task_scan import MAX_PRESERVATION_REQUEST_BYTES
    fields = {"session", "round", "archive_digest", "file_digest", "receipt_file_digest"}
    if set(request.query_params) != fields or len(request.query_params.multi_items()) != len(fields):
        raise HTTPException(422, "保存凭据核对参数缺失、重复或包含额外字段")
    payload = await _read_coverage_bytes(request, MAX_PRESERVATION_REQUEST_BYTES)
    try:
        return get_dispatch_service().verify_coverage_preservation(session, task_id, round, archive_id,
            archive_digest=archive_digest, file_digest=file_digest, receipt_file_digest=receipt_file_digest, payload=payload)
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.post("/{task_id}/followup", status_code=202)
async def followup(task_id: str, body: Followup):
    try:
        return _view(get_dispatch_service().followup(body.session, task_id, body.round, body.message))
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.post("/{task_id}/cancel")
async def cancel(task_id: str, body: RoundAction):
    try:
        return _view(await get_dispatch_service().cancel(body.session, task_id, body.round))
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.post("/{task_id}/release", status_code=202)
async def release(task_id: str, body: RoundAction):
    try:
        task, replayed = get_dispatch_service().release(body.session, task_id, body.round)
        return {**_view(task), "replayed": replayed}
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.post("/{task_id}/answer", status_code=202)
async def answer_question(task_id: str, body: QuestionAnswer):
    try:
        task, replayed = get_dispatch_service().answer_question(
            body.session, task_id, body.round, body.question_id, body.answer)
        return {**_view(task), "replayed": replayed, "answered_question_id": body.question_id}
    except (ValueError, OSError) as error:
        raise _http_error(error) from error


@router.post("/{task_id}/reconcile", status_code=202)
async def reconcile_dependencies(task_id: str, body: RoundAction):
    try:
        task, changed, stopped = get_dispatch_service().reconcile_dependencies(body.session, task_id, body.round)
        return {**_view(task), "invalidation_recorded": changed, "stop_requested": stopped}
    except (ValueError, OSError) as error:
        raise _http_error(error) from error
