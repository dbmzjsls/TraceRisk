from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tracerisk.ai.models import AIFinding
from tracerisk.auth.models import User
from tracerisk.auth.seed import DEMO_ACCOUNTS, seed_accounts
from tracerisk.companies.models import Company
from tracerisk.config import Settings
from tracerisk.evidence.models import (
    Evidence,
    EvidenceChunk,
    RetrievedEvidence,
    SourceCoverage,
)
from tracerisk.evidence.service import retrieve_evidence
from tracerisk.investigations.models import Investigation, WorkflowRun
from tracerisk.investigations.service import create_investigation
from tracerisk.main import app
from tracerisk.reports.models import RiskReport
from tracerisk.reports.service import generate_report
from tracerisk.retrieval.embeddings import FakeEmbedding
from tracerisk.retrieval.indexing import IndexContract, build_index
from tracerisk.reviews.models import ReviewDecision
from tracerisk.rules.models import RuleFinding, RuleFindingEvidence

pytestmark = pytest.mark.anyio
AI_CITATION = {
    "evidence_id": "21000000-0000-0000-0000-000000000001",
    "chunk_id": "00000000-0000-4000-8000-000000000006",
    "content_hash": "c" * 64,
    "quoted_excerpt": "Exact persisted excerpt",
}


async def freeze(session: AsyncSession, run_id: UUID) -> None:
    run = await session.get(WorkflowRun, run_id)
    assert run is not None
    investigation = await session.get(Investigation, run.investigation_id)
    assert investigation is not None
    session.add(
        User(
            id=investigation.created_by,
            username=str(run_id),
            password_hash="unused",
            role="analyst",
        )
    )
    await session.flush()
    await build_index(session, FakeEmbedding(), IndexContract.fake())
    await retrieve_evidence(session, run_id=run_id)


