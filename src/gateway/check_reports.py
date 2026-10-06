"""Persist completed check reports in the existing owner session transcript.

No new database or model history. A receipt proves recoverable display output,
not that a human has viewed it. The task ledger remains the authority for content.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from src.gateway.continuations import ContinuationConflict, ContinuationService
from src.gateway.sessions import SessionTable
from src.gateway.tasks import TASK_STATE_LOCK
from src.utils.ids import safe_id, typed_id


@dataclass(frozen=True)
class PersistedCheckReport:
    report_id: str
    task_id: str
    revision: str
    attempt_id: str


class CheckReportWriter:
    def __init__(self, table: SessionTable, repo_root: str, owner: str):
        if (not isinstance(owner, str) or not typed_id(owner, "sid")
                or safe_id(owner[4:]) != owner[4:]):
            raise ContinuationConflict("汇报需要稳定、有效的所属会话")
        self.table = table
        self.repo_root = str(repo_root)
        self.owner = owner
        self.service = ContinuationService(self.repo_root, owner)

    def persist(self, payload: dict) -> PersistedCheckReport:
        if not isinstance(payload, dict):
            raise ContinuationConflict("检查汇报格式无效")
        if (not all(isinstance(payload.get(key), str) and typed_id(payload[key], prefix)
                    for key, prefix in (("task_id", "task"), ("authorization_id", "check"),
                                        ("attempt_id", "attempt")))
                or not isinstance(payload.get("revision"), str)
                or re.fullmatch(r"[0-9a-f]{64}", payload["revision"]) is None):
            raise ContinuationConflict("检查汇报身份格式无效")
        with TASK_STATE_LOCK:
            entry = self.service.completed_report(
                payload.get("task_id"), payload.get("revision"), payload.get("authorization_id"),
                payload.get("attempt_id"))
            if (payload.get("result") != entry["report"] or payload.get("budget") != entry["budget"]
                    or payload.get("tainted") is not True):
                raise ContinuationConflict("检查汇报与持久结果不一致")
            identity = [self.owner, payload["task_id"], entry["revision"], entry["attempt_id"]]
            report_id = "report-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]
            message = {"role": "assistant", "rid": report_id,
                       "text": f"后台任务只读检查（{payload['task_id']}）\n\n{entry['report']}",
                       "check_report": {"task_id": payload["task_id"], "revision": entry["revision"],
                                        "attempt_id": entry["attempt_id"], "authorization_id": entry["id"],
                                        "tainted": True}}
            if not self.table.append_durable(self.owner, self.repo_root, message):
                raise OSError("检查汇报未能持久保存，交接继续待处理")
            return PersistedCheckReport(report_id, payload["task_id"], entry["revision"], entry["attempt_id"])
