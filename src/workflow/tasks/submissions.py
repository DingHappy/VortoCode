"""Persistent idempotency for approved development submissions; never replay execution."""
from __future__ import annotations

import hashlib
import re

from src.workflow.pipeline.pipeline_storage import pipeline_lock
from src.workflow.tasks.tasks import TASK_STATE_LOCK


def submission_task_id(request_id: str) -> str:
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", request_id):
        raise ValueError("request_id 必须为 1 到 120 位的字母、数字、点、下划线或连字符")
    return "task-submit-" + hashlib.sha256(request_id.encode()).hexdigest()[:32]


def is_submission_task_id(task_id: str) -> bool:
    """Recognize the reserved durable submission identity without authorizing replay."""
    return isinstance(task_id, str) and re.fullmatch(r"task-submit-[0-9a-f]{32}", task_id) is not None


async def submit_once(runner, request_id: str, prompt: str, owner_session: str = ""):
    tid = submission_task_id(request_id)
    # The service holds the workspace execution lease. This lock also serializes
    # durable creation across callers; an existing task is never enqueued again.
    with pipeline_lock(runner.ledger.repo_root, tid, kind="submission"), TASK_STATE_LOCK:
        task = runner.ledger.load(tid)
        if task is not None:
            if task.kind != "dev" or task.prompt != prompt or task.owner_session != owner_session:
                raise ValueError("request_id 已用于不同的任务合同")
            return task
        if runner.ledger._path(tid).exists():
            raise OSError("提交记录无法读取；未重新执行")
        if runner._closing:
            raise RuntimeError("Runtime is shutting down")
        task = runner.ledger.create("dev", prompt, owner_session=owner_session, tid=tid)
        runner.enqueue_worker(tid)
        return task
