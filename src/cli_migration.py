"""Read-only inventory of explicitly selected legacy launch/job inputs.

Reports contain locations and entry-point suggestions, never command arguments,
environment values or prompt bodies. A digest detects changes, not authorization.
"""

from __future__ import annotations

import hashlib
import io
from itertools import islice
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import stat
from contextlib import redirect_stderr, redirect_stdout

MAX_FILES = 128
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_REFERENCES = 512
MAX_REPORT_BYTES = 4 * 1024 * 1024
SUFFIXES = {".service", ".conf", ".plist", ".yaml", ".yml", ".sh", ".cron"}
LEGACY = re.compile(r"(?<![\w-])(?:vc|vortocode)(?![\w/-])|(?:-m\s+(?:main|src\.cli))|main\.py")
KNOWN_COMMANDS = {
    "server",
    "im",
    "doctor",
    "canary",
    "cron",
    "collect",
    "stats",
    "digest",
    "pipeline",
    "heartbeat",
    "agent",
    "workers",
    "roster",
    "im-pair",
    "runtime-acceptance",
    "analyze",
    "test",
    "demo",
}


def _read(path: Path, limit=MAX_FILE_BYTES) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("not_regular_or_too_large")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        after, named = os.fstat(fd), path.lstat()
        if (
            len(data) > limit
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise ValueError("source_changed_or_too_large")
        return data
    finally:
        os.close(fd)


def _reference(value: str | list, location: str, *, prompt=False) -> dict | None:
    if not isinstance(value, (str, list)):
        return None
    if isinstance(value, list) and not all(isinstance(item, str) for item in value):
        raise ValueError("invalid_program_arguments")
    text = value if isinstance(value, str) else " ".join(value)
    if not LEGACY.search(text):
        return None
    row = {
        "location": location,
        "command": "unclassified",
        "status": "manual_review",
        "reason": "prompt_reference" if prompt else "indirect_or_ambiguous_command",
    }
    if prompt:
        return row
    try:
        argv = value if isinstance(value, list) else shlex.split(value, comments=True)
    except ValueError:
        return row
    if not argv:
        return None
    executable = Path(argv[0]).name
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable):
        offset = (
            3
            if len(argv) > 2 and argv[1] == "-m" and argv[2] in {"main", "src.cli"}
            else 2 if len(argv) > 1 and Path(argv[1]).name == "main.py" else None
        )
        if offset is not None:
            command = argv[offset] if len(argv) > offset else ""
            row.update(
                entry="python_compatibility_module",
                command=command if command in KNOWN_COMMANDS else "unclassified",
                reason="fixed_source_and_installation_require_review",
            )
        return row
    if executable not in {"vc", "vortocode"}:
        return row
    row["entry"] = executable
    command = argv[1] if len(argv) > 1 else ""
    row["command"] = command if command in KNOWN_COMMANDS else "unclassified"
    if command not in KNOWN_COMMANDS:
        return row
    if not isinstance(value, list) and any(
        token in text for token in ("$", "`", ";", "|", "&", "<", ">", "\n")
    ):
        return row
    try:
        from src.runtime_entry import build_parser
        from src.runtime_jobs import validate_job_args
    except ImportError:
        row["reason"] = "internal_entry_unavailable"
        return row

    parser = build_parser()
    try:
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            args = parser.parse_args(argv[1:])
            validate_job_args(parser, args)
    except (SystemExit, ValueError):
        row["reason"] = "no_equivalent_internal_invocation"
        return row
    # Preview only: the installed destination and original arguments must still be reviewed.
    replacement = str(Path(argv[0]).with_name("vortoone-runtime"))
    return {
        **row,
        "status": "entry_replacement_candidate",
        "reason": "owner_review_required",
        "replacement_entry": replacement,
        "destination_verified": False,
    }


def _references(path: Path, data: bytes) -> list[dict]:
    rows = []

    def add(value, location, **options):
        row = _reference(value, location, **options)
        if row:
            rows.append(row)
            if len(rows) > MAX_FILES:
                raise ValueError("reference_limit")

    if path.suffix == ".plist":
        value = plistlib.loads(data)
        if not isinstance(value, dict):
            raise ValueError("invalid_launch_agent")
        if "Program" in value and not isinstance(value["Program"], str):
            raise ValueError("invalid_program")
        if "ProgramArguments" in value and (
            not isinstance(value["ProgramArguments"], list)
            or not all(isinstance(item, str) for item in value["ProgramArguments"])
        ):
            raise ValueError("invalid_program_arguments")
        if not value.get("Program") and not value.get("ProgramArguments"):
            raise ValueError("missing_launch_command")
        if value.get("Program"):
            add([value["Program"], *value.get("ProgramArguments", [])[1:]], "Program")
        elif "ProgramArguments" in value:
            add(value["ProgramArguments"], "ProgramArguments")
    elif path.suffix in {".yaml", ".yml"}:
        import yaml

        value = yaml.safe_load(data)
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("jobs"), list)
            or len(value["jobs"]) > MAX_FILES
        ):
            raise ValueError("unsupported_yaml_schema")
        for index, job in enumerate(value["jobs"]):
            if not isinstance(job, dict):
                raise ValueError("invalid_job")
            for field in ("command", "prompt"):
                if field in job:
                    if not isinstance(job[field], str):
                        raise ValueError("invalid_job_text")
                    add(job[field], f"jobs[{index}].{field}", prompt=field == "prompt")
    else:
        text = data.decode("utf-8")
        for index, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if path.suffix in {".service", ".conf"}:
                key, separator, value = line.partition("=")
                if separator and key.strip() in {
                    "ExecStart",
                    "ExecStartPre",
                    "ExecStartPost",
                    "ExecStop",
                    "ExecReload",
                    "ExecStopPost",
                }:
                    add(value.strip(), f"{key.strip()}:{index}")
            else:
                add(line, f"line:{index}")
    return rows


