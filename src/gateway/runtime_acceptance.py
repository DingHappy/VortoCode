"""Read-only, bounded runtime sampling; never submits tasks or calls a model."""
from __future__ import annotations

import json
import math
import os
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


@dataclass(frozen=True)
class Target:
    name: str
    url: str
    assistant: bool = False

    def validate(self):
        parts = urlsplit(self.url)
        if (self.name not in {"assistant", "code", "relay"}
                or parts.scheme not in {"http", "https"}
                or parts.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or parts.path not in {"", "/"}):
            raise ValueError("Targets must be named loopback HTTP origins without credentials")
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError("Invalid target port")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_json(url: str, token: str, timeout: float):
    headers = {"Authorization": "Bearer " + token} if token else {}
    request = Request(url, headers=headers, method="GET")
    # Redirects could forward the local bearer credential to another origin.
    with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Response exceeds sampling limit")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Expected an object")
    return data


_STATUSES = {"inactive", "idle", "running", "queued", "working", "waiting", "waiting_input", "needs_input", "completed",
             "done", "failed", "cancelled", "paused", "interrupted", "blocked"}


def _counts(rows):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Expected status rows")
    return dict(Counter(row.get("status") if row.get("status") in _STATUSES else "unknown" for row in rows))


def probe(target: Target, *, token="", require_im=False, fetch=fetch_json, timeout=3.0) -> dict:
    target.validate()
    result, problems = {}, []

    def read(path):
        return fetch(target.url.rstrip("/") + path, token, timeout)

    checks = [("health", "/api/health/quick"), ("sessions", "/api/agent/sessions"), ("im", "/api/im/status")]
    if not target.assistant:
        checks.append(("tasks", "/api/tasks"))
    for kind, path in checks:
        try:
            data = read(path)
            if kind == "health":
                result["healthy"] = data.get("status") == "healthy"
                if not result["healthy"]:
                    problems.append("health_unhealthy")
            elif kind in {"sessions", "tasks"}:
                result[kind] = _counts(data.get(kind))
                if result[kind].get("unknown"):
                    problems.append(f"{kind}_unknown_status")
                if kind == "sessions":
                    counts = [row.get("queue_count") for row in data[kind]]
                    if any(type(count) is not int or count < 0 for count in counts):
                        raise ValueError("Invalid queue count")
                    result["queued_inputs"] = sum(counts)
            else:
                if type(data.get("bridge")) is not bool:
                    raise ValueError("Invalid bridge status")
                # Never retain last_error, IDs, message text or unknown fields.
                result["im"] = {key: data[key] for key in ("bridge", "connected", "busy")
                                if type(data.get(key)) is bool}
                for key in ("undelivered", "reconnects"):
                    if type(data.get(key)) is int and data[key] >= 0:
                        result["im"][key] = data[key]
                if result["im"].get("undelivered", 0) > 0:
                    problems.append("im_delivery_pending")
                if target.assistant and require_im and not (
                        data.get("bridge") is True and data.get("connected") is True):
                    problems.append("im_disconnected")
        except HTTPError as error:
            problems.append(f"{kind}_http_{error.code}")
        except Exception as error:  # noqa: BLE001 - report uncertainty, never raw exceptions/URLs.
            problems.append(f"{kind}_{type(error).__name__}")
    result["problems"] = problems
    return result


def monitor(targets, output, *, duration=86400.0, interval=60.0, token="", require_im=False,
            fetch=fetch_json, clock=time.monotonic, sleep=time.sleep) -> dict:
    if (not math.isfinite(duration) or not 1 <= duration <= 7 * 86400
            or not math.isfinite(interval) or not 1 <= interval <= duration):
        raise ValueError("Duration must be 1s..7d; interval must be 1s..duration")
    if not targets or len({target.name for target in targets}) != len(targets):
        raise ValueError("Provide unique sampling targets")
    for target in targets:
        target.validate()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    started, samples, unhealthy = clock(), 0, 0
    interrupted = False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        def write(record):
            record["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        write({"type": "start", "duration_seconds": duration, "interval_seconds": interval,
               "targets": [target.name for target in targets], "model_requests": 0,
               "scope": "read_only_runtime_sampling"})
        try:
            while clock() - started < duration:
                readings = {target.name: probe(target, token=token, require_im=require_im, fetch=fetch)
                            for target in targets}
                ok = not any(reading["problems"] for reading in readings.values())
                samples += 1
                unhealthy += int(not ok)
                write({"type": "sample", "elapsed_seconds": round(clock() - started, 3),
                       "ok": ok, "targets": readings})
                elapsed = clock() - started
                next_slot = (math.floor(elapsed / interval) + 1) * interval
                sleep(max(0, min(duration, next_slot) - elapsed))
        except KeyboardInterrupt:
            interrupted = True
        elapsed = clock() - started
        planned = math.ceil(duration / interval)
        summary = {"type": "summary", "completed": not interrupted and elapsed >= duration,
                   "elapsed_seconds": round(elapsed, 3), "samples": samples,
                   "planned_samples": planned, "coverage_complete": samples >= planned,
                   "unhealthy_samples": unhealthy, "model_requests": 0,
                   "task_completion_scored": False, "scope": "read_only_runtime_sampling"}
        write(summary)
    return summary


def read_report(output) -> dict:
    """Missing final records are incomplete, never a successful 24-hour run."""
    path = Path(output)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Acceptance report exceeds reading limit")
    samples, unhealthy, summary, start = 0, 0, None, None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                return {"state": "incomplete", "samples": samples, "unhealthy_samples": unhealthy}
            if not isinstance(record, dict):
                raise ValueError("Invalid acceptance record")
            if record.get("type") == "start":
                if start is not None or samples:
                    raise ValueError("Multiple acceptance runs in one file")
                start = record
            elif record.get("type") == "sample":
                samples += 1
                unhealthy += int(record.get("ok") is not True)
                summary = None  # A stale summary cannot approve later samples.
            elif record.get("type") == "summary":
                summary = record
    duration = (start or {}).get("duration_seconds", 0)
    interval = (start or {}).get("interval_seconds", 0)
    valid_plan = (type(duration) in (int, float) and type(interval) in (int, float)
                  and math.isfinite(duration) and math.isfinite(interval) and 1 <= interval <= duration <= 7 * 86400)
    planned = math.ceil(duration / interval) if valid_plan else 0
    elapsed = (summary or {}).get("elapsed_seconds", 0)
    complete = bool(summary and valid_plan and type(elapsed) in (int, float) and elapsed >= duration
                    and summary.get("completed") is True and summary.get("coverage_complete") is True
                    and summary.get("samples") == samples and samples >= planned > 0)
    return {"state": ("failed" if unhealthy else "passed") if complete else "incomplete",
            "samples": samples, "unhealthy_samples": unhealthy,
            "duration_seconds": duration if valid_plan else None,
            "full_task_loop_verified": False}
