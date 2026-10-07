import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import procrastinate
import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tracerisk.ai.mock_adapter import MockModelAdapter
from tracerisk.ai.models import AIFinding
from tracerisk.ai.schemas import RiskContext, StructuredRiskFindings
from tracerisk.ai.service import generate_validate_findings
from tracerisk.auth.models import User
from tracerisk.companies.models import Company
from tracerisk.config import Settings
from tracerisk.evidence.models import (
    Evidence,
    RetrievedEvidence,
    SourceCoverage,
)
from tracerisk.evidence.service import build_risk_context, retrieve_evidence
from tracerisk.investigations.models import (
    DispatchOutbox,
    Investigation,
    WorkflowRun,
    WorkflowStep,
)
from tracerisk.investigations.service import create_investigation
from tracerisk.reports.models import RiskReport
from tracerisk.retrieval.embeddings import FakeEmbedding
from tracerisk.retrieval.indexing import IndexContract, build_index
from tracerisk.reviews.models import ReviewDecision
from tracerisk.reviews.service import ReviewConflict, RunReview, submit_review
from tracerisk.workflows.outbox import dispatch_once, recover_stalled
from tracerisk.workflows.queue import enqueue_run
from tracerisk.workflows.spike_graph import (
    SpikeState,
    build_graph,
    execute_workflow,
    initialize_checkpoints,
)
from tracerisk.workflows.worker import create_worker


@pytest.mark.parametrize("empty", [True, False])
async def test_frozen_retrieval_baseline_and_latest(
    resources: "Resources", empty: bool
) -> None:
    from hashlib import sha256

    settings, sessions, _ = resources
    async with sessions.begin() as session:
        company = Company(
            synthetic_key="pipeline",
            name="Pipeline",
            unified_credit_code="SYNTHETIC-PIPELINE",
        )
        user = User(username="pipeline", password_hash="unused", role="analyst")
        session.add_all([company, user])
        await session.flush()
        investigation = await create_investigation(
            session,
            company_id=company.id,
            created_by=user.id,
            as_of_date=date(2026, 9, 11),
        )
        run_id = investigation.current_run_id
        await session.execute(
            update(WorkflowRun).where(WorkflowRun.id == run_id).values(status="running")
        )

    async def add_fact(key: str, status: str, day: int) -> None:
        content = f"{key} {status} on {day}"
        async with sessions.begin() as session:
            session.add(
                Evidence(
                    company_id=company.id,
                    source_type="judicial",
                    risk_type="judicial",
                    source_uri=f"synthetic://pipeline/{key}/{day}",
                    title=key,
                    observed_at=date(2026, 9, day),
                    fact_type="judicial_case_status",
                    fact_key=key,
                    fact_value=status,
                    polarity="negative" if status == "open" else "neutral",
                    content=content,
                    content_hash=sha256(content.encode()).hexdigest(),
                )
            )

    if not empty:
        await add_fact("closed-case", "open", 9)
        await add_fact("closed-case", "closed", 10)
        await add_fact("open-case", "open", 11)
        await add_fact("future-case", "open", 12)
    async with sessions.begin() as session:
        await build_index(session, FakeEmbedding(), IndexContract.fake())
    async with sessions.begin() as session:
        await retrieve_evidence(session, run_id=run_id)
        first = await build_risk_context(session, run_id=run_id)
    assert sorted((c.fact_key, c.fact_value) for c in first.evidence_chunks) == (
        []
        if empty
        else [("closed-case", "closed"), ("closed-case", "open"), ("open-case", "open")]
    )
    await add_fact("late-insert", "open", 8)
    async with sessions.begin() as session:
        await retrieve_evidence(session, run_id=run_id)
        assert await build_risk_context(session, run_id=run_id) == first
    await generate_validate_findings(sessions, run_id=run_id)
    # Completion freezes zero output as well as nonzero output.
    await generate_validate_findings(sessions, run_id=run_id)
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(AIFinding)) == (
            0 if empty else 1
        )
    await execute_workflow(settings, sessions, run_id, "start")
    async with sessions() as session:
        assert (
            await session.scalar(
                select(WorkflowRun.status).where(WorkflowRun.id == run_id)
            )
            == "awaiting_review"
        )
        from tracerisk.rules.models import RuleFinding

        assert sum(
            (
                await session.scalars(
                    select(RuleFinding.score).where(RuleFinding.run_id == run_id)
                )
            ).all()
        ) == (0 if empty else 35)


