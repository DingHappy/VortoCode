"""Bounded schedule-card projections over the existing cron config and run ledger."""
from __future__ import annotations

import hashlib

from src.scheduling.cron import cron_path, parse_jobs_text
from src.workflow.workbench.runs import RunLedger


def config_revision(root: str) -> str:
    return hashlib.sha256(cron_path(root).read_bytes()).hexdigest()


def recent_runs(root: str, name: str) -> list[dict]:
    # Read at most 200 retained records; an empty result is not proof of no past runs.
    return [{"id": run.id, "status": run.status, "created": run.created,
             "summary": run.output[-2000:], "error": run.error[:1000]}
            for run in RunLedger(root).list(limit=200)
            if run.kind == "cron" and run.sandbox.get("cron_job") == name][:10]


def config_snapshot(root: str):
    path = cron_path(root)
    if not path.exists():
        return [], None
    raw = path.read_bytes()
    return parse_jobs_text(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()
