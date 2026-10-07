from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from uuid import UUID


@dataclass(frozen=True)
class EvidenceReference:
    evidence_id: UUID
    content_hash: str


@dataclass(frozen=True)
class RuleEvaluation:
    rule_code: str
    rule_version: str
    score: int
    parameters: dict[str, object]
    matched_fact_keys: tuple[str, ...]
    evidence_references: tuple[EvidenceReference, ...]

    @property
    def is_match(self) -> bool:
        return self.score > 0


@dataclass(frozen=True)
class RuleScore:
    total_score: int
    evaluations: tuple[RuleEvaluation, ...]


def select_latest_states[FactT](
    facts: Iterable[FactT],
    *,
    as_of_date: date,
    fact_key: Callable[[FactT], str],
    observed_at: Callable[[FactT], date],
    evidence_id: Callable[[FactT], UUID],
) -> tuple[FactT, ...]:
    """Select one stable latest event per fact key on or before the baseline."""
    latest: dict[str, FactT] = {}
    for fact in facts:
        if observed_at(fact) > as_of_date:
            continue
        key = fact_key(fact)
        current = latest.get(key)
        if current is None or (observed_at(fact), str(evidence_id(fact))) > (
            observed_at(current),
            str(evidence_id(current)),
        ):
            latest[key] = fact
    return tuple(latest[key] for key in sorted(latest))


def references_for[FactT](
    facts: Iterable[FactT],
    *,
    evidence_id: Callable[[FactT], UUID],
    content_hash: Callable[[FactT], str],
) -> tuple[EvidenceReference, ...]:
    return tuple(
        EvidenceReference(evidence_id(fact), content_hash(fact)) for fact in facts
    )
