"""Recovery acceptance through the real queue, graph, review and report services."""

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import date
from uuid import UUID

import procrastinate
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tracerisk.ai.models import AIFinding
from tracerisk.auth.models import User
from tracerisk.config import Settings
from tracerisk.evaluation.metrics import canonical_json
from tracerisk.investigations.models import DispatchOutbox, WorkflowEvent, WorkflowRun
from tracerisk.investigations.service import create_investigation
from tracerisk.reports.models import RiskReport
from tracerisk.retrieval.embeddings import FakeEmbedding
from tracerisk.retrieval.indexing import IndexContract, build_index
from tracerisk.reviews.models import ReviewDecision
from tracerisk.reviews.service import FindingReview, RunReview, submit_review
from tracerisk.synthetic.generator import generate_catalog
from tracerisk.synthetic.importer import import_catalog
from tracerisk.workflows import outbox, worker
from tracerisk.workflows.queue import enqueue_run
from tracerisk.workflows.spike_graph import execute_workflow, initialize_checkpoints

pytestmark = pytest.mark.anyio
REVIEWER = UUID("30000000-0000-0000-0000-000000000011")
Resources = tuple[Settings, async_sessionmaker[AsyncSession], procrastinate.App, UUID]


@pytest.fixture
async def resources(database: Settings) -> AsyncIterator[Resources]:
    await initialize_checkpoints()
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        scenario = next(s for s in generate_catalog() if s.key == "existing-judicial")
        async with sessions.begin() as session:
            await import_catalog(session, generate_catalog())
            await build_index(session, FakeEmbedding(), IndexContract.fake())
            session.add_all(
                [
                    User(
                        id=REVIEWER,
                        username="evaluation-reviewer",
                        password_hash="disabled",
                        role="reviewer",
                    ),
                    User(
                        username="evaluation-analyst",
                        password_hash="disabled",
                        role="analyst",
                    ),
                ]
            )
            await session.flush()
            analyst = await session.scalar(
                select(User).where(User.username == "evaluation-analyst")
            )
            assert analyst is not None
            investigation = await create_investigation(
                session,
                company_id=scenario.company.id,
                created_by=analyst.id,
                as_of_date=date(2026, 9, 11),
            )
            run_id = investigation.current_run_id
        async with worker.create_worker(database, sessions).open_async() as app:
            await app.schema_manager.apply_schema_async()
            yield database, sessions, app, run_id
    finally:
        await engine.dispose()


async def _consume(app: procrastinate.App) -> None:
    await app.run_worker_async(
        queues=["tracerisk"], wait=False, install_signal_handlers=False
    )


