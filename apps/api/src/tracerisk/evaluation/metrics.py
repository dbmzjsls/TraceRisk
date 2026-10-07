"""Pure, immutable evaluation measurements with auditable denominators."""

import json
from dataclasses import asdict, dataclass, field
from math import isfinite
from typing import Literal


def canonical_json(document: object) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=_json_default,
        )
        + "\n"
    ).encode("utf-8")


def _json_default(value: object) -> object:
    if isinstance(value, MetricResult):
        return asdict(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


@dataclass(frozen=True)
class MetricResult:
    name: str
    formula: str
    numerator: float
    denominator: int
    sample_ids: tuple[str, ...]
    source_snapshot_sha256: str
    mode: Literal["mock"]
    limitations: tuple[str, ...] = field(default_factory=tuple)
    value: float | None = field(init=False)
    status: Literal["ok", "not_applicable"] = field(init=False)

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.formula.strip():
            raise ValueError("Metric requires a name and formula")
        if (
            not isfinite(self.numerator)
            or self.numerator < 0
            or self.denominator < 0
            or self.numerator > self.denominator
        ):
            raise ValueError("Invalid metric numerator or denominator")
        if (
            self.denominator != len(self.sample_ids)
            or (len(set(self.sample_ids)) != len(self.sample_ids))
            or any(not item.strip() for item in self.sample_ids)
        ):
            raise ValueError("Metric requires one unique sample ID per denominator")
        if len(self.source_snapshot_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_snapshot_sha256
        ):
            raise ValueError("Metric requires a source snapshot SHA-256")
        if self.denominator:
            object.__setattr__(self, "value", self.numerator / self.denominator)
            object.__setattr__(self, "status", "ok")
        else:
            object.__setattr__(self, "value", None)
            object.__setattr__(self, "status", "not_applicable")


def ratio_metric(
    name: str,
    formula: str,
    samples: dict[str, bool],
    *,
    source_snapshot_sha256: str,
    limitations: tuple[str, ...] = (),
) -> MetricResult:
    return MetricResult(
        name=name,
        formula=formula,
        numerator=sum(samples.values()),
        denominator=len(samples),
        sample_ids=tuple(samples),
        source_snapshot_sha256=source_snapshot_sha256,
        mode="mock",
        limitations=limitations,
    )