@pytest.mark.parametrize("damage", ["delete_tail", "delete_all", "change_rank"])
async def test_retrieval_manifest_rejects_incomplete_saved_set(
    resources: "Resources", damage: str
) -> None:
    from hashlib import sha256

    _, sessions, _ = resources
    async with sessions.begin() as session:
        company = Company(
            synthetic_key=f"manifest-{damage}",
            name="Manifest",
            unified_credit_code=f"SYNTHETIC-MANIFEST-{damage}",
        )
        user = User(
            username=f"manifest-{damage}", password_hash="unused", role="analyst"
        )
        session.add_all([company, user])
        await session.flush()
        investigation = await create_investigation(
            session,
            company_id=company.id,
            created_by=user.id,
            as_of_date=date(2026, 9, 11),
        )
        run_id = investigation.current_run_id
        await session.execute(
            update(WorkflowRun).where(WorkflowRun.id == run_id).values(status="running")
        )
        for index in (1, 2):
            content = f"manifest-{index}"
            session.add(
                Evidence(
                    company_id=company.id,
                    source_type="judicial",
                    risk_type="judicial",
                    source_uri=f"synthetic://manifest/{damage}/{index}",
                    title=content,
                    observed_at=date(2026, 9, 10),
                    fact_type="judicial_case_status",
                    fact_key=f"CASE-{index}",
                    fact_value="closed",
                    polarity="neutral",
                    content=content,
                    content_hash=sha256(content.encode()).hexdigest(),
                )
            )
    async with sessions.begin() as session:
        await build_index(session, FakeEmbedding(), IndexContract.fake())
        await retrieve_evidence(session, run_id=run_id)
    async with sessions.begin() as session:
        if damage == "delete_all":
            await session.execute(
                delete(RetrievedEvidence).where(RetrievedEvidence.run_id == run_id)
            )
        else:
            rows = list(
                (
                    await session.scalars(
                        select(RetrievedEvidence)
                        .where(RetrievedEvidence.run_id == run_id)
                        .order_by(RetrievedEvidence.rank)
                    )
                ).all()
            )
            assert len(rows) == 2
            if damage == "delete_tail":
                await session.delete(rows[-1])
            else:
                rows[0].rank = 3
    async with sessions.begin() as session:
        with pytest.raises(ValueError, match="retrieval|Retrieval"):
            await retrieve_evidence(session, run_id=run_id)


