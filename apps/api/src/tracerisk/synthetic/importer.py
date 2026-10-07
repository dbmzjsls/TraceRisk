"""Idempotently persist the deterministic Stage 3 synthetic catalog."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.companies.models import Company, CompanyAlias
from tracerisk.evidence.models import Evidence, SourceCoverage
from tracerisk.synthetic.scenarios import CompanyScenario

ALIAS_TYPE = "historical"
COUNT_KEYS = ("companies", "company_aliases", "evidence", "source_coverage")


class SyntheticDataConflict(RuntimeError):
    """A stable catalog identity exists with different business fields."""

    def __init__(
        self,
        entity: str,
        identity: UUID,
        differences: dict[str, tuple[object, object]],
    ) -> None:
        self.entity = entity
        self.identity = identity
        self.differences = differences
        fields = ", ".join(sorted(differences)) or "missing stable identity"
        super().__init__(f"Synthetic {entity} conflict for {identity}: {fields}")


@dataclass(frozen=True)
class ImportResult:
    inserted: dict[str, int]
    totals: dict[str, int]


def coverage_checked_at(scenario: CompanyScenario) -> datetime:
    """Return the catalog's reproducible coverage check instant."""
    return datetime.combine(scenario.coverage[0].as_of_date, datetime.min.time(), UTC)


def _differences(
    actual: object, expected: dict[str, object]
) -> dict[str, tuple[object, object]]:
    return {
        field: (getattr(actual, field), value)
        for field, value in expected.items()
        if getattr(actual, field) != value
    }


async def _inserted(session: AsyncSession, statement: Any) -> int:
    result = await session.execute(statement.returning(statement.table.c.id))
    return int(result.scalar_one_or_none() is not None)


async def _verify(
    session: AsyncSession,
    model: type[Company] | type[CompanyAlias] | type[Evidence] | type[SourceCoverage],
    identity: UUID,
    entity: str,
    expected: dict[str, object],
) -> None:
    actual = await session.get(model, identity)
    if actual is None:
        raise SyntheticDataConflict(entity, identity, {})
    differences = _differences(actual, expected)
    if differences:
        raise SyntheticDataConflict(entity, identity, differences)


async def import_catalog(
    session: AsyncSession, catalog: tuple[CompanyScenario, ...]
) -> ImportResult:
    """Insert and exactly verify catalog rows without owning the transaction."""
    inserted = dict.fromkeys(COUNT_KEYS, 0)

    for scenario in catalog:
        company = scenario.company
        expected: dict[str, object] = {
            "id": company.id,
            "synthetic_key": company.synthetic_key,
            "name": company.name,
            "unified_credit_code": company.unified_credit_code,
            "is_synthetic": company.is_synthetic,
        }
        inserted["companies"] += await _inserted(
            session,
            insert(Company).values(**expected).on_conflict_do_nothing(),
        )
        await _verify(session, Company, company.id, "company", expected)

    for scenario in catalog:
        for alias in scenario.aliases:
            expected = {
                "id": alias.id,
                "company_id": alias.company_id,
                "name": alias.name,
                "alias_type": ALIAS_TYPE,
            }
            inserted["company_aliases"] += await _inserted(
                session,
                insert(CompanyAlias).values(**expected).on_conflict_do_nothing(),
            )
            await _verify(session, CompanyAlias, alias.id, "company alias", expected)

    for scenario in catalog:
        for evidence in scenario.evidence:
            expected = {
                "id": evidence.id,
                "company_id": evidence.company_id,
                "source_type": evidence.source_type.value,
                "risk_type": evidence.risk_type.value,
                "source_uri": evidence.source_uri,
                "title": evidence.title,
                "observed_at": evidence.observed_at,
                "fact_type": evidence.fact_type,
                "fact_key": evidence.fact_key,
                "fact_value": evidence.fact_value,
                "polarity": evidence.polarity,
                "content": evidence.content,
                "content_hash": evidence.content_hash,
            }
            inserted["evidence"] += await _inserted(
                session,
                insert(Evidence).values(**expected).on_conflict_do_nothing(),
            )
            await _verify(session, Evidence, evidence.id, "evidence", expected)

    for scenario in catalog:
        checked_at = coverage_checked_at(scenario)
        for coverage in scenario.coverage:
            expected = {
                "id": coverage.id,
                "company_id": coverage.company_id,
                "source_type": coverage.source_type.value,
                "as_of_date": coverage.as_of_date,
                "status": coverage.status.value,
                "checked_at": checked_at,
            }
            inserted["source_coverage"] += await _inserted(
                session,
                insert(SourceCoverage).values(**expected).on_conflict_do_nothing(),
            )
            await _verify(
                session, SourceCoverage, coverage.id, "source coverage", expected
            )

    totals = {
        "companies": len(catalog),
        "company_aliases": sum(len(item.aliases) for item in catalog),
        "evidence": sum(len(item.evidence) for item in catalog),
        "source_coverage": sum(len(item.coverage) for item in catalog),
    }
    return ImportResult(inserted=inserted, totals=totals)
