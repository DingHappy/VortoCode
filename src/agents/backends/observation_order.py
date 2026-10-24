"""Causal ordering for replaceable observations; never execution authorization."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


class ObservationConflict(ValueError):
    """A report no longer follows the accepted observation; do not reclaim it."""


@dataclass(frozen=True)
class ObservationCursor:
    producer_id: str
    generation_id: str
    sequence: int

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() or len(value) > 200
               for value in (self.producer_id, self.generation_id)):
            raise ValueError("invalid observation producer or generation")
        if type(self.sequence) is not int or not 1 <= self.sequence <= 2 ** 53 - 1:
            raise ValueError("invalid observation sequence")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Any) -> ObservationCursor:
        if not isinstance(raw, Mapping):
            raise ValueError("invalid observation cursor")
        try:
            return cls(raw["producer_id"], raw["generation_id"], raw["sequence"])
        except KeyError as error:
            raise ValueError("incomplete observation cursor") from error


@dataclass(frozen=True)
class ObservationOrder:
    cursor: ObservationCursor
    parent: ObservationCursor | None

    def __post_init__(self) -> None:
        if not isinstance(self.cursor, ObservationCursor) or (self.parent is not None and not isinstance(self.parent, ObservationCursor)):
            raise ValueError("invalid observation order")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "cursor": self.cursor.to_dict(),
                "parent": self.parent.to_dict() if self.parent is not None else None}

    @classmethod
    def from_dict(cls, raw: Any) -> ObservationOrder:
        if (not isinstance(raw, Mapping) or type(raw.get("schema_version")) is not int
                or raw["schema_version"] != 1 or "parent" not in raw):
            raise ValueError("unsupported or incomplete observation order")
        return cls(ObservationCursor.from_dict(raw.get("cursor")),
                   ObservationCursor.from_dict(raw["parent"]) if raw["parent"] is not None else None)

    def require_successor(self, current: ObservationCursor | None) -> None:
        if self.parent != current:
            raise ObservationConflict("进度报告的前序已经变化，未覆盖当前状态")
        same_generation = (current is not None and self.cursor.producer_id == current.producer_id
                           and self.cursor.generation_id == current.generation_id)
        expected = current.sequence + 1 if same_generation and current is not None else 1
        if self.cursor.sequence != expected:
            raise ObservationConflict("进度报告的代次或序号不连续，未覆盖当前状态")
