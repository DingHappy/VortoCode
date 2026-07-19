"""Structured command runs for Desktop terminals, tests, and local previews.

The agent shell already owns sandbox selection, destructive-command blocking, output
draining, and process-group termination.  This module adds the product layer that a
Desktop client needs: durable run records, exit codes, bounded output, preview URLs,
and restart recovery.  A command reaches this runtime only after an explicit user
action in Desktop; generated agent commands continue to use the normal confirmation
gate in ``build_command_tool``.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

_DIRNAME = "runs"
_VALID_KINDS = frozenset({"terminal", "test", "preview"})
_TERMINAL = frozenset({"done", "failed", "cancelled", "interrupted"})
_TEST_RESULT_STATES = frozenset({"done", "failed"})
_VERIFIER_RESULT_STATES = frozenset({"done", "failed", "interrupted"})
_MAX_OUTPUT_CHARS = 160_000
_MAX_COMMAND_CHARS = 4_000
_LOCAL_PREVIEW = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1)(?::\d{1,5})?(?:/[^\s\]\[<>'\"`]*)?",
    re.IGNORECASE,
)
_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def parse_test_results(command: str, output: str) -> Dict[str, Any]:
    """Extract a bounded cross-runner test summary without replacing raw output.

    Reporters differ wildly, so cases are best-effort while aggregate counts are
    independently recovered from the final summary.  The UI can be honest when a
    terse reporter only named failing cases.
    """
    clean = _ANSI_ESCAPE.sub("", str(output or "")).replace("\r", "")
    command_text = str(command or "").lower()
    if "pytest" in command_text or "pytest" in clean.lower():
        framework = "pytest"
    elif "vitest" in command_text or "vitest" in clean.lower():
        framework = "vitest"
    elif "jest" in command_text or "test suites:" in clean.lower():
        framework = "jest"
    elif "cargo test" in command_text or re.search(r"(?m)^test .+ \.\.\. (?:ok|FAILED|ignored)$", clean):
        framework = "rust"
    elif re.search(r"(?:^|\s)go test(?:\s|$)", command_text) or re.search(r"(?m)^--- (?:PASS|FAIL|SKIP):", clean):
        framework = "go"
    else:
        framework = "test"

    cases: List[Dict[str, str]] = []
    seen = set()
    cases_truncated = False

    def add_case(name: str, status: str, detail: str = "", duration: str = "") -> None:
        nonlocal cases_truncated
        value = name.strip()
        key = (value, status)
        if not value or key in seen:
            return
        if len(cases) >= 500:
            cases_truncated = True
            return
        seen.add(key)
        path, _, leaf = value.rpartition("::")
        cases.append({
            "name": leaf or value,
            "path": path,
            "status": status,
            "detail": detail.strip()[:500],
            "duration": duration.strip(),
        })

    for raw_line in clean.splitlines():
        line = raw_line.strip()
        pytest_match = re.match(
            r"^(PASSED|FAILED|SKIPPED|XFAIL|XPASS|ERROR)\s+(\S+?)(?:\s+-\s+(.*))?$",
            line,
        )
        if pytest_match:
            raw_status, name, detail = pytest_match.groups()
            status = {
                "PASSED": "passed", "FAILED": "failed", "SKIPPED": "skipped",
                "XFAIL": "skipped", "XPASS": "failed", "ERROR": "error",
            }[raw_status]
            add_case(name, status, detail or "")
            continue
        rust_match = re.match(r"^test (.+?) \.\.\. (ok|FAILED|ignored)$", line)
        if rust_match:
            add_case(rust_match.group(1), {"ok": "passed", "FAILED": "failed", "ignored": "skipped"}[rust_match.group(2)])
            continue
        go_match = re.match(r"^--- (PASS|FAIL|SKIP): (\S+)(?: \(([^)]+)\))?", line)
        if go_match:
            add_case(go_match.group(2), {"PASS": "passed", "FAIL": "failed", "SKIP": "skipped"}[go_match.group(1)], duration=go_match.group(3) or "")
            continue
        js_match = re.match(r"^[✓✔√]\s+(.+?)(?:\s+\(([^)]+)\))?$", line)
        if js_match:
            add_case(js_match.group(1), "passed", duration=js_match.group(2) or "")
            continue
        js_fail_match = re.match(r"^[×✕✗]\s+(.+?)(?:\s+\(([^)]+)\))?$", line)
        if js_fail_match:
            add_case(js_fail_match.group(1), "failed", duration=js_fail_match.group(2) or "")

    labels = {
        "passed": (r"(\d+)\s+passed\b", r"Tests:\s+(\d+)\s+passed\b"),
        "failed": (r"(\d+)\s+failed\b", r"Tests:\s+(\d+)\s+failed\b"),
        "skipped": (r"(\d+)\s+skipped\b", r"(\d+)\s+ignored\b", r"Tests:\s+(\d+)\s+skipped\b"),
        "errors": (r"(\d+)\s+errors?\b",),
    }
    counts: Dict[str, int] = {}
    for status, patterns in labels.items():
        matches = [int(value) for pattern in patterns for value in re.findall(pattern, clean, re.IGNORECASE)]
        counts[status] = max(matches, default=sum(case["status"] == ("error" if status == "errors" else status) for case in cases))
    counts["total"] = counts["passed"] + counts["failed"] + counts["skipped"] + counts["errors"]
    return {
        "framework": framework,
        "summary": counts,
        "cases": cases,
        "truncated": cases_truncated,
        "complete": counts["total"] == len(cases) if counts["total"] else bool(cases),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_preview_url(value: str) -> str:
    """Return a safe local http preview URL or raise ``ValueError``."""
    raw = str(value or "").strip().rstrip(".,;)")
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"预览地址无效：{error}") from error
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("预览地址只允许本机 http://localhost 或 http://127.0.0.1")
    if parsed.username or parsed.password or port is None and ":" in parsed.netloc:
        raise ValueError("预览地址不能包含凭据或无效端口")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("预览端口必须在 1–65535 之间")
    path = parsed.path or "/"
    return urlunsplit(("http", parsed.netloc, path, parsed.query, ""))


def detect_preview_url(output: str) -> str:
    """Find the latest safe localhost URL printed by a dev server."""
    matches = _LOCAL_PREVIEW.findall(_ANSI_ESCAPE.sub("", str(output or "")))
    for candidate in reversed(matches):
        try:
            return normalize_preview_url(candidate)
        except ValueError:
            continue
    return ""


@dataclass
class CommandRun:
    id: str
    command: str
    kind: str
    status: str = "queued"
    process_id: str = ""
    pid: Optional[int] = None
    code: Optional[int] = None
    output: str = ""
    dropped: int = 0
    preview_url: str = ""
    warning: str = ""
    error: str = ""
    sandbox: Dict[str, Any] = field(default_factory=dict)
    goal_id: str = ""
    criterion_id: str = ""
    evidence_kind: str = ""
    require_isolation: bool = False
    timeout_seconds: int = 0
    test_results: Dict[str, Any] = field(default_factory=dict)
    created: str = ""
    updated: str = ""

    @staticmethod
    def new(
        command: str,
        kind: str,
        *,
        preview_url: str = "",
        goal_id: str = "",
        criterion_id: str = "",
        evidence_kind: str = "",
        require_isolation: bool = False,
        timeout_seconds: int = 0,
    ) -> "CommandRun":
        now = _now()
        return CommandRun(
            id="run-" + uuid.uuid4().hex[:10],
            command=command,
            kind=kind,
            preview_url=preview_url,
            goal_id=goal_id,
            criterion_id=criterion_id,
            evidence_kind=evidence_kind,
            require_isolation=require_isolation,
            timeout_seconds=timeout_seconds,
            created=now,
            updated=now,
        )

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["passed"] = (
            self.code == 0 if self.kind == "test" and self.status in _TEST_RESULT_STATES else None
        )
        return payload

    @staticmethod
    def from_dict(payload: Dict[str, Any]) -> "CommandRun":
        known = set(CommandRun.__dataclass_fields__)
        return CommandRun(**{key: value for key, value in payload.items() if key in known})


class RunLedger:
    """Atomic per-run records under ``.vortocode/runs``."""

    def __init__(self, repo_root: str):
        self.repo_root = str(repo_root)

    def _dir(self) -> Path:
        return Path(self.repo_root) / ".vortocode" / _DIRNAME

    def _path(self, run_id: str) -> Path:
        return self._dir() / f"{run_id}.json"

    @staticmethod
    def _clean_id(run_id: str) -> str:
        value = str(run_id or "")
        return value if re.fullmatch(r"run-[A-Za-z0-9_-]+", value) else ""

    def create(
        self,
        command: str,
        kind: str,
        *,
        preview_url: str = "",
        goal_id: str = "",
        criterion_id: str = "",
        evidence_kind: str = "",
        require_isolation: bool = False,
        timeout_seconds: int = 0,
    ) -> CommandRun:
        run = CommandRun.new(
            command,
            kind,
            preview_url=preview_url,
            goal_id=goal_id,
            criterion_id=criterion_id,
            evidence_kind=evidence_kind,
            require_isolation=require_isolation,
            timeout_seconds=timeout_seconds,
        )
        self.save(run)
        return run

    def save(self, run: CommandRun) -> bool:
        run_id = self._clean_id(run.id)
        if not run_id:
            return False
        run.id = run_id
        run.output = run.output[-_MAX_OUTPUT_CHARS:]
        run.updated = _now()
        path = self._path(run_id)
        try:
            from src.agents.dev_plan import ensure_state_gitignore

            ensure_state_gitignore(self.repo_root)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(run.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(path)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def load(self, run_id: str) -> Optional[CommandRun]:
        clean = self._clean_id(run_id)
        if not clean:
            return None
        path = self._path(clean)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return CommandRun.from_dict(payload) if isinstance(payload, dict) else None
        except (OSError, TypeError, ValueError):
            return None

    def list(self) -> List[CommandRun]:
        directory = self._dir()
        if not directory.is_dir():
            return []
        items = []
        for path in directory.glob("run-*.json"):
            run = self.load(path.stem)
            if run is not None:
                try:
                    items.append((path.stat().st_mtime, run))
                except OSError:
                    items.append((0.0, run))
        items.sort(key=lambda item: item[0], reverse=True)
        return [run for _mtime, run in items]

    def recover_interrupted(self) -> List[CommandRun]:
        recovered = []
        for run in self.list():
            if run.status in {"queued", "running", "cancelling"}:
                run.status = "interrupted"
                run.error = "runtime 重启，原进程已失联"
                self.save(run)
                recovered.append(run)
        return recovered


class RunManager:
    """Starts and monitors user-invoked workspace commands."""

    def __init__(
        self,
        repo_root: str,
        *,
        poll_interval: float = 0.35,
        on_update: Optional[Callable[[CommandRun], None]] = None,
    ):
        self.repo_root = str(repo_root)
        self.ledger = RunLedger(repo_root)
        self.poll_interval = max(0.01, poll_interval)
        self._processes: Dict[str, str] = {}
        self._monitors: Dict[str, asyncio.Task] = {}
        self._on_update = on_update

    def _notify(self, run: CommandRun) -> None:
        if self._on_update is None:
            return
        try:
            self._on_update(run)
        except Exception:  # noqa: BLE001 - UI notification must not break the process
            pass

    def _sync_goal_evidence(self, run: CommandRun) -> None:
        """Attach a terminal verifier result to its acceptance criterion."""
        if not run.goal_id or not run.criterion_id or run.status not in _VERIFIER_RESULT_STATES:
            return
        output = _ANSI_ESCAPE.sub("", run.output).strip()
        detail = output[-1200:] if output else (run.error or "命令没有输出")
        summary = f"{run.command}（退出码 {run.code}）\n{detail}"
        try:
            from src.gateway.goals import GoalLedger

            GoalLedger(self.repo_root).record_evidence(
                run.goal_id,
                run.criterion_id,
                kind=run.evidence_kind or "test",
                summary=summary,
                passed=run.code == 0,
                source=f"run:{run.id}",
            )
        except (KeyError, OSError, ValueError):
            # Run history remains authoritative even if a goal was removed/corrupted.
            return

    def recover(self) -> List[CommandRun]:
        recovered = self.ledger.recover_interrupted()
        for run in recovered:
            self._sync_goal_evidence(run)
            self._notify(run)
        return recovered

    async def submit(
        self,
        command: str,
        *,
        kind: str = "terminal",
        preview_url: str = "",
        goal_id: str = "",
        criterion_id: str = "",
        evidence_kind: str = "",
        require_isolation: bool = False,
        timeout_seconds: int = 0,
    ) -> CommandRun:
        from src.agents.shell import is_dangerous, run_command_background

        command = str(command or "").strip()
        kind = str(kind or "terminal").strip().lower()
        if not command:
            raise ValueError("缺少要运行的命令")
        if len(command) > _MAX_COMMAND_CHARS:
            raise ValueError(f"命令超过 {_MAX_COMMAND_CHARS} 字符上限")
        if kind not in _VALID_KINDS:
            raise ValueError("运行类型只支持 terminal / test / preview")
        try:
            timeout_seconds = int(timeout_seconds or 0)
        except (TypeError, ValueError):
            raise ValueError("timeout_seconds 必须是整数") from None
        if timeout_seconds < 0 or timeout_seconds > 900:
            raise ValueError("timeout_seconds 必须在 0–900 秒之间")
        danger = is_dangerous(command)
        if danger:
            raise ValueError(f"拒绝执行疑似危险命令：{danger}")
        preview_url = normalize_preview_url(preview_url) if preview_url else ""

        run = self.ledger.create(
            command,
            kind,
            preview_url=preview_url,
            goal_id=str(goal_id or ""),
            criterion_id=str(criterion_id or ""),
            evidence_kind=str(evidence_kind or ""),
            require_isolation=bool(require_isolation),
            timeout_seconds=timeout_seconds,
        )
        self._notify(run)
        started = await asyncio.to_thread(
            run_command_background,
            self.repo_root,
            command,
            require_isolation=run.require_isolation,
        )
        if not started.get("ok"):
            run.status = "failed"
            run.code = -1
            run.error = str(started.get("error") or "无法启动命令")[-1000:]
            run.sandbox = dict(started.get("sandbox") or {})
            self.ledger.save(run)
            self._sync_goal_evidence(run)
            self._notify(run)
            return run

        run.status = "running"
        run.process_id = str(started["id"])
        run.pid = int(started["pid"])
        run.warning = str(started.get("warning") or "")
        run.sandbox = dict(started.get("sandbox") or {})
        self.ledger.save(run)
        self._notify(run)
        self._processes[run.id] = run.process_id
        monitor = asyncio.create_task(self._monitor(run.id, run.process_id))
        self._monitors[run.id] = monitor
        return run

    async def _monitor(self, run_id: str, process_id: str) -> None:
        from src.agents.shell import read_background, stop_background

        started_at = time.monotonic()
        try:
            while True:
                result = await asyncio.to_thread(read_background, process_id)
                run = self.ledger.load(run_id)
                if run is None:
                    return
                if not result.get("ok"):
                    run.status = "failed"
                    run.error = str(result.get("error") or "读取命令输出失败")[-1000:]
                    self.ledger.save(run)
                    self._sync_goal_evidence(run)
                    self._notify(run)
                    return

                changed = False
                chunk = str(result.get("output") or "")
                if chunk:
                    run.output = f"{run.output}\n{chunk}".lstrip("\n")[-_MAX_OUTPUT_CHARS:]
                    if run.kind == "preview" and not run.preview_url:
                        run.preview_url = detect_preview_url(chunk)
                    changed = True
                dropped = int(result.get("dropped") or 0)
                if dropped:
                    run.dropped += dropped
                    changed = True
                if result.get("status") != "running":
                    run.code = result.get("code")
                    run.status = "done" if run.code == 0 else "failed"
                    if run.code != 0 and not run.error:
                        run.error = f"命令退出码 {run.code}"
                    if run.kind == "test":
                        run.test_results = parse_test_results(run.command, run.output)
                    self.ledger.save(run)
                    self._sync_goal_evidence(run)
                    self._notify(run)
                    return
                if run.timeout_seconds and time.monotonic() - started_at >= run.timeout_seconds:
                    stopped = await asyncio.to_thread(stop_background, process_id)
                    run.code = stopped.get("code")
                    if run.code in {None, 0}:
                        run.code = -1
                    run.status = "failed"
                    run.error = f"命令超过 {run.timeout_seconds} 秒，已停止"
                    if run.kind == "test":
                        run.test_results = parse_test_results(run.command, run.output)
                    self.ledger.save(run)
                    self._sync_goal_evidence(run)
                    self._notify(run)
                    return
                if changed:
                    self.ledger.save(run)
                    self._notify(run)
                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            raise
        finally:
            self._processes.pop(run_id, None)
            self._monitors.pop(run_id, None)

    async def cancel(self, run_id: str) -> Optional[CommandRun]:
        from src.agents.shell import stop_background

        run = self.ledger.load(run_id)
        if run is None:
            return None
        process_id = self._processes.get(run_id) or run.process_id
        if run.status in _TERMINAL or not process_id:
            return run
        run.status = "cancelling"
        self.ledger.save(run)
        self._notify(run)
        stopped = await asyncio.to_thread(stop_background, process_id)
        monitor = self._monitors.pop(run_id, None)
        if monitor is not None:
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor
        self._processes.pop(run_id, None)
        current = self.ledger.load(run_id) or run
        current.code = stopped.get("code")
        current.status = "cancelled" if stopped.get("ok") and stopped.get("stopped") else "failed"
        if current.status == "failed":
            current.error = str(stopped.get("error") or "无法停止命令")[-1000:]
        self.ledger.save(current)
        self._notify(current)
        return current

    async def shutdown(self) -> None:
        for run_id in list(self._processes):
            await self.cancel(run_id)

    def get(self, run_id: str) -> Optional[CommandRun]:
        return self.ledger.load(run_id)

    def list(self) -> List[CommandRun]:
        return self.ledger.list()

    async def wait(self, run_id: str, timeout: float = 10.0) -> None:
        """Wait for a run monitor in tests/one-shot clients."""
        monitor = self._monitors.get(run_id)
        if monitor is not None:
            await asyncio.wait_for(asyncio.shield(monitor), timeout=timeout)
