"""Versioned execution observations shared by native, CLI and session adapters.

Pure data, not a scheduler, permission grant, routing registry or task database.
Backend names and feature names are extensible; known fields have strict types.
An execution finishing never establishes business acceptance.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, ClassVar, Mapping, Protocol

_PROCESS_ID = uuid.uuid4().hex


def process_instance(adapter: str) -> str:
    """Process-lifetime identity only; durable hosts must supply their own identity."""
    return f"process:{_PROCESS_ID}:{adapter}"


def new_run_id() -> str:
    return uuid.uuid4().hex


def _text(value: object, name: str, *, required: bool = False) -> None:
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"{name} must be a{' nonempty' if required else ''} string")


def _number(value: object, name: str, *, integer: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite and nonnegative")
    try:
        valid = math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be finite and nonnegative")
    if integer and (not isinstance(value, int) or value > 2 ** 53 - 1):
        raise ValueError(f"{name} must be a JSON-safe integer")


class ExecutionState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ExecutionRef:
    backend_id: str
    instance_id: str
    run_id: str
    session_id: str = ""
    turn_id: str = ""
    task_id: str = ""
    scope: str = "process"

    def __post_init__(self) -> None:
        for name in ("backend_id", "instance_id", "run_id"):
            _text(getattr(self, name), name, required=True)
        for name in ("session_id", "turn_id", "task_id"):
            _text(getattr(self, name), name)
        if self.scope not in {"process", "runtime"}:
            raise ValueError("unknown identity scope")

    @property
    def key(self) -> tuple[str, str, str]:
        return self.backend_id, self.instance_id, self.run_id


@dataclass(frozen=True)
class ExecutionProgress:
    summary: str = ""
    completed_steps: int | None = None
    total_steps: int | None = None

    def __post_init__(self) -> None:
        _text(self.summary, "progress summary")
        for name in ("completed_steps", "total_steps"):
            if (value := getattr(self, name)) is not None:
                _number(value, name, integer=True)
        if self.completed_steps is not None and self.total_steps is not None and self.completed_steps > self.total_steps:
            raise ValueError("completed_steps exceeds total_steps")


@dataclass(frozen=True)
class ExecutionUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens", "cost_usd"):
            if (value := getattr(self, name)) is not None:
                _number(value, name, integer=name != "cost_usd")

    @classmethod
    def reported(cls, values: Mapping[str, Any], cost_usd: object = None) -> ExecutionUsage:
        """Malformed/missing provider measurements remain unknown, never zero."""
        def counter(value: Any) -> int | None:
            return value if type(value) is int and 0 <= value <= 2 ** 53 - 1 else None
        cost = None
        if isinstance(cost_usd, (int, float)) and not isinstance(cost_usd, bool):
            try:
                _number(cost_usd, "reported cost")
                cost = float(cost_usd)
            except ValueError:
                pass
        return cls(counter(values.get("input_tokens")), counter(values.get("output_tokens")), cost)


@dataclass(frozen=True)
class ExecutionFailure:
    code: str
    message: str
    retryable: bool | None = None
    effect: str = "unknown"

    def __post_init__(self) -> None:
        _text(self.code, "error code", required=True)
        _text(self.message, "error message")
        if self.retryable is not None and type(self.retryable) is not bool:
            raise ValueError("retryable must be a boolean or unknown")
        if self.effect not in {"not_started", "possible", "unknown"}:
            raise ValueError("unknown execution effect")


@dataclass(frozen=True)
class ExecutionEvidence:
    evidence_id: str
    ref: ExecutionRef
    kind: str
    verdict: str
    observed_at: float
    summary: str = ""
    revision: str = ""
    verified_revision: str = ""

    def __post_init__(self) -> None:
        for name in ("evidence_id", "kind"):
            _text(getattr(self, name), name, required=True)
        for name in ("summary", "revision", "verified_revision"):
            _text(getattr(self, name), name)
        _number(self.observed_at, "evidence time")
        if not isinstance(self.ref, ExecutionRef) or self.verdict not in {"passed", "failed", "unknown"}:
            raise ValueError("invalid evidence reference or verdict")

    def matches_revision(self, ref: ExecutionRef, revision: str) -> bool:
        """Binding check only; does not grant approval or establish goal acceptance."""
        return (self.ref == ref and bool(revision) and self.revision == revision
                and self.verified_revision == revision and self.verdict == "passed")


@dataclass(frozen=True)
class ExecutionResult:
    summary: str = ""
    evidence: tuple[ExecutionEvidence, ...] = ()
    usage: ExecutionUsage = field(default_factory=ExecutionUsage)

    def __post_init__(self) -> None:
        _text(self.summary, "result summary")
        if not isinstance(self.evidence, tuple) or not all(isinstance(item, ExecutionEvidence) for item in self.evidence):
            raise ValueError("evidence must be an immutable collection")
        if not isinstance(self.usage, ExecutionUsage):
            raise ValueError("invalid usage")


@dataclass(frozen=True)
class ExecutionSnapshot:
    schema_version: ClassVar[int] = 1
    ref: ExecutionRef
    state: ExecutionState
    observed_at: float
    native_state: str = ""
    progress: ExecutionProgress = field(default_factory=ExecutionProgress)
    result: ExecutionResult | None = None
    error: ExecutionFailure | None = None
    features: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.ref, ExecutionRef) or not isinstance(self.state, ExecutionState):
            raise ValueError("invalid execution identity or state")
        _number(self.observed_at, "observation time")
        _text(self.native_state, "native state")
        if not isinstance(self.progress, ExecutionProgress):
            raise ValueError("invalid progress")
        if self.result is not None and not isinstance(self.result, ExecutionResult):
            raise ValueError("invalid result")
        if self.error is not None and not isinstance(self.error, ExecutionFailure):
            raise ValueError("invalid error")
        if not isinstance(self.features, frozenset) or any(not isinstance(item, str) or not item.strip() for item in self.features):
            raise ValueError("features must be an immutable set of names")
        if self.result is not None and any(item.ref != self.ref for item in self.result.evidence):
            raise ValueError("evidence belongs to another execution")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(schema_version=self.schema_version, state=self.state.value, features=sorted(self.features))
        return data

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ExecutionSnapshot:
        """v1 allows additive fields; unknown major versions are rejected.

        Unknown states stay unknown, retaining their native name. Decoding data
        never authorizes a feature or chooses a backend/host to execute it.
        """
        if not isinstance(value, Mapping) or type(value.get("schema_version")) is not int or value["schema_version"] != cls.schema_version:
            raise ValueError("unsupported execution schema version")
        def fields(raw: Any, names: tuple[str, ...]) -> dict:
            if not isinstance(raw, Mapping):
                raise ValueError("expected an object")
            return {name: raw[name] for name in names if name in raw}
        def ref(raw: Any) -> ExecutionRef:
            try:
                return ExecutionRef(**fields(raw, ("backend_id", "instance_id", "run_id", "session_id", "turn_id", "task_id", "scope")))
            except TypeError as exc:
                raise ValueError("incomplete execution identity") from exc
        raw_state = value.get("state", "unknown")
        _text(raw_state, "execution state")
        unknown_state = False
        try:
            state = ExecutionState(raw_state)
        except ValueError:
            state = ExecutionState.UNKNOWN
            unknown_state = True
        result = None
        if (raw := value.get("result")) is not None:
            raw_result = fields(raw, ("summary", "evidence", "usage"))
            raw_evidence = raw_result.get("evidence", [])
            if not isinstance(raw_evidence, (list, tuple)):
                raise ValueError("invalid evidence list")
            evidence = []
            for item in raw_evidence:
                entry = fields(item, ("evidence_id", "kind", "verdict", "observed_at", "summary", "revision", "verified_revision"))
                try:
                    evidence.append(ExecutionEvidence(ref=ref(item.get("ref")), **entry))
                except TypeError as exc:
                    raise ValueError("incomplete execution evidence") from exc
            usage = ExecutionUsage(**fields(raw_result.get("usage", {}), ("input_tokens", "output_tokens", "cost_usd")))
            result = ExecutionResult(summary=raw_result.get("summary", ""), evidence=tuple(evidence), usage=usage)
        error = None
        if value.get("error") is not None:
            try:
                error = ExecutionFailure(**fields(value["error"], ("code", "message", "retryable", "effect")))
            except TypeError as exc:
                raise ValueError("incomplete execution error") from exc
        features = value.get("features", [])
        if not isinstance(features, (list, tuple, frozenset)) or any(not isinstance(item, str) for item in features):
            raise ValueError("invalid feature names")
        native_state = value.get("native_state", "")
        _text(native_state, "native state")
        if unknown_state and not native_state:
            native_state = str(raw_state)
        try:
            return cls(ref=ref(value.get("ref")), state=state, observed_at=value["observed_at"],
                       native_state=native_state,
                       progress=ExecutionProgress(**fields(value.get("progress", {}), ("summary", "completed_steps", "total_steps"))),
                       result=result, error=error, features=frozenset(features))
        except (KeyError, TypeError) as exc:
            raise ValueError("incomplete execution snapshot") from exc


class ExecutionReporter(Protocol):
    @property
    def execution_report(self) -> ExecutionSnapshot | None: ...
