from collections import Counter
from datetime import date
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tracerisk.ai.mock_adapter import MockModelAdapter
from tracerisk.ai.models import AIFinding
from tracerisk.ai.service import generate_validate_findings
from tracerisk.ai.validation import CitationValidationError, validate_findings
from tracerisk.audit.models import AuditEvent
from tracerisk.auth.models import User
from tracerisk.companies.models import Company
from tracerisk.evidence.conflicts import load_retrieved_conflicts
from tracerisk.evidence.models import Evidence, EvidenceChunk, RetrievedEvidence
from tracerisk.evidence.service import build_risk_context, retrieve_evidence
from tracerisk.investigations.models import Investigation, WorkflowRun, WorkflowStep
from tracerisk.investigations.service import create_investigation
from tracerisk.reports.models import RiskReport
from tracerisk.reports.service import generate_report
from tracerisk.retrieval.embeddings import FakeEmbedding
from tracerisk.retrieval.indexing import IndexContract, build_index
from tracerisk.retrieval.models import EmbeddingIndexVersion
from tracerisk.retrieval.queries import QUERY_VERSION, workflow_queries
from tracerisk.reviews.models import ReviewDecision
from tracerisk.rules.engine import evaluate_all_rules_from_facts, save_rule_findings
from tracerisk.workflows.outbox import dispatch_once
from tracerisk.workflows.spike_graph import initialize_checkpoints
from tracerisk.workflows.worker import create_worker

pytestmark = pytest.mark.anyio


async def seed(session, *, indexed=True):
    company = Company(
        synthetic_key="freeze", name="Freeze", unified_credit_code="FREEZE"
    )
    user = User(username="freeze", password_hash="unused", role="analyst")
    session.add_all([company, user])
    await session.flush()
    for query in workflow_queries():
        for n in range(7):
            content = query.text + (
                "\n\n未选中的新闻段落" if query.risk_type == "news" else ""
            )
            fact_type, value = {
                "business": ("business_abnormal_status", "active"),
                "judicial": ("judicial_case_status", "open"),
                "news": ("negative_news_story", "negative"),
                "intellectual_property": ("core_ip_status", "invalid"),
            }[query.risk_type]
            session.add(
                Evidence(
                    company_id=company.id,
                    source_type=query.risk_type,
                    risk_type=query.risk_type,
                    source_uri=f"synthetic://freeze/{query.risk_type}/{n}",
                    title=query.text,
                    observed_at=date(2026, 9, 11),
                    fact_type=fact_type,
                    fact_key=str(n),
                    fact_value=value,
                    polarity="negative",
                    content=content,
                    content_hash=sha256(content.encode()).hexdigest(),
                )
            )
    await session.flush()
    version = (
        await build_index(session, FakeEmbedding(), IndexContract.fake())
        if indexed
        else None
    )
    investigation = await create_investigation(
        session, company_id=company.id, created_by=user.id, as_of_date=date(2026, 9, 11)
    )
    run = await session.get(WorkflowRun, investigation.current_run_id)
    run.status = "running"
    await session.flush()
    return run.id, version