@pytest.mark.parametrize("decision", [None, "approve", "reject"])
async def test_report_paths(database: Settings, decision: str | None) -> None:
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            company = Company(
                synthetic_key="report",
                name="<script>company</script>",
                unified_credit_code="SYN-REPORT",
            )
            session.add(company)
            await session.flush()
            session.add_all(
                SourceCoverage(
                    company_id=company.id,
                    source_type=source_type,
                    as_of_date=date(2026, 9, 11),
                    status="available",
                    checked_at=datetime(2026, 9, 11, tzinfo=UTC),
                )
                for source_type in (
                    "business",
                    "judicial",
                    "news",
                    "intellectual_property",
                )
            )
            investigation = await create_investigation(
                session,
                company_id=company.id,
                created_by=uuid4(),
                as_of_date=date(2026, 9, 11),
            )
            run_id = investigation.current_run_id
            run = await session.get(WorkflowRun, run_id)
            assert run is not None
            run.status = "running"
            evidence = Evidence(
                company_id=company.id,
                source_type="judicial",
                risk_type="judicial",
                source_uri="synthetic://report",
                title="<script>",
                observed_at=date(2026, 9, 11),
                fact_type="judicial_case_status",
                fact_key="case",
                fact_value="open",
                polarity="negative",
                content="<script>evidence</script>",
                content_hash=sha256(b"<script>evidence</script>").hexdigest(),
            )
            session.add(evidence)
            await session.flush()
            await freeze(session, run_id)
            chunk = await session.scalar(
                select(EvidenceChunk)
                .join(RetrievedEvidence)
                .where(RetrievedEvidence.run_id == run_id)
            )
            assert chunk is not None
            rule = RuleFinding(
                run_id=run_id,
                rule_code="judicial",
                rule_version="1",
                score=35,
                parameters={},
            )
            session.add(rule)
            finding = AIFinding(
                run_id=run_id,
                finding_key="case",
                version=1,
                risk_type="judicial",
                claim_code="JUDICIAL_OPEN_CASE",
                title="<script>AI",
                summary="case",
                reasoning="<script>reasoning",
                citations=[AI_CITATION],
                evidence_sufficiency="sufficient",
                confidence_level="high",
                review_recommended=decision is not None,
            )
            session.add(finding)
            await session.flush()
            finding_id = finding.id
            rule_id, evidence_id = rule.id, evidence.id
            session.add(
                RuleFindingEvidence(
                    rule_finding_id=rule.id,
                    evidence_id=evidence.id,
                    content_hash=evidence.content_hash,
                )
            )
            second_id = None
            if decision is not None:
                second = AIFinding(
                    run_id=run_id,
                    finding_key="second",
                    version=1,
                    risk_type="judicial",
                    claim_code=finding.claim_code,
                    title="Second finding",
                    summary="case",
                    reasoning="case",
                    citations=[AI_CITATION],
                    evidence_sufficiency="sufficient",
                    confidence_level="high",
                    review_recommended=True,
                )
                session.add(second)
                await session.flush()
                second_id = second.id
        review_id = None
        if decision is not None:
            with pytest.raises(ValueError, match="complete human review"):
                await generate_report(sessions, run_id)
            async with sessions.begin() as session:
                review = ReviewDecision(
                    run_id=run_id,
                    ai_finding_id=finding_id,
                    finding_version=2,
                    reviewer_id=uuid4(),
                    idempotency_key="report",
                    decision=decision,
                    reason="<script>review</script>",
                )
                session.add(review)
                await session.flush()
                review_id = review.id
            with pytest.raises(ValueError, match="Stale"):
                await generate_report(sessions, run_id, review_id)
            async with sessions.begin() as session:
                saved_review = await session.get(ReviewDecision, review_id)
                assert saved_review is not None
                saved_review.finding_version = 1
            with pytest.raises(ValueError, match="complete human review"):
                await generate_report(sessions, run_id, review_id)
            async with sessions.begin() as session:
                session.add(
                    ReviewDecision(
                        run_id=run_id,
                        ai_finding_id=second_id,
                        finding_version=1,
                        reviewer_id=uuid4(),
                        idempotency_key="second-review",
                        decision=decision,
                        reason="Second decision",
                    )
                )
        with pytest.raises(ValueError, match="Invalid report review"):
            await generate_report(sessions, run_id, uuid4())
        report_id = await generate_report(sessions, run_id, review_id)
        async with sessions() as session:
            report = await session.get(RiskReport, report_id)
            run = await session.get(WorkflowRun, run_id)
            assert report is not None and run is not None
            snapshot = cast(dict[str, Any], report.snapshot)
            assert run.status == "completed"
            assert snapshot["snapshot_version"] == "stage6-v1"
            assert [
                entry["source_type"] for entry in snapshot["coverage"]["entries"]
            ] == ["business", "judicial", "news", "intellectual_property"]
            assert snapshot["conflicts"] == []
            assert snapshot["ai_findings"][0]["support_status"] == "indeterminate"
            assert snapshot["ai_findings"][0]["support_reason"] == "legacy-stage3"
            assert snapshot["ai_findings"][0]["citations"] == [AI_CITATION]
            if decision != "reject":
                assert snapshot["final_findings"][0]["citations"] == [AI_CITATION]
            assert report.snapshot["rule_score"] == 35
            assert report.snapshot["evidence_references"] == [
                {
                    "rule_finding_id": str(rule_id),
                    "evidence_id": str(evidence_id),
                    "content_hash": evidence.content_hash,
                    "chunk_id": str(chunk.id),
                    "chunk_content_hash": chunk.content_hash,
                    "title": "case",
                    "content": "<script>evidence</script>",
                }
            ]
            final = report.snapshot["final_findings"]
            assert isinstance(final, list)
            assert len(final) == (
                0 if decision == "reject" else 1 if decision is None else 2
            )
            assert "<script>" not in report.html_content
            before = (report.snapshot, report.html_content)
            count = await session.scalar(
                select(func.count()).select_from(ReviewDecision)
            )
            assert count == (0 if decision is None else 2)
        assert await generate_report(sessions, run_id, review_id) == report_id
        async with sessions() as session:
            report = await session.get(RiskReport, report_id)
            assert report is not None
            assert (report.snapshot, report.html_content) == before
            assert (
                await session.scalar(select(func.count()).select_from(RiskReport)) == 1
            )
    finally:
        await engine.dispose()