async def test_graph_rules_use_frozen_retrieval_when_fact_arrives_between_nodes(
    resources: "Resources",
) -> None:
    from hashlib import sha256

    settings, sessions, _ = resources
    async with sessions.begin() as session:
        company = Company(
            synthetic_key="between-nodes",
            name="Between Nodes",
            unified_credit_code="SYNTHETIC-BETWEEN-NODES",
        )
        user = User(username="between-nodes", password_hash="unused", role="analyst")
        session.add_all([company, user])
        await session.flush()
        investigation = await create_investigation(
            session,
            company_id=company.id,
            created_by=user.id,
            as_of_date=date(2026, 9, 11),
        )
        run_id = investigation.current_run_id
        await session.execute(
            update(WorkflowRun).where(WorkflowRun.id == run_id).values(status="running")
        )
        content = "closed before freeze"
        session.add(
            Evidence(
                company_id=company.id,
                source_type="judicial",
                risk_type="judicial",
                source_uri="synthetic://between-nodes/closed",
                title="Closed",
                observed_at=date(2026, 9, 10),
                fact_type="judicial_case_status",
                fact_key="CASE-CLOSED",
                fact_value="closed",
                polarity="neutral",
                content=content,
                content_hash=sha256(content.encode()).hexdigest(),
            )
        )
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
    async with sessions.begin() as session:
        await build_index(session, FakeEmbedding(), IndexContract.fake())
    conninfo = settings.database_url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    config: RunnableConfig = {"configurable": {"thread_id": str(run_id)}}
    initial: SpikeState = {"run_id": str(run_id)}
    async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
        graph = build_graph(saver, sessions)
        await graph.ainvoke(
            initial,
            config,
            interrupt_after=["retrieve_evidence"],
            durability="sync",
        )
        async with sessions() as session:
            assert await session.scalar(
                select(WorkflowStep.id).where(
                    WorkflowStep.run_id == run_id,
                    WorkflowStep.name == "retrieve_evidence",
                )
            )
            assert not await session.scalar(
                select(WorkflowStep.id).where(
                    WorkflowStep.run_id == run_id,
                    WorkflowStep.name == "apply_rules",
                )
            )
        async with sessions.begin() as session:
            content = "open after freeze"
            session.add(
                Evidence(
                    company_id=company.id,
                    source_type="judicial",
                    risk_type="judicial",
                    source_uri="synthetic://between-nodes/open",
                    title="Open",
                    observed_at=date(2026, 9, 11),
                    fact_type="judicial_case_status",
                    fact_key="CASE-OPEN",
                    fact_value="open",
                    polarity="negative",
                    content=content,
                    content_hash=sha256(content.encode()).hexdigest(),
                )
            )
        await graph.ainvoke(None, config, durability="sync")
    async with sessions() as session:
        report = await session.scalar(
            select(RiskReport).where(RiskReport.run_id == run_id)
        )
        assert report is not None
        snapshot = cast(dict[str, Any], report.snapshot)
        assert snapshot["rule_score"] == 0
        assert [item["fact_key"] for item in snapshot["retrieved_evidence"]] == [
            "CASE-CLOSED"
        ]
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AIFinding)
                .where(AIFinding.run_id == run_id)
            )
            == 0
        )


async def test_graph_direct_report_and_zero_generation_replay(
    resources: "Resources", monkeypatch: pytest.MonkeyPatch
) -> None:
    from hashlib import sha256

    settings, sessions, _ = resources
    async with sessions.begin() as session:
        company = Company(
            synthetic_key="direct-graph",
            name="Direct",
            unified_credit_code="SYNTHETIC-DIRECT-GRAPH",
        )
        user = User(username="direct-graph", password_hash="unused", role="analyst")
        session.add_all([company, user])
        await session.flush()
        investigation = await create_investigation(
            session,
            company_id=company.id,
            created_by=user.id,
            as_of_date=date(2026, 9, 11),
        )
        run_id = investigation.current_run_id
        content = "Case is closed"
        session.add(
            Evidence(
                company_id=company.id,
                source_type="judicial",
                risk_type="judicial",
                source_uri="synthetic://direct-graph",
                title="Case",
                observed_at=date(2026, 9, 10),
                fact_type="judicial_case_status",
                fact_key="CASE-DIRECT",
                fact_value="closed",
                polarity="neutral",
                content=content,
                content_hash=sha256(content.encode()).hexdigest(),
            )
        )
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
    calls = 0
    original = MockModelAdapter.analyze_risk

    async def counted(
        self: MockModelAdapter, context: RiskContext
    ) -> StructuredRiskFindings:
        nonlocal calls
        calls += 1
        return await original(self, context)

    monkeypatch.setattr(MockModelAdapter, "analyze_risk", counted)
    async with sessions.begin() as session:
        await build_index(session, FakeEmbedding(), IndexContract.fake())
    await execute_workflow(settings, sessions, run_id, "start")
    await execute_workflow(settings, sessions, run_id, "start")
    async with sessions() as session:
        assert (
            await session.scalar(
                select(WorkflowRun.status).where(WorkflowRun.id == run_id)
            )
            == "completed"
        )
        assert (
            await session.scalar(select(func.count()).select_from(ReviewDecision)) == 0
        )
        assert await session.scalar(select(func.count()).select_from(RiskReport)) == 1
    assert calls == 1


