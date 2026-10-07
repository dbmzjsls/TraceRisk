import asyncio
import hashlib
import sys
from datetime import UTC, date, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tracerisk.companies.models import Company
from tracerisk.config import Settings
from tracerisk.evidence.models import Evidence, SourceCoverage
from tracerisk.rules.engine import evaluate_judicial_rule

COMPANY_ID = UUID("20000000-0000-0000-0000-000000000001")
COMPANY_KEY = "judicial-open-case-story"

EVIDENCE_ROWS = (
    (
        UUID("21000000-0000-0000-0000-000000000001"),
        "synthetic://judicial/case-001/notice-a",
        "（2026）合成执字第001号立案信息",
        date(2026, 8, 10),
        "SYN-CASE-001",
        "合成司法来源显示：（2026）合成执字第001号案件状态为未结案。",
    ),
    (
        UUID("21000000-0000-0000-0000-000000000002"),
        "synthetic://judicial/case-001/notice-b",
        "（2026）合成执字第001号状态复核",
        date(2026, 8, 12),
        "SYN-CASE-001",
        "合成司法来源再次确认：（2026）合成执字第001号案件仍为未结案。",
    ),
    (
        UUID("21000000-0000-0000-0000-000000000003"),
        "synthetic://judicial/case-002/notice",
        "（2026）合成执字第002号立案信息",
        date(2026, 8, 15),
        "SYN-CASE-002",
        "合成司法来源显示：（2026）合成执字第002号案件状态为未结案。",
    ),
)


async def seed_synthetic(session: AsyncSession) -> None:
    await session.execute(
        insert(Company)
        .values(
            id=COMPANY_ID,
            synthetic_key=COMPANY_KEY,
            name="华辰智造（合成演示）有限公司",
            unified_credit_code="SYNTHETIC-COMPANY-001",
            is_synthetic=True,
        )
        .on_conflict_do_nothing()
    )
    for (
        evidence_id,
        source_uri,
        title,
        observed_at,
        case_number,
        content,
    ) in EVIDENCE_ROWS:
        await session.execute(
            insert(Evidence)
            .values(
                id=evidence_id,
                company_id=COMPANY_ID,
                source_type="judicial",
                risk_type="judicial",
                source_uri=source_uri,
                title=title,
                observed_at=observed_at,
                fact_type="judicial_case_status",
                fact_key=case_number,
                fact_value="open",
                polarity="negative",
                content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )
            .on_conflict_do_nothing()
        )
    for source_type in (
        "business",
        "judicial",
        "news",
        "intellectual_property",
    ):
        await session.execute(
            insert(SourceCoverage)
            .values(
                id=uuid5(
                    NAMESPACE_URL,
                    f"tracerisk:legacy-coverage:{COMPANY_ID}:{source_type}:2026-09-11",
                ),
                company_id=COMPANY_ID,
                source_type=source_type,
                as_of_date=date(2026, 9, 11),
                status="available",
                checked_at=datetime(2026, 9, 11, tzinfo=UTC),
            )
            .on_conflict_do_nothing()
        )
    await session.commit()
    persisted = (
        await session.scalars(
            select(Evidence).where(Evidence.id.in_([row[0] for row in EVIDENCE_ROWS]))
        )
    ).all()
    if len(persisted) != len(EVIDENCE_ROWS):
        raise RuntimeError("Synthetic Evidence rows are missing")
    expected_cases = {row[0]: row[4] for row in EVIDENCE_ROWS}
    for evidence in persisted:
        if (
            evidence.company_id != COMPANY_ID
            or evidence.source_type != "judicial"
            or evidence.risk_type != "judicial"
            or evidence.fact_type != "judicial_case_status"
            or evidence.fact_key != expected_cases[evidence.id]
            or evidence.fact_value != "open"
            or evidence.polarity != "negative"
            or not evidence.content.strip()
            or evidence.content_hash
            != hashlib.sha256(evidence.content.encode("utf-8")).hexdigest()
        ):
            raise RuntimeError("Persisted synthetic Evidence is not Chunk-ready")


async def main() -> None:
    engine = create_async_engine(Settings().database_url)
    try:
        async with async_sessionmaker(engine)() as session:
            await seed_synthetic(session)
            evaluation = await evaluate_judicial_rule(
                session, company_id=COMPANY_ID, as_of_date=date(2026, 9, 11)
            )
        if evaluation.score != 35 or len(evaluation.matched_fact_keys) != 2:
            raise RuntimeError("Synthetic judicial story failed its rule expectation")
        print(
            "Synthetic judicial story initialized: 2 open cases, "
            "one 35-point rule match; 3 persisted Evidence rows are Chunk-ready; "
            "existing records preserved."
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    with asyncio.Runner(
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
    ) as runner:
        runner.run(main())
