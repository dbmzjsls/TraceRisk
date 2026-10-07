import asyncio
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tracerisk.auth.models import User
from tracerisk.companies.models import Company
from tracerisk.config import Settings
from tracerisk.evidence.models import Evidence, EvidenceChunk
from tracerisk.evidence.service import (
    _retrieval_manifest,
    build_risk_context,
    retrieve_evidence,
)
from tracerisk.investigations.models import Investigation, WorkflowRun, WorkflowStep
from tracerisk.retrieval.embeddings import FakeEmbedding
from tracerisk.retrieval.indexing import (
    IndexContract,
    IndexReadinessError,
    build_index,
    check_index,
)
from tracerisk.retrieval.models import (
    ChunkEmbedding,
    EmbeddingIndexVersion,
    IndexQueryEmbedding,
)

pytestmark = pytest.mark.anyio


class CountingEmbedding(FakeEmbedding):
    document_calls = 0
    query_calls = 0

    async def embed_documents(self, texts):
        self.document_calls += 1
        return await super().embed_documents(texts)

    async def embed_queries(self, texts):
        self.query_calls += 1
        return await FakeEmbedding().embed_documents(texts)


async def seed(session):
    company = Company(
        synthetic_key="vector", name="Vector", unified_credit_code="SYNTHETIC-VECTOR"
    )
    session.add(company)
    await session.flush()
    content = "First negative news paragraph.\n\nSecond paragraph."
    source = Evidence(
        company_id=company.id,
        source_type="news",
        risk_type="news",
        source_uri="synthetic://vector/1",
        title="News",
        observed_at=date(2026, 9, 16),
        fact_type="news_sentiment",
        fact_key="n1",
        fact_value="negative",
        polarity="negative",
        content=content,
        content_hash=sha256(content.encode()).hexdigest(),
    )
    session.add(source)
    await session.flush()
    return source


async def test_index_idempotence_readiness_and_mismatch(database: Settings):
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    provider = CountingEmbedding()
    contract = IndexContract.fake()
    try:
        async with sessions.begin() as session:
            source = await seed(session)
            assert await check_index(session, contract, required=False) is None
            with pytest.raises(IndexReadinessError):
                await check_index(session, contract)
            version = await build_index(session, provider, contract)
            assert (
                await session.scalar(select(func.count()).select_from(EvidenceChunk))
                == 2
            )
            assert (
                await session.scalar(select(func.count()).select_from(ChunkEmbedding))
                == 2
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(IndexQueryEmbedding)
                )
                == 4
            )
            assert (
                await session.scalar(
                    text("SELECT vector_dims(embedding) FROM chunk_embeddings LIMIT 1")
                )
                == 512
            )
            assert await check_index(session, contract) is not None
            await build_index(session, provider, contract)
            assert (provider.document_calls, provider.query_calls) == (1, 1)
            for field, value in (
                ("provider", "bge"),
                ("model", "wrong"),
                ("dimensions", 3),
                ("distance", "l2"),
                ("chunk_schema_version", "v2"),
            ):
                with pytest.raises(IndexReadinessError):
                    await check_index(session, replace(contract, **{field: value}))
            assert (
                await check_index(
                    session,
                    contract,
                    company_id=source.company_id,
                    as_of_date=date(2000, 1, 1),
                )
                is not None
            )
            query = await session.scalar(select(IndexQueryEmbedding))
            query.query_hash = "a" * 64
            await session.flush()
            with pytest.raises(IndexReadinessError):
                await check_index(session, contract)
            assert version.active
    finally:
        await engine.dispose()


async def test_failed_query_batch_rolls_back_whole_index(database: Settings):
    class Broken(FakeEmbedding):
        async def embed_queries(self, texts):
            return [[0.0] * 512 for _ in texts]

    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine)
    try:
        async with sessions.begin() as session:
            await seed(session)
        with pytest.raises(ValueError):
            async with sessions.begin() as session:
                await build_index(session, Broken(), IndexContract.fake())
        async with sessions() as session:
            for model in (
                EvidenceChunk,
                ChunkEmbedding,
                IndexQueryEmbedding,
                EmbeddingIndexVersion,
            ):
                assert (
                    await session.scalar(select(func.count()).select_from(model)) == 0
                )
    finally:
        await engine.dispose()