async def test_four_top5_manifest_context_and_frozen_index(database, monkeypatch):
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            run_id, version = await seed(session)
            await retrieve_evidence(session, run_id=run_id)
            context = await build_risk_context(session, run_id=run_id)
            assert Counter(c.risk_type for c in context.evidence_chunks) == {
                q.risk_type: 5 for q in workflow_queries()
            }
            rows = (
                await session.execute(
                    select(EvidenceChunk, RetrievedEvidence)
                    .join(RetrievedEvidence)
                    .where(RetrievedEvidence.run_id == run_id)
                )
            ).all()
            assert {
                c.chunk_id: (c.content_hash, c.evidence_content_hash, c.content)
                for c in context.evidence_chunks
            } == {
                c.id: (c.content_hash, c.evidence_content_hash, c.content)
                for c, _ in rows
            }
            manifest = await session.scalar(
                select(WorkflowStep.name).where(
                    WorkflowStep.name.startswith("retrieve_evidence_manifest:")
                )
            )
            assert QUERY_VERSION in manifest and str(version.id) in manifest
            assert any(
                c.content_hash != c.evidence_content_hash
                for c in context.evidence_chunks
            )
        async with sessions.begin() as session:
            old = await session.get(EmbeddingIndexVersion, version.id)
            old.active = False
            await session.flush()
            session.add(
                EmbeddingIndexVersion(
                    id=uuid4(),
                    version_key="next",
                    provider="fake",
                    model="next",
                    dimensions=512,
                    distance="cosine",
                    chunk_schema_version="v1",
                    active=True,
                )
            )
            # Live metadata must not supply frozen facts, text, or report references.
            await session.execute(
                update(Evidence).values(
                    fact_value="changed", content="changed", title="changed"
                )
            )
        async with sessions.begin() as session:
            await retrieve_evidence(session, run_id=run_id)
            assert await build_risk_context(session, run_id=run_id) == context
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "damage",
    [
        "tail",
        "all",
        "rank",
        "content",
        "fact",
        "manifest",
        "marker",
        "source_version",
        "query_version",
        "index_version",
        "index_metadata",
        "manifest_digest",
        "schema",
        "scope",
    ],
)
async def test_frozen_set_damage_rejected(database, damage):
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            run_id, _ = await seed(session)
            await retrieve_evidence(session, run_id=run_id)
            context = await build_risk_context(session, run_id=run_id)
            drafts = await MockModelAdapter().analyze_risk(context)
            user_id = await session.scalar(select(User.id))
        async with sessions.begin() as session:
            row = await session.scalar(
                select(RetrievedEvidence).where(
                    RetrievedEvidence.run_id == run_id, RetrievedEvidence.rank == 5
                )
            )
            if damage == "tail":
                await session.delete(row)
            elif damage == "all":
                await session.execute(
                    delete(RetrievedEvidence).where(RetrievedEvidence.run_id == run_id)
                )
            elif damage == "rank":
                row.rank = 6
            elif damage in {"content", "fact"}:
                chunk = await session.get(EvidenceChunk, row.chunk_id)
                if damage == "content":
                    chunk.content = "forged"
                    chunk.content_hash = sha256(b"forged").hexdigest()
                else:
                    chunk.fact_value = "forged"
            elif damage == "source_version":
                chunk = await session.get(EvidenceChunk, row.chunk_id)
                source = await session.get(Evidence, chunk.evidence_id)
                replacement = Evidence(
                    **{
                        column.name: getattr(source, column.name)
                        for column in Evidence.__table__.columns
                        if column.name
                        not in {"id", "source_uri", "content", "content_hash"}
                    },
                    source_uri="synthetic://replacement",
                    content="new source",
                    content_hash=sha256(b"new source").hexdigest(),
                )
                session.add(replacement)
                await session.flush()
                chunk.evidence_id = replacement.id
                chunk.evidence_content_hash = replacement.content_hash
            elif damage == "schema":
                await session.execute(
                    update(EvidenceChunk).values(chunk_schema_version="legacy-stage3")
                )
            elif damage == "index_metadata":
                version = await session.scalar(select(EmbeddingIndexVersion))
                version.model = "altered-historical-model"
            elif damage in {"query_version", "index_version", "manifest_digest"}:
                marker = await session.scalar(
                    select(WorkflowStep).where(
                        WorkflowStep.name.startswith("retrieve_evidence_manifest:")
                    )
                )
                if damage == "query_version":
                    marker.name = marker.name.replace(QUERY_VERSION, "unknown-queries")
                elif damage == "manifest_digest":
                    marker.name = marker.name.rsplit(":", 1)[0] + ":" + "0" * 64
                else:
                    version = await session.scalar(select(EmbeddingIndexVersion))
                    marker.name = marker.name.replace(str(version.id), str(uuid4()))
            elif damage == "scope":
                run = await session.get(WorkflowRun, run_id)
                investigation = await session.get(Investigation, run.investigation_id)
                investigation.as_of_date = date(2026, 9, 12)
            else:
                await session.execute(
                    delete(WorkflowStep).where(
                        WorkflowStep.run_id == run_id,
                        WorkflowStep.name.startswith("retrieve_evidence_manifest:")
                        if damage == "manifest"
                        else WorkflowStep.name == "retrieve_evidence",
                    )
                )
        async with sessions.begin() as session:
            with pytest.raises(ValueError):
                await retrieve_evidence(session, run_id=run_id)
            with pytest.raises(ValueError):
                await build_risk_context(session, run_id=run_id)
            with pytest.raises(CitationValidationError):
                await validate_findings(
                    session, run_id=run_id, user_id=user_id, findings=drafts
                )
    finally:
        await engine.dispose()


async def test_missing_index_never_falls_back_to_evidence(database):
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine)
    try:
        async with sessions.begin() as session:
            run_id, _ = await seed(session, indexed=False)
        async with sessions.begin() as session:
            with pytest.raises(ValueError, match="index"):
                await retrieve_evidence(session, run_id=run_id)
            assert not (await session.scalars(select(RetrievedEvidence))).all()
    finally:
        await engine.dispose()


