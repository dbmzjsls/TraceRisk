from datetime import date
from hashlib import sha256
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio.session import AsyncSession

from tracerisk.ai.models import AIFinding
from tracerisk.auth.models import User
from tracerisk.auth.seed import seed_accounts
from tracerisk.config import Settings
from tracerisk.evidence.models import Evidence
from tracerisk.investigations.models import DispatchOutbox, WorkflowRun
from tracerisk.investigations.service import create_investigation
from tracerisk.reports.models import RiskReport
from tracerisk.retrieval.embeddings import FakeEmbedding
from tracerisk.retrieval.indexing import IndexContract, build_index
from tracerisk.reviews.models import ReviewDecision
from tracerisk.reviews.service import FindingReview, RunReview, submit_review
from tracerisk.rules.engine import evaluate_all_rules
from tracerisk.rules.models import RuleFinding
from tracerisk.synthetic.generator import generate_catalog
from tracerisk.synthetic.importer import import_catalog
from tracerisk.workflows.spike_graph import execute_workflow, initialize_checkpoints

pytestmark = pytest.mark.anyio
BASELINE = date(2026, 9, 11)


async def _new_run(
    sessions: async_sessionmaker[AsyncSession], company_id: UUID
) -> tuple[UUID, UUID, UUID]:
    async with sessions.begin() as session:
        creator = await session.scalar(
            select(User.id).where(User.username == "analyst")
        )
        reviewer = await session.scalar(
            select(User.id).where(User.username == "reviewer")
        )
        assert creator is not None and reviewer is not None
        investigation = await create_investigation(
            session,
            company_id=company_id,
            created_by=creator,
            as_of_date=BASELINE,
        )
        return investigation.current_run_id, creator, reviewer


async def _approve_current_findings(
    sessions: async_sessionmaker[AsyncSession],
    run_id: UUID,
    reviewer: UUID,
    key: str,
) -> None:
    async with sessions() as session:
        findings = list(
            (
                await session.scalars(
                    select(AIFinding)
                    .where(AIFinding.run_id == run_id)
                    .order_by(AIFinding.finding_key)
                )
            ).all()
        )
    assert findings
    for index, finding in enumerate(findings):
        await submit_review(
            sessions,
            FindingReview(
                run_id=run_id,
                reviewer_id=reviewer,
                idempotency_key=f"{key}-{index}",
                decision="approve",
                reason="synthetic verification",
                ai_finding_id=finding.id,
                finding_version=finding.version,
            ),
        )