async def _new_worker() -> int:
    script = """
import asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tracerisk.config import Settings
from tracerisk.workflows.worker import create_worker
async def main():
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    try:
        queue = create_worker(settings, async_sessionmaker(engine))
        async with queue.open_async() as app:
            await app.run_worker_async(queues=['tracerisk'], wait=False,
                                       install_signal_handlers=False)
    finally:
        await engine.dispose()
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
    runner.run(main())
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script],
        env=os.environ.copy(),
        capture_output=True,
        timeout=30,
    )
    return result.returncode


async def test_redelivery_retry_restart_finding_review_and_immutable_report(
    resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, sessions, app, run_id = resources
    original_enqueue = outbox.enqueue_run

    async def lose_ack(
        queue: procrastinate.App, identity: UUID, operation: str
    ) -> None:
        await original_enqueue(queue, identity, operation)
        raise RuntimeError("ack gap")

    with monkeypatch.context() as patch:
        patch.setattr(outbox, "enqueue_run", lose_ack)
        with pytest.raises(RuntimeError, match="ack gap"):
            await outbox.dispatch_once(sessions, app)
    assert await outbox.dispatch_once(sessions, app) == 1
    assert await outbox.dispatch_once(sessions, app) == 0

    original_execute = worker.execute_workflow
    attempts = 0

    async def transient(
        configuration: Settings,
        factory: async_sessionmaker[AsyncSession],
        identity: UUID,
        operation: str,
    ) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("one transient worker failure")
        await original_execute(configuration, factory, identity, operation)

    with monkeypatch.context() as patch:
        patch.setattr(worker, "execute_workflow", transient)
        await _consume(app)
        await asyncio.sleep(1.05)
        await _consume(app)
    assert attempts >= 2
    async with sessions() as session:
        assert (
            await session.scalar(
                select(WorkflowRun.status).where(WorkflowRun.id == run_id)
            )
            == "awaiting_review"
        )
        finding = await session.scalar(
            select(AIFinding).where(AIFinding.run_id == run_id)
        )
        assert finding is not None
    request = FindingReview(
        run_id=run_id,
        reviewer_id=REVIEWER,
        idempotency_key="finding-review-once",
        decision="approve",
        reason="verified synthetic evidence",
        ai_finding_id=finding.id,
        finding_version=finding.version,
    )
    review_id = await submit_review(sessions, request)
    assert await submit_review(sessions, request) == review_id
    assert await outbox.dispatch_once(sessions, app) == 1
    await enqueue_run(app, run_id, "resume")
    assert await _new_worker() == 0
    async with sessions() as session:
        assert (
            await session.scalar(
                select(WorkflowRun.status).where(WorkflowRun.id == run_id)
            )
            == "completed"
        )
        report = await session.scalar(
            select(RiskReport).where(RiskReport.run_id == run_id)
        )
        assert report is not None
        original_bytes = canonical_json(report.snapshot) + report.html_content.encode()
    assert await submit_review(sessions, request) == review_id
    await enqueue_run(app, run_id, "resume")
    await _consume(app)
    await execute_workflow(settings, sessions, run_id, "resume")
    async with sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ReviewDecision)) == 1
        )
        assert await session.scalar(select(func.count()).select_from(RiskReport)) == 1
        outboxes = (
            await session.scalars(
                select(DispatchOutbox.operation).where(DispatchOutbox.run_id == run_id)
            )
        ).all()
        assert sorted(outboxes) == ["resume", "start"]
        terminal = (
            await session.scalars(
                select(WorkflowEvent).where(
                    WorkflowEvent.run_id == run_id,
                    WorkflowEvent.event_type == "run.completed",
                )
            )
        ).all()
        assert len(terminal) == 1
        report = await session.scalar(
            select(RiskReport).where(RiskReport.run_id == run_id)
        )
        assert report is not None
        assert (
            canonical_json(report.snapshot) + report.html_content.encode()
            == original_bytes
        )


async def test_duplicate_run_level_review_and_resume(resources: Resources) -> None:
    settings, sessions, app, _ = resources
    async with sessions.begin() as session:
        analyst = await session.scalar(
            select(User).where(User.username == "evaluation-analyst")
        )
        assert analyst is not None
        company = next(s for s in generate_catalog() if s.key == "unavailable-source")
        investigation = await create_investigation(
            session,
            company_id=company.company.id,
            created_by=analyst.id,
            as_of_date=date(2026, 9, 11),
        )
        run_id = investigation.current_run_id
    assert await outbox.dispatch_once(sessions, app) == 2
    await _consume(app)
    async with sessions() as session:
        assert (
            await session.scalar(
                select(WorkflowRun.status).where(WorkflowRun.id == run_id)
            )
            == "awaiting_review"
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AIFinding)
                .where(AIFinding.run_id == run_id)
            )
            == 0
        )
    request = RunReview(
        run_id=run_id,
        reviewer_id=REVIEWER,
        idempotency_key="run-review-once",
        decision="approve",
        reason="source unavailable; manual decision",
    )
    first, second = await asyncio.gather(
        submit_review(sessions, request), submit_review(sessions, request)
    )
    assert first == second
    assert await outbox.dispatch_once(sessions, app) == 1
    await asyncio.gather(
        enqueue_run(app, run_id, "resume"), enqueue_run(app, run_id, "resume")
    )
    await _consume(app)
    await execute_workflow(settings, sessions, run_id, "resume")
    async with sessions() as session:
        assert (
            await session.scalar(
                select(WorkflowRun.status).where(WorkflowRun.id == run_id)
            )
            == "completed"
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ReviewDecision)
                .where(ReviewDecision.run_id == run_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RiskReport)
                .where(RiskReport.run_id == run_id)
            )
            == 1
        )
        outboxes = (
            await session.scalars(
                select(DispatchOutbox.operation).where(DispatchOutbox.run_id == run_id)
            )
        ).all()
        assert sorted(outboxes) == ["resume", "start"]
        assert (
            await session.scalar(
                select(func.count())
                .select_from(WorkflowEvent)
                .where(
                    WorkflowEvent.run_id == run_id,
                    WorkflowEvent.event_type == "run.completed",
                )
            )
            == 1
        )