async def test_consumers_use_exact_frozen_set_and_report_survives_reindex(
    database, monkeypatch
):
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    captured = []
    original = MockModelAdapter.analyze_risk

    async def capture(self, context):
        captured.append(context)
        return await original(self, context)

    monkeypatch.setattr(MockModelAdapter, "analyze_risk", capture)
    try:
        async with sessions.begin() as session:
            run_id, _ = await seed(session)
            await retrieve_evidence(session, run_id=run_id)
            context = await build_risk_context(session, run_id=run_id)
            # Mutating live fields must neither alter nor invalidate the frozen set.
            await session.execute(
                update(Evidence).values(
                    content="live substitution",
                    fact_value="changed",
                    title="live title",
                )
            )
            policy = await load_retrieved_conflicts(
                session, run_id=run_id, as_of_date=date(2026, 9, 11)
            )
            score = evaluate_all_rules_from_facts(
                policy.resolved_facts, as_of_date=date(2026, 9, 11)
            )
            assert score.total_score == 95
            await save_rule_findings(session, run_id=run_id, score=score)
        await generate_validate_findings(sessions, run_id=run_id)
        async with sessions.begin() as session:
            assert captured == [context]
            findings = (
                await session.scalars(
                    select(AIFinding).where(AIFinding.run_id == run_id)
                )
            ).all()
            assert findings
            selected = {str(c.chunk_id): c for c in context.evidence_chunks}
            for finding in findings:
                for citation in finding.citations:
                    chunk = selected[citation["chunk_id"]]
                    assert citation["content_hash"] == chunk.content_hash
                    assert citation["quoted_excerpt"] in chunk.content
                session.add(
                    ReviewDecision(
                        run_id=run_id,
                        ai_finding_id=finding.id,
                        finding_version=1,
                        reviewer_id=uuid4(),
                        idempotency_key=str(uuid4()),
                        decision="approve",
                        reason="verified",
                    )
                )
        report_id = await generate_report(sessions, run_id)
        async with sessions() as session:
            report = await session.get(RiskReport, report_id)
            before = report.snapshot, report.html_content
            assert {
                row["chunk_id"]: row["content"]
                for row in report.snapshot["retrieved_evidence"]
            } == {str(c.chunk_id): c.content for c in context.evidence_chunks}
            assert all(
                ref["content"] == selected[ref["chunk_id"]].content
                for ref in report.snapshot["evidence_references"]
            )
            assert all(
                "\n\n" not in ref["content"]
                for ref in report.snapshot["evidence_references"]
            )
        async with sessions.begin() as session:
            # Restore original sources, then add a new immutable Evidence version.
            sources = (await session.scalars(select(Evidence))).all()
            for source in sources:
                query = next(
                    q for q in workflow_queries() if q.risk_type == source.risk_type
                )
                source.content = query.text + (
                    "\n\n未选中的新闻段落" if source.risk_type == "news" else ""
                )
                source.fact_value = {
                    "business": "active",
                    "judicial": "open",
                    "news": "negative",
                    "intellectual_property": "invalid",
                }[source.risk_type]
            source = sources[0]
            session.add(
                Evidence(
                    company_id=source.company_id,
                    source_type=source.source_type,
                    risk_type=source.risk_type,
                    source_uri="synthetic://freeze/new-version",
                    title="New",
                    observed_at=date(2026, 9, 11),
                    fact_type=source.fact_type,
                    fact_key="new",
                    fact_value=source.fact_value,
                    polarity=source.polarity,
                    content="New indexed evidence",
                    content_hash=sha256(b"New indexed evidence").hexdigest(),
                )
            )
            await session.flush()
            await build_index(session, FakeEmbedding(), IndexContract.fake())
        assert await generate_report(sessions, run_id) == report_id
        async with sessions() as session:
            report = await session.get(RiskReport, report_id)
            assert (report.snapshot, report.html_content) == before
    finally:
        await engine.dispose()


async def test_retrieval_transaction_rolls_back_all_rows_and_markers(database):
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine)
    try:
        async with sessions.begin() as session:
            run_id, _ = await seed(session)
        with pytest.raises(RuntimeError, match="caller rollback"):
            async with sessions.begin() as session:
                await retrieve_evidence(session, run_id=run_id)
                raise RuntimeError("caller rollback")
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(RetrievedEvidence)
                )
                == 0
            )
            assert (
                await session.scalar(select(func.count()).select_from(WorkflowStep))
                == 0
            )
        async with sessions.begin() as session:
            await retrieve_evidence(session, run_id=run_id)
            assert (
                len((await build_risk_context(session, run_id=run_id)).evidence_chunks)
                == 20
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("failure", ["missing", "incompatible", "partial", "query"])
async def test_worker_retrieval_failures_are_terminal_once(database, failure):
    from tracerisk.retrieval.models import IndexQueryEmbedding

    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await initialize_checkpoints()
        async with sessions.begin() as session:
            run_id, version = await seed(session, indexed=failure != "missing")
            if failure == "partial":
                await retrieve_evidence(session, run_id=run_id)
                await session.execute(
                    delete(RetrievedEvidence).where(RetrievedEvidence.rank == 5)
                )
            elif failure == "incompatible":
                version.model = "wrong-model"
            elif failure == "query":
                await session.execute(delete(IndexQueryEmbedding))
        async with create_worker(database, sessions).open_async() as app:
            await app.schema_manager.apply_schema_async()
            await dispatch_once(sessions, app)
            await app.run_worker_async(
                wait=False, install_signal_handlers=False, queues=["tracerisk"]
            )
            async with sessions() as session:
                run = await session.get(WorkflowRun, run_id)
                assert run.status == "failed"
                job = (
                    await session.execute(
                        text("SELECT status, attempts FROM procrastinate_jobs")
                    )
                ).one()
                assert job.status == "succeeded" and job.attempts == 1
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(AuditEvent)
                        .where(AuditEvent.event_type == "workflow.failed")
                    )
                    == 1
                )
                assert (
                    await session.scalar(select(func.count()).select_from(RiskReport))
                    == 0
                )
    finally:
        await engine.dispose()