async def test_activation_serializes_and_database_enforces_single_active(
    database: Settings,
):
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine)
    provider = CountingEmbedding()
    try:
        async with sessions.begin() as session:
            await seed(session)

        async def build():
            async with sessions.begin() as session:
                await session.execute(text("SET LOCAL lock_timeout = '5s'"))
                await build_index(session, provider, IndexContract.fake())

        await asyncio.gather(build(), build())
        assert (provider.document_calls, provider.query_calls) == (1, 1)
        with pytest.raises(IntegrityError):
            async with sessions.begin() as session:
                session.add(
                    EmbeddingIndexVersion(
                        version_key="second",
                        provider="fake",
                        model="fake-sha256-v1",
                        dimensions=512,
                        distance="cosine",
                        chunk_schema_version="v1",
                        active=True,
                    )
                )
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "mutation",
    [
        "document_missing",
        "document_zero",
        "query_missing",
        "query_version",
        "query_hash",
        "query_zero",
        "eligible_missing",
    ],
)
async def test_readiness_rejects_incomplete_or_corrupt_vectors(
    database: Settings, mutation
):
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine)
    try:
        async with sessions.begin() as session:
            source = await seed(session)
            await build_index(session, FakeEmbedding(), IndexContract.fake())
            if mutation.startswith("document"):
                vector = await session.scalar(select(ChunkEmbedding))
                if mutation == "document_missing":
                    await session.delete(vector)
                else:
                    vector.embedding = [0.0] * 512
            elif mutation.startswith("query"):
                query = await session.scalar(select(IndexQueryEmbedding))
                if mutation == "query_missing":
                    await session.delete(query)
                elif mutation == "query_version":
                    query.query_version = "wrong"
                elif mutation == "query_hash":
                    query.query_hash = "a" * 64
                else:
                    query.embedding = [0.0] * 512
            else:
                session.add(
                    Evidence(
                        **{
                            column.name: getattr(source, column.name)
                            for column in Evidence.__table__.columns
                            if column.name not in {"id", "source_uri"}
                        },
                        id=uuid4(),
                        source_uri="synthetic://vector/new",
                    )
                )
            await session.flush()
            with pytest.raises(IndexReadinessError):
                await check_index(session, IndexContract.fake())
    finally:
        await engine.dispose()


async def test_upgrade_preserves_legacy_news_snapshots(database: Settings):
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.downgrade(config, "0009")
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            source = await seed(session)
            user = User(username="migration", password_hash="unused", role="analyst")
            session.add(user)
            await session.flush()
            # Construct a pre-event fixture at its historical schema revision.
            # The current creation service requires the Stage 5 event table.
            investigation = Investigation(
                id=uuid4(),
                company_id=source.company_id,
                created_by=user.id,
                as_of_date=source.observed_at,
                current_run_id=uuid4(),
            )
            run = WorkflowRun(
                id=investigation.current_run_id,
                investigation_id=investigation.id,
                run_number=1,
                status="running",
            )
            session.add_all([investigation, run])
            await session.flush()
            chunk_id = uuid4()
            await session.execute(
                text("""INSERT INTO evidence_chunks
                (id, evidence_id, company_id, observed_at, fact_type, fact_key,
                 fact_value, polarity, content, content_hash)
                SELECT :id, id, company_id, observed_at, fact_type, fact_key,
                 fact_value, polarity, content, content_hash
                FROM evidence WHERE id = :evidence"""),
                {"id": chunk_id, "evidence": source.id},
            )
            await session.execute(
                text(
                    "INSERT INTO retrieved_evidence "
                    "(id, run_id, risk_type, chunk_id, rank) "
                    "VALUES (:id, :run, 'news', :chunk, 1)"
                ),
                {"id": uuid4(), "run": run.id, "chunk": chunk_id},
            )
            session.add_all(
                [
                    WorkflowStep(run_id=run.id, name="retrieve_evidence"),
                    WorkflowStep(
                        run_id=run.id, name=_retrieval_manifest([("news", chunk_id, 1)])
                    ),
                ]
            )
            await session.execute(
                text(
                    "INSERT INTO ai_findings (id, run_id, finding_key, version, "
                    "risk_type, claim_code, title, summary, reasoning, citations, "
                    "evidence_sufficiency, confidence_level, review_recommended) "
                    "VALUES (:id, :run, 'legacy-news', 1, 'news', 'NEGATIVE_NEWS', "
                    "'News', 'News', 'News', CAST(:citation AS jsonb), "
                    "'sufficient', 'high', true)"
                ),
                {
                    "id": uuid4(),
                    "run": run.id,
                    "citation": '[{"chunk_id":"'
                    + str(chunk_id)
                    + '","content_hash":"'
                    + source.content_hash
                    + '"}]',
                },
            )
            await session.execute(
                text(
                    "INSERT INTO risk_reports (id, run_id, snapshot, html_content) "
                    "VALUES (:id, :run, CAST(:snapshot AS jsonb), "
                    "'<p>Legacy report</p>')"
                ),
                {"id": uuid4(), "run": run.id, "snapshot": '{"legacy":true}'},
            )
        columns = (
            "id,evidence_id,company_id,observed_at,fact_type,fact_key,"
            "fact_value,polarity,content,content_hash"
        )
        tables = {
            "evidence_chunks": columns,
            "retrieved_evidence": "*",
            "workflow_steps": "*",
            "ai_findings": (
                "id,run_id,finding_key,version,risk_type,claim_code,title,summary,"
                "reasoning,citations,evidence_sufficiency,confidence_level,"
                "review_recommended"
            ),
            "risk_reports": "*",
            "evidence": "*",
        }

        async def snapshots():
            async with sessions() as session:
                return {
                    table: (
                        await session.execute(
                            text(
                                "SELECT row_to_json(t)::text FROM "
                                f"(SELECT {cols} FROM {table}) t ORDER BY 1"
                            )
                        )
                    )
                    .scalars()
                    .all()
                    for table, cols in tables.items()
                }

        before = await snapshots()
        command.upgrade(config, "head")
        assert await snapshots() == before
        async with sessions.begin() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(EmbeddingIndexVersion)
                )
                == 0
            )
            chunk = await session.get(EvidenceChunk, chunk_id)
            assert chunk.chunk_schema_version == "legacy-stage3"
            assert (
                chunk.evidence_content_hash == chunk.content_hash == source.content_hash
            )
            await retrieve_evidence(session, run_id=run.id)
            context = await build_risk_context(session, run_id=run.id)
            assert (
                context.evidence_chunks[0].evidence_content_hash == source.content_hash
            )
            await build_index(session, FakeEmbedding(), IndexContract.fake())
            candidates = (
                await session.scalars(select(EvidenceChunk).join(ChunkEmbedding))
            ).all()
            assert len(candidates) == 2
            assert all(
                c.chunk_schema_version == "v1" and c.id != chunk_id for c in candidates
            )
        # Indexing adds canonical chunks, never rewrites legacy citations or reports.
        after = await snapshots()
        for table in tables:
            if table != "evidence_chunks":
                assert after[table] == before[table]
        assert set(before["evidence_chunks"]).issubset(after["evidence_chunks"])
    finally:
        await engine.dispose()


