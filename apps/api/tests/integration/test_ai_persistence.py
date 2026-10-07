from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tracerisk.ai.models import AIFinding
from tracerisk.companies.models import Company
from tracerisk.config import Settings
from tracerisk.evidence.models import Evidence, EvidenceChunk, RetrievedEvidence
from tracerisk.investigations.service import create_investigation
from tracerisk.reports.models import RiskReport
from tracerisk.reviews.models import ReviewDecision

pytestmark = pytest.mark.anyio


async def test_run_scoped_ai_and_report_persistence(database: Settings) -> None:
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    observed_at = date(2026, 9, 11)
    evidence_hash = "a" * 64

    try:
        async with sessions.begin() as session:
            company = Company(
                synthetic_key="ai-persistence-company",
                name="AI persistence synthetic company",
                unified_credit_code="SYNTHETIC-AI-PERSISTENCE-001",
            )
            other_company = Company(
                synthetic_key="other-ai-persistence-company",
                name="Other synthetic company",
                unified_credit_code="SYNTHETIC-AI-PERSISTENCE-002",
            )
            session.add_all([company, other_company])
            await session.flush()
            evidence = Evidence(
                company_id=company.id,
                source_type="judicial",
                risk_type="judicial",
                source_uri="synthetic://ai-persistence/case-1",
                title="Open synthetic case",
                observed_at=observed_at,
                fact_type="judicial_case_status",
                fact_key="SYN-CASE-1",
                fact_value="open",
                polarity="negative",
                content="Synthetic case SYN-CASE-1 remains open.",
                content_hash=evidence_hash,
            )
            session.add(evidence)
            investigation = await create_investigation(
                session,
                company_id=company.id,
                created_by=uuid4(),
                as_of_date=observed_at,
            )
            other_investigation = await create_investigation(
                session,
                company_id=company.id,
                created_by=uuid4(),
                as_of_date=observed_at,
            )
            await session.flush()
            run_id = investigation.current_run_id
            other_run_id = other_investigation.current_run_id
            company_id = company.id
            other_company_id = other_company.id
            evidence_id = evidence.id

            chunk = EvidenceChunk(
                evidence_id=evidence.id,
                source_type=evidence.source_type,
                risk_type=evidence.risk_type,
                evidence_content_hash=evidence.content_hash,
                chunk_schema_version="legacy-stage3",
                company_id=company.id,
                observed_at=observed_at,
                fact_type=evidence.fact_type,
                fact_key=evidence.fact_key,
                fact_value=evidence.fact_value,
                polarity=evidence.polarity,
                content=evidence.content,
                content_hash=evidence.content_hash,
            )
            session.add(chunk)
            await session.flush()
            chunk_id = chunk.id

            retrieved = RetrievedEvidence(
                run_id=run_id,
                risk_type="judicial",
                chunk_id=chunk.id,
                rank=1,
            )
            finding = AIFinding(
                run_id=run_id,
                finding_key="judicial-open-case",
                version=1,
                risk_type="judicial",
                claim_code="JUDICIAL_OPEN_CASE",
                title="Open judicial case",
                summary="One open synthetic judicial case was found.",
                reasoning="The retrieved evidence records an open case.",
                citations=[
                    {
                        "evidence_id": str(evidence.id),
                        "chunk_id": str(chunk.id),
                        "content_hash": evidence.content_hash,
                        "quoted_excerpt": evidence.content,
                    }
                ],
                evidence_sufficiency="sufficient",
                confidence_level="high",
                review_recommended=True,
            )
            session.add_all([retrieved, finding])
            await session.flush()

            review = ReviewDecision(
                run_id=run_id,
                ai_finding_id=finding.id,
                finding_version=finding.version,
                reviewer_id=uuid4(),
                idempotency_key="ai-persistence-review",
                decision="approve",
                reason="The citation matches the stored evidence.",
            )
            report = RiskReport(
                run_id=run_id,
                snapshot={"run_id": str(run_id)},
                html_content="<html><body>Synthetic report</body></html>",
            )
            session.add_all([review, report])
            await session.flush()

            assert retrieved.run_id == run_id
            assert finding.version == 1
            assert review.ai_finding_id == finding.id
            assert report.run_id == run_id

        await _assert_integrity_error(
            sessions,
            RetrievedEvidence(
                run_id=run_id,
                risk_type="judicial",
                chunk_id=chunk_id,
                rank=2,
            ),
        )
        await _assert_integrity_error(
            sessions,
            AIFinding(
                run_id=run_id,
                finding_key="judicial-open-case",
                version=2,
                risk_type="judicial",
                claim_code="JUDICIAL_OPEN_CASE",
                title="Duplicate finding",
                summary="Duplicate finding for the same immutable key.",
                reasoning="This row must be rejected.",
                citations=[],
                evidence_sufficiency="sufficient",
                confidence_level="high",
                review_recommended=True,
            ),
        )
        await _assert_invalid_chunk(
            sessions,
            evidence_id=evidence_id,
            company_id=other_company_id,
            content_hash=evidence_hash,
            observed_at=observed_at,
        )
        await _assert_invalid_chunk(
            sessions,
            evidence_id=evidence_id,
            company_id=company_id,
            content_hash="b" * 64,
            observed_at=observed_at,
        )
        await _assert_integrity_error(
            sessions,
            RiskReport(
                run_id=run_id,
                snapshot={"run_id": str(run_id), "duplicate": True},
                html_content="<html><body>Duplicate report</body></html>",
            ),
        )
        await _assert_integrity_error(
            sessions,
            ReviewDecision(
                run_id=run_id,
                ai_finding_id=uuid4(),
                finding_version=1,
                reviewer_id=uuid4(),
                idempotency_key="missing-finding-review",
                decision="approve",
                reason="A missing AI finding must not be reviewable.",
            ),
        )
        await _assert_integrity_error(
            sessions,
            ReviewDecision(
                run_id=other_run_id,
                ai_finding_id=finding.id,
                finding_version=finding.version,
                reviewer_id=uuid4(),
                idempotency_key="cross-run-finding-review",
                decision="approve",
                reason="A Run must not review another Run's finding.",
            ),
        )
        async with sessions.begin() as session:
            run_level_review = ReviewDecision(
                run_id=other_run_id,
                ai_finding_id=None,
                finding_version=None,
                reviewer_id=uuid4(),
                idempotency_key="run-level-review",
                decision="approve",
                reason="Zero-finding Run retains run-level review support.",
            )
            session.add(run_level_review)
            await session.flush()
            assert run_level_review.ai_finding_id is None
            assert run_level_review.finding_version is None
    finally:
        await engine.dispose()


async def _assert_invalid_chunk(
    sessions: async_sessionmaker[AsyncSession],
    *,
    evidence_id: UUID,
    company_id: UUID,
    content_hash: str,
    observed_at: date,
) -> None:
    await _assert_integrity_error(
        sessions,
        EvidenceChunk(
            source_type="judicial",
            risk_type="judicial",
            evidence_content_hash=content_hash,
            chunk_schema_version="legacy-stage3",
            evidence_id=evidence_id,
            company_id=company_id,
            observed_at=observed_at,
            fact_type="judicial_case_status",
            fact_key=str(uuid4()),
            fact_value="open",
            polarity="negative",
            content="Mismatched evidence chunk.",
            content_hash=content_hash,
        ),
    )


async def _assert_integrity_error(
    sessions: async_sessionmaker[AsyncSession], row: object
) -> None:
    with pytest.raises(IntegrityError):
        async with sessions.begin() as session:
            session.add(row)
            await session.flush()