async def test_reports_api(database: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "JWT_SECRET", "report-test-signing-secret-at-least-48-characters"
    )
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as session:
            await seed_accounts(session)
        async with app.state.session_factory.begin() as session:
            investigation = await create_investigation(
                session,
                company_id=uuid4(),
                created_by=uuid4(),
                as_of_date=date(2026, 9, 11),
            )
            report = RiskReport(
                run_id=investigation.current_run_id,
                snapshot={"notice": "synthetic"},
                html_content="safe",
            )
            session.add(report)
            await session.flush()
            report_id = report.id
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            assert (await client.get(f"/reports/{report_id}")).status_code == 401
            for username, password, _role in DEMO_ACCOUNTS:
                login = await client.post(
                    "/auth/login",
                    json={"username": username, "password": password},
                )
                headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
                response = await client.get(f"/reports/{report_id}", headers=headers)
                assert response.status_code == 200
                assert response.json()["html_content"] == "safe"
                missing = await client.get(f"/reports/{uuid4()}", headers=headers)
                assert missing.status_code == 404


async def test_zero_finding_run_level_review(database: Settings) -> None:
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            investigation = await create_investigation(
                session,
                company_id=uuid4(),
                created_by=uuid4(),
                as_of_date=date(2026, 9, 11),
            )
            run_id = investigation.current_run_id
            run = await session.get(WorkflowRun, run_id)
            assert run is not None
            run.status = "running"
            await freeze(session, run_id)
        with pytest.raises(ValueError, match="complete human review"):
            await generate_report(sessions, run_id)
        async with sessions.begin() as session:
            review = ReviewDecision(
                run_id=run_id,
                ai_finding_id=None,
                finding_version=None,
                reviewer_id=uuid4(),
                idempotency_key="zero-findings",
                decision="reject",
                reason="Coverage is insufficient",
            )
            session.add(review)
            await session.flush()
            review_id = review.id
        report_id = await generate_report(sessions, run_id, review_id)
        async with sessions() as session:
            report = await session.get(RiskReport, report_id)
            assert report is not None
            assert report.snapshot["review_id"] == str(review_id)
            assert report.snapshot["decision"] == "reject"
            assert report.snapshot["reason"] == "Coverage is insufficient"
            assert report.snapshot["evidence_sufficiency"] == "insufficient"
            assert report.snapshot["final_findings"] == []
            assert report.snapshot["review_reasons"] == ["evidence_insufficient"]
    finally:
        await engine.dispose()


@pytest.mark.parametrize("failure", ["score_overflow", "transition_conflict"])
async def test_report_failure_rolls_back(database: Settings, failure: str) -> None:
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            investigation = await create_investigation(
                session,
                company_id=uuid4(),
                created_by=uuid4(),
                as_of_date=date(2026, 9, 11),
            )
            run_id = investigation.current_run_id
            run = await session.get(WorkflowRun, run_id)
            assert run is not None
            run.status = "running"
            await freeze(session, run_id)
            session.add(
                ReviewDecision(
                    run_id=run_id,
                    ai_finding_id=None,
                    finding_version=None,
                    reviewer_id=uuid4(),
                    idempotency_key="failure-review",
                    decision="approve",
                    reason="synthetic",
                )
            )
            if failure == "score_overflow":
                session.add_all(
                    RuleFinding(
                        run_id=run_id,
                        rule_code=code,
                        rule_version="1",
                        score=60,
                        parameters={},
                    )
                    for code in ("one", "two")
                )
            else:
                # Suppress the completion UPDATE in this fixture's isolated DB.
                await session.execute(
                    text("""
                    CREATE FUNCTION block_report_completion() RETURNS trigger
                    LANGUAGE plpgsql AS $$ BEGIN
                        IF NEW.status = 'completed' THEN RETURN NULL; END IF;
                        RETURN NEW;
                    END $$
                """)
                )
                await session.execute(
                    text("""
                    CREATE TRIGGER block_report_completion
                    BEFORE UPDATE ON workflow_runs FOR EACH ROW
                    EXECUTE FUNCTION block_report_completion()
                """)
                )
        message = (
            "Invalid rule score" if failure == "score_overflow" else "Run state changed"
        )
        with pytest.raises(ValueError, match=message):
            await generate_report(sessions, run_id)
        async with sessions() as session:
            run = await session.get(WorkflowRun, run_id)
            assert run is not None and run.status == "running"
            assert (
                await session.scalar(select(func.count()).select_from(RiskReport)) == 0
            )
    finally:
        await engine.dispose()