async def test_cli_and_startup_without_bge(database: Settings, monkeypatch):
    from tracerisk.main import app, lifespan
    from tracerisk.workflows import worker

    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDING_MODEL", "fake-sha256-v1")
    async with lifespan(app):
        pass  # API permits an unindexed deployment.
    with pytest.raises(IndexReadinessError, match="No active"):
        await worker.serve()
    script = (
        "import sys, runpy; "
        "sys.modules['sentence_transformers'] = None; "
        "sys.argv = ['index_cli', '--provider', 'fake']; "
        "runpy.run_module('tracerisk.retrieval.index_cli', run_name='__main__')"
    )
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr
        assert "Active index: fake:fake-sha256-v1:512:cosine:v1" in result.stdout
    async with lifespan(app):
        pass

    class Queue:
        @asynccontextmanager
        async def open_async(self):
            yield self

        async def run_worker_async(self, *, queues):
            assert queues

    monkeypatch.setattr(worker, "create_worker", lambda *args: Queue())
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    await worker.serve()
    monkeypatch.setenv("EMBEDDING_MODEL", "wrong")
    with pytest.raises(IndexReadinessError, match="incompatible"):
        async with lifespan(app):
            pass
    with pytest.raises(IndexReadinessError, match="incompatible"):
        await worker.serve()


async def test_downgrade_refused_after_canonical_indexing(database: Settings):
    """The downgrade guard must leave paragraph snapshots/index/schema intact."""
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    try:
        async with sessions.begin() as session:
            await seed(session)
            await build_index(session, FakeEmbedding(), IndexContract.fake())
        with pytest.raises(RuntimeError, match="Cannot downgrade after canonical"):
            command.downgrade(config, "0009")
        async with sessions() as session:
            assert (
                await session.scalar(text("SELECT version_num FROM alembic_version"))
                == "0012"
            )
            assert (
                await session.scalar(select(func.count()).select_from(EvidenceChunk))
                == 2
            )
            assert (
                await session.scalar(select(func.count()).select_from(ChunkEmbedding))
                == 2
            )
            assert await check_index(session, IndexContract.fake()) is not None
    finally:
        await engine.dispose()