@pytest.mark.parametrize("failure", ["schema", "citation"])
async def test_permanent_model_failure_is_not_retried(
    resources: "Resources", monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    _, sessions, app = resources
    run_id = await seed(sessions)
    calls = 0

    async def malformed(
        self: MockModelAdapter, context: object
    ) -> StructuredRiskFindings:
        nonlocal calls
        calls += 1
        if failure == "citation":
            return StructuredRiskFindings.model_validate(
                {
                    "findings": [
                        {
                            "finding_key": "invalid-citation",
                            "risk_type": "judicial",
                            "claim_code": "JUDICIAL_OPEN_CASE",
                            "title": "Case",
                            "summary": "Case",
                            "reasoning": "Case",
                            "evidence_citations": [
                                {
                                    "evidence_id": str(uuid4()),
                                    "chunk_id": str(uuid4()),
                                    "content_hash": "0" * 64,
                                    "quoted_excerpt": "Case",
                                }
                            ],
                            "evidence_sufficiency": "sufficient",
                            "confidence_level": "high",
                            "review_recommended": True,
                        }
                    ]
                }
            )
        return StructuredRiskFindings.model_validate({"unexpected": True})

    monkeypatch.setattr(MockModelAdapter, "analyze_risk", malformed)
    await dispatch_once(sessions, app)
    await consume(app)
    async with sessions() as session:
        assert (
            await session.scalar(
                select(WorkflowRun.status).where(WorkflowRun.id == run_id)
            )
            == "failed"
        )
        assert await session.scalar(select(func.count()).select_from(AIFinding)) == 0
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM procrastinate_jobs WHERE status = 'succeeded'"
                )
            )
            == 1
        )
    assert calls == 1


async def test_pipeline_rejects_terminal_run(resources: "Resources") -> None:
    _, sessions, _ = resources
    run_id = await seed(sessions)
    async with sessions.begin() as session:
        await session.execute(
            update(WorkflowRun).where(WorkflowRun.id == run_id).values(status="failed")
        )
    with pytest.raises(ValueError, match="current and running"):
        async with sessions.begin() as session:
            await retrieve_evidence(session, run_id=run_id)
    with pytest.raises(ValueError, match="current and running"):
        await generate_validate_findings(sessions, run_id=run_id)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AIFinding)) == 0


pytestmark = pytest.mark.anyio
Resources = tuple[Settings, async_sessionmaker[AsyncSession], procrastinate.App]
REVIEWER_ID = UUID("30000000-0000-0000-0000-000000000001")


@pytest.fixture
async def resources(database: Settings) -> AsyncIterator[Resources]:
    await initialize_checkpoints()
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            session.add(
                User(
                    id=REVIEWER_ID,
                    username="workflow-reviewer",
                    password_hash="unused",
                    role="reviewer",
                )
            )
        async with create_worker(database, sessions).open_async() as app:
            await app.schema_manager.apply_schema_async()
            yield database, sessions, app
    finally:
        await engine.dispose()


async def seed(sessions: async_sessionmaker[AsyncSession]) -> UUID:
    async with sessions.begin() as session:
        creator = User(username=str(uuid4()), password_hash="unused", role="analyst")
        session.add(creator)
        await session.flush()
        investigation = await create_investigation(
            session,
            company_id=uuid4(),
            created_by=creator.id,
            as_of_date=date(2026, 9, 11),
        )
        await build_index(session, FakeEmbedding(), IndexContract.fake())
        return investigation.current_run_id


async def consume(app: procrastinate.App) -> None:
    await app.run_worker_async(
        wait=False, install_signal_handlers=False, queues=["tracerisk"]
    )


def review_request(run_id: UUID) -> RunReview:
    return RunReview(
        run_id=run_id,
        reviewer_id=REVIEWER_ID,
        idempotency_key=str(uuid4()),
        decision="approve",
        reason="已核对技术 Spike，证据不足仍保留",
    )


async def worker_process(patch: str = "") -> int:
    script = (
        """
import asyncio, os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from tracerisk.config import Settings
from tracerisk.workflows import spike_graph
from tracerisk.workflows.worker import create_worker
"""
        + patch
        + """
async def main():
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    try:
        queue = create_worker(settings, async_sessionmaker(engine))
        async with queue.open_async() as app:
            await app.run_worker_async(wait=False, install_signal_handlers=False,
                                      queues=['tracerisk'])
    finally:
        await engine.dispose()
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
    runner.run(main())
"""
    )
    process = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script],
        env=os.environ.copy(),
        timeout=30,
    )
    return process.returncode