async def test_stage3_workflow_matrix_and_zero_ai_review_replay(
    database: Settings,
) -> None:
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    catalog = {scenario.key: scenario for scenario in generate_catalog()}
    try:
        await initialize_checkpoints()
        async with sessions() as session:
            await seed_accounts(session)
        async with sessions.begin() as session:
            await import_catalog(session, tuple(catalog.values()))
            await build_index(session, FakeEmbedding(), IndexContract.fake())

        clean_id = catalog["clean-operations"].company.id
        clean_run, _, _ = await _new_run(sessions, clean_id)
        await execute_workflow(database, sessions, clean_run, "start")
        async with sessions() as session:
            clean_report = await session.scalar(
                select(RiskReport).where(RiskReport.run_id == clean_run)
            )
            assert clean_report is not None
            clean_snapshot = cast(dict[str, Any], clean_report.snapshot)
            assert clean_snapshot["rule_score"] == 0
            assert clean_snapshot["review_reasons"] == []
            assert clean_snapshot["coverage"]["insufficiency_details"] == []

        missing_id = catalog["unavailable-source"].company.id
        missing_run, _, reviewer = await _new_run(sessions, missing_id)
        await execute_workflow(database, sessions, missing_run, "start")
        async with sessions() as session:
            run = await session.get(WorkflowRun, missing_run)
            assert run is not None and run.status == "awaiting_review"
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AIFinding)
                    .where(AIFinding.run_id == missing_run)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.coalesce(func.sum(RuleFinding.score), 0)).where(
                        RuleFinding.run_id == missing_run
                    )
                )
                == 0
            )
        request = RunReview(
            run_id=missing_run,
            reviewer_id=reviewer,
            idempotency_key="missing-coverage-review",
            decision="approve",
            reason="coverage limitation accepted",
        )
        decision_id = await submit_review(sessions, request)
        await execute_workflow(database, sessions, missing_run, "resume")
        assert await submit_review(sessions, request) == decision_id
        async with sessions() as session:
            report = await session.scalar(
                select(RiskReport).where(RiskReport.run_id == missing_run)
            )
            assert report is not None
            missing_snapshot = cast(dict[str, Any], report.snapshot)
            assert missing_snapshot["rule_score"] == 0
            assert missing_snapshot["review_reasons"] == ["evidence_insufficient"]
            assert missing_snapshot["coverage"]["insufficiency_details"] == [
                "business:unavailable",
                "intellectual_property:stale",
            ]
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ReviewDecision)
                    .where(ReviewDecision.run_id == missing_run)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(DispatchOutbox)
                    .where(
                        DispatchOutbox.run_id == missing_run,
                        DispatchOutbox.operation == "resume",
                    )
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(RiskReport)
                    .where(RiskReport.run_id == missing_run)
                )
                == 1
            )

        conflict_id = catalog["cross-source-conflict"].company.id
        conflict_run, _, conflict_reviewer = await _new_run(sessions, conflict_id)
        await execute_workflow(database, sessions, conflict_run, "start")
        await _approve_current_findings(
            sessions, conflict_run, conflict_reviewer, "conflict"
        )
        await execute_workflow(database, sessions, conflict_run, "resume")
        async with sessions() as session:
            report = await session.scalar(
                select(RiskReport).where(RiskReport.run_id == conflict_run)
            )
            assert report is not None and report.snapshot["rule_score"] == 35
            assert "conflict" in report.snapshot["review_reasons"]
            conflict = report.snapshot["conflicts"][0]
            assert conflict["authority_source"] == "judicial"
            assert conflict["resolved_value"] == "open"
            assert len(conflict["sides"]) == 2

        full_id = catalog["all-rules-95"].company.id
        full_run, _, full_reviewer = await _new_run(sessions, full_id)
        await execute_workflow(database, sessions, full_run, "start")
        await _approve_current_findings(sessions, full_run, full_reviewer, "full")
        await execute_workflow(database, sessions, full_run, "resume")
        async with sessions() as session:
            report = await session.scalar(
                select(RiskReport).where(RiskReport.run_id == full_run)
            )
            assert report is not None and report.snapshot["rule_score"] == 95
            assert {
                item["risk_type"] for item in report.snapshot["retrieved_evidence"]
            } == {
                "business",
                "judicial",
                "news",
                "intellectual_property",
            }

        injection = catalog["prompt-injection"]
        injection_run, _, _ = await _new_run(sessions, injection.company.id)
        await execute_workflow(database, sessions, injection_run, "start")
        async with sessions() as session:
            report = await session.scalar(
                select(RiskReport).where(RiskReport.run_id == injection_run)
            )
            assert report is not None
            assert report.snapshot["rule_score"] == 0
            assert report.snapshot["review_reasons"] == []
            assert "忽略此前规则" in report.snapshot["retrieved_evidence"][0]["content"]
    finally:
        await engine.dispose()


async def test_unresolved_conflict_excludes_only_its_fact(database: Settings) -> None:
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    catalog = generate_catalog()
    company = catalog[1].company
    try:
        await initialize_checkpoints()
        async with sessions() as session:
            await seed_accounts(session)
        async with sessions.begin() as session:
            await import_catalog(session, catalog)
            for serial, source, key, value, polarity in (
                (1, "business", "CONFLICT", "negative", "negative"),
                (2, "judicial", "CONFLICT", "positive", "positive"),
                (3, "news", "SAFE-1", "negative", "negative"),
                (4, "news", "SAFE-2", "negative", "negative"),
            ):
                content = f"news-{serial}"
                session.add(
                    Evidence(
                        company_id=company.id,
                        source_type=source,
                        risk_type="news",
                        source_uri=f"synthetic://unresolved/{serial}",
                        title=content,
                        observed_at=BASELINE,
                        fact_type="negative_news_story",
                        fact_key=key,
                        fact_value=value,
                        polarity=polarity,
                        content=content,
                        content_hash=sha256(content.encode()).hexdigest(),
                    )
                )
        async with sessions.begin() as session:
            await build_index(session, FakeEmbedding(), IndexContract.fake())
        async with sessions() as session:
            score = await evaluate_all_rules(
                session, company_id=company.id, as_of_date=BASELINE
            )
            assert score.total_score == 20
            news = next(
                item
                for item in score.evaluations
                if item.rule_code == "NEGATIVE_NEWS_V1"
            )
            assert news.matched_fact_keys == ("SAFE-1", "SAFE-2")
        run_id, _, reviewer = await _new_run(sessions, company.id)
        await execute_workflow(database, sessions, run_id, "start")
        await submit_review(
            sessions,
            RunReview(
                run_id=run_id,
                reviewer_id=reviewer,
                idempotency_key="unresolved-conflict",
                decision="approve",
                reason="conflict accepted",
            ),
        )
        await execute_workflow(database, sessions, run_id, "resume")
        async with sessions() as session:
            report = await session.scalar(
                select(RiskReport).where(RiskReport.run_id == run_id)
            )
            assert report is not None
            snapshot = cast(dict[str, Any], report.snapshot)
            assert snapshot["rule_score"] == 20
            assert snapshot["review_reasons"] == [
                "evidence_insufficient",
                "conflict",
            ]
    finally:
        await engine.dispose()
