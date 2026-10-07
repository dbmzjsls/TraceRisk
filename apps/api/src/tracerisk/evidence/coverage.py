from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.evidence.models import SourceCoverage

SOURCE_ORDER = ("business", "judicial", "news", "intellectual_property")
_VALID_STATUSES = frozenset({"available", "unavailable", "stale"})


@dataclass(frozen=True)
class CoverageRecord:
    source_type: str
    status: str
    as_of_date: date
    checked_at: datetime
    coverage_id: UUID


@dataclass(frozen=True)
class CoverageEntry:
    source_type: str
    status: str
    coverage_id: UUID | None
    checked_at: datetime | None


@dataclass(frozen=True)
class CoverageEvaluation:
    entries: tuple[CoverageEntry, ...]
    insufficiency_details: tuple[str, ...]

    @property
    def is_sufficient(self) -> bool:
        return not self.insufficiency_details


def evaluate_coverage(
    records: list[CoverageRecord] | tuple[CoverageRecord, ...], *, as_of_date: date
) -> CoverageEvaluation:
    by_source: dict[str, CoverageRecord] = {}
    for record in records:
        if record.source_type not in SOURCE_ORDER:
            raise ValueError(f"Unknown coverage source: {record.source_type}")
        if record.status not in _VALID_STATUSES:
            raise ValueError(f"Unknown coverage status: {record.status}")
        if record.as_of_date != as_of_date:
            raise ValueError("Coverage date does not match the investigation baseline")
        if record.source_type in by_source:
            raise ValueError(f"Duplicate coverage source: {record.source_type}")
        by_source[record.source_type] = record
    entries = tuple(
        CoverageEntry(
            source_type=source,
            status=by_source[source].status if source in by_source else "missing",
            coverage_id=by_source[source].coverage_id if source in by_source else None,
            checked_at=by_source[source].checked_at if source in by_source else None,
        )
        for source in SOURCE_ORDER
    )
    return CoverageEvaluation(
        entries=entries,
        insufficiency_details=tuple(
            f"{entry.source_type}:{entry.status}"
            for entry in entries
            if entry.status != "available"
        ),
    )


async def load_coverage(
    session: AsyncSession, *, company_id: UUID, as_of_date: date
) -> CoverageEvaluation:
    rows = list(
        (
            await session.scalars(
                select(SourceCoverage)
                .where(
                    SourceCoverage.company_id == company_id,
                    SourceCoverage.as_of_date == as_of_date,
                )
                .order_by(SourceCoverage.source_type, SourceCoverage.id)
            )
        ).all()
    )
    return evaluate_coverage(
        [
            CoverageRecord(
                source_type=row.source_type,
                status=row.status,
                as_of_date=row.as_of_date,
                checked_at=row.checked_at,
                coverage_id=row.id,
            )
            for row in rows
        ],
        as_of_date=as_of_date,
    )