async def expire_worker(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions.begin() as session:
        await session.execute(
            text(
                "UPDATE procrastinate_workers "
                "SET last_heartbeat = now() - interval '1 minute'"
            )
        )


async def test_interrupt_new_process_resume_and_idempotency(
    resources: Resources,
) -> None:
    settings, sessions, app = resources
    run_id = await seed(sessions)
    await dispatch_once(sessions, app)
    assert await worker_process() == 0
    async with sessions() as session:
        assert await session.scalar(select(WorkflowRun.status)) == "awaiting_review"
        assert (
            await session.scalar(
                text("SELECT count(*) FROM procrastinate_jobs WHERE status = 'doing'")
            )
            == 0
        )
        assert await session.scalar(
            text("SELECT DISTINCT thread_id FROM checkpoints")
        ) == str(run_id)
    request = review_request(run_id)
    review_id = await submit_review(sessions, request)
    assert await submit_review(sessions, request) == review_id
    await dispatch_once(sessions, app)
    # A new interpreter constructs a new worker, graph, connection and saver.
    assert await worker_process() == 0
    async with sessions() as session:
        report = await session.scalar(select(RiskReport))
        assert report is not None
        snapshot = dict(report.snapshot)
        assert snapshot["review_id"] == str(review_id)
        assert await session.scalar(select(WorkflowRun.status)) == "completed"
    assert await submit_review(sessions, request) == review_id
    await enqueue_run(app, run_id, "resume")
    await consume(app)
    await execute_workflow(settings, sessions, run_id, "resume")
    async with sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ReviewDecision)) == 1
        )
        assert await session.scalar(select(func.count()).select_from(RiskReport)) == 1
        assert await session.scalar(select(RiskReport.snapshot)) == snapshot
        assert (
            await session.scalar(select(func.count()).select_from(DispatchOutbox)) == 2
        )


async def test_checkpoint_before_status_crash(resources: Resources) -> None:
    _, sessions, app = resources
    await seed(sessions)
    await dispatch_once(sessions, app)
    assert (
        await worker_process("""
async def die(*args):
    os._exit(23)
spike_graph.publish_waiting = die
""")
        == 23
    )
    async with sessions() as session:
        assert await session.scalar(select(WorkflowRun.status)) == "running"
        assert await session.scalar(text("SELECT count(*) FROM checkpoints")) > 0
    await expire_worker(sessions)
    assert await recover_stalled(app) == 1
    assert await worker_process() == 0
    async with sessions() as session:
        assert await session.scalar(select(WorkflowRun.status)) == "awaiting_review"


@pytest.mark.parametrize("point", ["claimed", "before_report", "after_report"])
async def test_resume_crash_windows(resources: Resources, point: str) -> None:
    _, sessions, app = resources
    run_id = await seed(sessions)
    await dispatch_once(sessions, app)
    await consume(app)
    await submit_review(sessions, review_request(run_id))
    await dispatch_once(sessions, app)
    if point == "claimed":
        patch = """
def die(*args):
    os._exit(24)
spike_graph.build_graph = die
"""
    else:
        patch = f"""
original = spike_graph.generate_report
async def die(*args):
    if {point == "after_report"!r}:
        await original(*args)
    os._exit(24)
spike_graph.generate_report = die
"""
    assert await worker_process(patch) == 24
    await expire_worker(sessions)
    assert await recover_stalled(app) == 1
    assert await worker_process() == 0
    async with sessions() as session:
        assert await session.scalar(select(WorkflowRun.status)) == "completed"
        assert await session.scalar(select(func.count()).select_from(RiskReport)) == 1


async def test_concurrent_review_and_resume(resources: Resources) -> None:
    settings, sessions, app = resources
    run_id = await seed(sessions)
    await dispatch_once(sessions, app)
    await consume(app)
    request = review_request(run_id)
    first, second = await asyncio.gather(
        submit_review(sessions, request), submit_review(sessions, request)
    )
    assert first == second
    await asyncio.gather(
        execute_workflow(settings, sessions, run_id, "resume"),
        execute_workflow(settings, sessions, run_id, "resume"),
    )
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(RiskReport)) == 1