def _digest(report: dict) -> str:
    return hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def inventory(inputs: list[str | Path]) -> dict:
    if not inputs or len(inputs) > MAX_FILES:
        raise ValueError("invalid_input_count")
    roots = sorted({str(Path(path).absolute()) for path in inputs})
    files, problems = set(), []
    seen = 0
    for raw in roots:
        path = Path(raw)
        try:
            if path.is_symlink():
                raise ValueError("symlink_not_read")
            if path.is_dir():
                # Bounded known launch/job directories, not a machine-wide text search.
                pending = [(path, 0)]
                while pending:
                    directory, depth = pending.pop()
                    remaining = MAX_FILES * 4 - seen
                    with os.scandir(directory) as entries:
                        names = [entry.name for entry in islice(entries, remaining + 1)]
                    if len(names) > remaining:
                        raise ValueError("directory_limit")
                    for name in sorted(names):
                        child = directory / name
                        seen += 1
                        if child.is_symlink():
                            problems.append({"path": str(child), "reason": "symlink_not_read"})
                        elif child.is_dir():
                            if depth >= 2:
                                problems.append({"path": str(child), "reason": "depth_limit"})
                            else:
                                pending.append((child, depth + 1))
                        elif child.suffix in SUFFIXES or child.name == "crontab":
                            files.add(child)
            else:
                files.add(path)
        except (OSError, ValueError):
            problems.append({"path": raw, "reason": "input_not_fully_read"})
    sources, total = [], 0
    references_count = 0
    for index, path in enumerate(sorted(files)):
        if index >= MAX_FILES or total >= MAX_TOTAL_BYTES:
            problems.append({"path": str(path), "reason": "file_limit"})
            break
        try:
            data = _read(path)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise ValueError("total_limit")
            if path.suffix not in SUFFIXES and path.name != "crontab":
                raise ValueError("unsupported_source")
            rows = _references(path, data)
            references_count += len(rows)
            if references_count > MAX_REFERENCES:
                problems.append({"path": str(path), "reason": "reference_limit"})
                break
            sources.append(
                {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "references": rows}
            )
        except (OSError, ValueError, TypeError, RecursionError):
            problems.append({"path": str(path), "reason": "source_not_fully_read"})
        except Exception:
            # Parser-specific failures must not expose a snippet containing credentials.
            problems.append({"path": str(path), "reason": "source_not_fully_read"})
    references = [row for source in sources for row in source["references"]]
    report = {
        "schema_version": 1,
        "inputs": roots,
        "sources": sources,
        "problems": problems,
        "complete_for_declared_inputs": not problems,
        "legacy_references": len(references),
        "manual_reviews": sum(row["status"] == "manual_review" for row in references),
        "retirement_authorized": False,
        "runtime_migration_verified": False,
        "scope": "explicit_inputs_only; indirect scripts and other hosts require separate review",
    }
    return {**report, "review_digest": _digest(report)}


def verify_report(path: str | Path) -> dict:
    saved = json.loads(_read(Path(path), MAX_REPORT_BYTES))
    if not isinstance(saved, dict) or saved.get("schema_version") != 1:
        raise ValueError("invalid_report")
    digest = saved.pop("review_digest", None)
    if (
        digest != _digest(saved)
        or not isinstance(saved.get("inputs"), list)
        or not all(isinstance(item, str) for item in saved["inputs"])
    ):
        raise ValueError("invalid_report")
    current = inventory(saved["inputs"])
    return {
        "schema_version": 1,
        "unchanged": digest == current["review_digest"],
        "complete_for_declared_inputs": current["complete_for_declared_inputs"],
        "retirement_authorized": False,
        "runtime_migration_verified": False,
    }


def write_report(path: str | Path, report: dict) -> None:
    content = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if len(content.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ValueError("report_too_large")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)
