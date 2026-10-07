from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.evidence.models import Evidence
from tracerisk.evidence.service import load_frozen_retrieval

_VALUE_SETS = {
    "judicial_case_status": frozenset({"open", "closed"}),
    "business_abnormal_status": frozenset({"active", "resolved"}),
    "negative_news_story": frozenset({"negative", "positive"}),
    "core_ip_status": frozenset({"valid", "invalid", "ownership_dispute"}),
    "noncore_ip_status": frozenset({"valid", "invalid", "ownership_dispute"}),
}
_AUTHORITY_BY_FACT_TYPE = {
    "judicial_case_status": "judicial",
    "business_abnormal_status": "business",
    "core_ip_status": "intellectual_property",
    "noncore_ip_status": "intellectual_property",
}


@dataclass(frozen=True)
class ConflictFact:
    evidence_id: UUID
    company_id: UUID
    source_type: str
    risk_type: str
    fact_type: str
    fact_key: str
    fact_value: str
    observed_at: date
    content_hash: str
    polarity: str = "neutral"


@dataclass(frozen=True)
class FactConflict:
    company_id: UUID
    risk_type: str
    fact_type: str
    fact_key: str
    sides: tuple[ConflictFact, ...]
    authority_source: str | None
    resolved_value: str | None


@dataclass(frozen=True)
class ConflictEvaluation:
    selected_facts: tuple[ConflictFact, ...]
    resolved_facts: tuple[ConflictFact, ...]
    conflicts: tuple[FactConflict, ...]

    @property
    def requires_review(self) -> bool:
        return bool(self.conflicts)

    @property
    def has_unresolved(self) -> bool:
        return any(conflict.resolved_value is None for conflict in self.conflicts)


def _fact_order(fact: ConflictFact) -> tuple[str, str, str, str, str, str]:
    return (
        str(fact.company_id),
        fact.risk_type,
        fact.fact_type,
        fact.fact_key,
        fact.source_type,
        str(fact.evidence_id),
    )


def evaluate_conflicts(
    facts: Iterable[ConflictFact], *, as_of_date: date
) -> ConflictEvaluation:
    latest_by_source: dict[tuple[UUID, str, str, str, str], ConflictFact] = {}
    for fact in facts:
        allowed_values = _VALUE_SETS.get(fact.fact_type)
        if allowed_values is None or fact.fact_value not in allowed_values:
            raise ValueError(
                f"Unknown normalized value for {fact.fact_type}: {fact.fact_value}"
            )
        if fact.observed_at > as_of_date:
            continue
        key = (
            fact.company_id,
            fact.risk_type,
            fact.fact_type,
            fact.fact_key,
            fact.source_type,
        )
        current = latest_by_source.get(key)
        if current is None or (fact.observed_at, str(fact.evidence_id)) > (
            current.observed_at,
            str(current.evidence_id),
        ):
            latest_by_source[key] = fact

    selected = tuple(sorted(latest_by_source.values(), key=_fact_order))
    groups: dict[tuple[UUID, str, str, str], list[ConflictFact]] = {}
    for fact in selected:
        groups.setdefault(
            (fact.company_id, fact.risk_type, fact.fact_type, fact.fact_key), []
        ).append(fact)

    resolved: list[ConflictFact] = []
    conflicts: list[FactConflict] = []
    for group_key in sorted(groups, key=lambda key: tuple(map(str, key))):
        sides = tuple(
            sorted(
                groups[group_key],
                key=lambda item: (item.source_type, str(item.evidence_id)),
            )
        )
        current_values = {side.fact_value for side in sides}
        authority = _AUTHORITY_BY_FACT_TYPE.get(group_key[2])
        authoritative = next(
            (side for side in sides if side.source_type == authority), None
        )
        if len(current_values) == 1:
            resolved.append(authoritative or sides[0])
            continue
        chosen = authoritative
        conflicts.append(
            FactConflict(
                company_id=group_key[0],
                risk_type=group_key[1],
                fact_type=group_key[2],
                fact_key=group_key[3],
                sides=sides,
                authority_source=authority if authoritative is not None else None,
                resolved_value=chosen.fact_value if chosen is not None else None,
            )
        )
        if chosen is not None:
            resolved.append(chosen)
    return ConflictEvaluation(
        selected_facts=selected,
        resolved_facts=tuple(sorted(resolved, key=_fact_order)),
        conflicts=tuple(conflicts),
    )


def evidence_to_conflict_fact(record: Evidence) -> ConflictFact:
    return ConflictFact(
        evidence_id=record.id,
        company_id=record.company_id,
        source_type=record.source_type,
        risk_type=record.risk_type,
        fact_type=record.fact_type,
        fact_key=record.fact_key,
        fact_value=record.fact_value,
        observed_at=record.observed_at,
        content_hash=record.content_hash,
        polarity=record.polarity,
    )


async def load_conflicts(
    session: AsyncSession, *, company_id: UUID, as_of_date: date
) -> ConflictEvaluation:
    records = list(
        (
            await session.scalars(
                select(Evidence)
                .where(
                    Evidence.company_id == company_id,
                    Evidence.observed_at <= as_of_date,
                )
                .order_by(
                    Evidence.risk_type,
                    Evidence.fact_type,
                    Evidence.fact_key,
                    Evidence.source_type,
                    Evidence.observed_at,
                    Evidence.id,
                )
            )
        ).all()
    )
    return evaluate_conflicts(
        (evidence_to_conflict_fact(record) for record in records),
        as_of_date=as_of_date,
    )


async def load_retrieved_conflicts(
    session: AsyncSession, *, run_id: UUID, as_of_date: date
) -> ConflictEvaluation:
    rows = await load_frozen_retrieval(session, run_id=run_id)
    return evaluate_conflicts(
        (
            ConflictFact(
                evidence_id=chunk.evidence_id,
                company_id=chunk.company_id,
                source_type=chunk.source_type,
                risk_type=chunk.risk_type,
                fact_type=chunk.fact_type,
                fact_key=chunk.fact_key,
                fact_value=chunk.fact_value,
                observed_at=chunk.observed_at,
                content_hash=chunk.evidence_content_hash,
                polarity=chunk.polarity,
            )
            for chunk, _ in rows
        ),
        as_of_date=as_of_date,
    )