async def test_review_validation_and_atomic_rollback(resources: Resources) -> None:
    _, sessions, app = resources
    run_id = await seed(sessions)
    request = review_request(run_id)
    with pytest.raises(ReviewConflict):
        await submit_review(sessions, request)
    await dispatch_once(sessions, app)
    await consume(app)
    async with sessions() as session:
        creator = await session.scalar(select(Investigation.created_by))
    with pytest.raises(ReviewConflict):
        await submit_review(
            sessions, request.model_copy(update={"reviewer_id": creator})
        )
    # Force the final insert to fail: decision and status must roll back together.
    async with sessions.begin() as session:
        session.add(DispatchOutbox(run_id=run_id, operation="resume"))
    with pytest.raises(ReviewConflict):
        await submit_review(sessions, request)
    async with sessions() as session:
        assert await session.scalar(select(WorkflowRun.status)) == "awaiting_review"
        assert (
            await session.scalar(select(func.count()).select_from(ReviewDecision)) == 0
        )


async def test_reject_and_conflicting_replay(resources: Resources) -> None:
    _, sessions, app = resources
    run_id = await seed(sessions)
    await dispatch_once(sessions, app)
    await consume(app)
    request = review_request(run_id).model_copy(update={"decision": "reject"})
    await submit_review(sessions, request)
    for updates in ({"reason": "不同理由"}, {"idempotency_key": "another-key"}):
        with pytest.raises(ReviewConflict):
            await submit_review(sessions, request.model_copy(update=updates))
    await dispatch_once(sessions, app)
    await consume(app)
    async with sessions() as session:
        report = await session.scalar(select(RiskReport))
        assert report is not None
        assert report.snapshot["decision"] == "reject"
        assert report.snapshot["evidence_sufficiency"] == "insufficient"


async def test_missing_decision_and_failed_run_cannot_resume(
    resources: Resources,
) -> None:
    settings, sessions, app = resources
    run_id = await seed(sessions)
    await dispatch_once(sessions, app)
    await consume(app)
    await execute_workflow(settings, sessions, run_id, "resume")
    async with sessions() as session:
        assert await session.scalar(select(WorkflowRun.status)) == "awaiting_review"
    await submit_review(sessions, review_request(run_id))
    async with sessions.begin() as session:
        await session.execute(update(WorkflowRun).values(status="failed"))
    await execute_workflow(settings, sessions, run_id, "resume")
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(RiskReport)) == 0


async def test_new_run_has_distinct_thread(resources: Resources) -> None:
    settings, sessions, app = resources
    old_id = await seed(sessions)
    await dispatch_once(sessions, app)
    await consume(app)
    async with sessions.begin() as session:
        investigation = await session.scalar(select(Investigation))
        assert investigation is not None
        new_id = uuid4()
        await session.execute(update(WorkflowRun).values(status="failed"))
        session.add(
            WorkflowRun(id=new_id, investigation_id=investigation.id, run_number=2)
        )
        investigation.current_run_id = new_id
    await execute_workflow(settings, sessions, new_id, "start")
    async with sessions() as session:
        ids = set(
            (
                await session.execute(
                    text("SELECT DISTINCT thread_id FROM checkpoints")
                )
            ).scalars()
        )
        assert ids == {str(old_id), str(new_id)}
    conninfo = settings.database_url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    async with AsyncPostgresSaver.from_conn_string(conninfo) as saver:
        graph = build_graph(saver, sessions)
        state = await graph.aget_state({"configurable": {"thread_id": str(new_id)}})
        assert any(task.interrupts for task in state.tasks)


async def test_same_key_cannot_review_two_runs(resources: Resources) -> None:
    _, sessions, app = resources
    first = await seed(sessions)
    second = await seed(sessions)
    await dispatch_once(sessions, app)
    await consume(app)
    request = review_request(first)
    results = await asyncio.gather(
        submit_review(sessions, request),
        submit_review(sessions, request.model_copy(update={"run_id": second})),
        return_exceptions=True,
    )
    assert sum(isinstance(result, UUID) for result in results) == 1
    assert sum(isinstance(result, ReviewConflict) for result in results) == 1
    async with sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ReviewDecision)) == 1
        )
        states = set((await session.scalars(select(WorkflowRun.status))).all())
        assert states == {"awaiting_review", "resume_queued"}
