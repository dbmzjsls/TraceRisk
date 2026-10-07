import builtins
from dataclasses import asdict, replace
from datetime import date
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tracerisk.auth.models import User
from tracerisk.companies.models import Company
from tracerisk.evidence.models import Evidence, EvidenceChunk, RetrievedEvidence
from tracerisk.investigations.models import Investigation
from tracerisk.retrieval import hybrid
from tracerisk.retrieval.chunking import chunk_evidence
from tracerisk.retrieval.embeddings import BGEEmbedding, FakeEmbedding
from tracerisk.retrieval.hybrid import select_hybrid
from tracerisk.retrieval.indexing import IndexContract, IndexReadinessError, build_index
from tracerisk.retrieval.models import (
    ChunkEmbedding,
    EmbeddingIndexVersion,
    IndexQueryEmbedding,
)
from tracerisk.retrieval.queries import workflow_queries
from tracerisk.retrieval.vector import load_query_vector, rank_vector

pytestmark = pytest.mark.anyio
BASELINE = date(2026, 9, 16)
AXIS = [1.0] + [0.0] * 511


async def seed(session, per_risk=7):
    companies = [
        Company(
            synthetic_key=f"hybrid-{n}",
            name=f"Company {n}",
            unified_credit_code=f"HYBRID-{n}",
        )
        for n in range(2)
    ]
    user = User(username="hybrid-reader", password_hash="unused", role="analyst")
    session.add_all([*companies, user])
    await session.flush()
    sources = []
    for query in workflow_queries():
        for n in range(per_risk + 2):
            content = query.text + (
                "\n\n另一段新闻" if query.risk_type == "news" else ""
            )
            evidence = Evidence(
                company_id=companies[n == per_risk].id,
                source_type=query.risk_type,
                risk_type=query.risk_type,
                source_uri=f"synthetic://hybrid/{query.risk_type}/{n}",
                title=query.text,
                observed_at=date(2026, 9, 17) if n == per_risk + 1 else BASELINE,
                fact_type="event",
                fact_key=str(n),
                fact_value="negative",
                polarity="negative",
                content=content,
                content_hash=sha256(content.encode()).hexdigest(),
            )
            session.add(evidence)
            sources.append(evidence)
    await session.flush()
    investigation = Investigation(
        company_id=companies[0].id, created_by=user.id, as_of_date=BASELINE
    )
    version = await build_index(session, FakeEmbedding(), IndexContract.fake())
    return investigation, version, sources


async def test_scoped_before_rankers_independent_top5_and_no_writes(
    database, monkeypatch
):
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            investigation, version, sources = await seed(session)
            # Even an active embedding cannot make a legacy Chunk eligible.
            legacy = EvidenceChunk(
                **{
                    **asdict(chunk_evidence(sources[0])[0]),
                    "id": uuid4(),
                    "chunk_schema_version": "legacy-stage3",
                }
            )
            session.add(legacy)
            await session.flush()
            session.add(
                ChunkEmbedding(
                    chunk_id=legacy.id, index_version_id=version.id, embedding=AXIS
                )
            )
            inactive = EmbeddingIndexVersion(
                version_key="inactive",
                provider="fake",
                model="old",
                dimensions=512,
                distance="cosine",
                chunk_schema_version="v1",
                active=False,
            )
            session.add(inactive)
            await session.flush()
            inactive_chunk = EvidenceChunk(
                **{**asdict(chunk_evidence(sources[0])[0]), "id": uuid4()}
            )
            session.add(inactive_chunk)
            await session.flush()
            session.add(
                ChunkEmbedding(
                    chunk_id=inactive_chunk.id,
                    index_version_id=inactive.id,
                    embedding=AXIS,
                )
            )
            await session.flush()

            expected = {
                query.risk_type: {
                    draft.id
                    for source in sources
                    if source.company_id == investigation.company_id
                    and source.observed_at <= BASELINE
                    and source.risk_type == query.risk_type
                    for draft in chunk_evidence(source)
                }
                for query in workflow_queries()
            }
            calls = []
            original_keyword, original_vector = hybrid.rank_keyword, hybrid.rank_vector

            def keyword_spy(query, documents):
                risk = next(q.risk_type for q in workflow_queries() if q.text == query)
                ids = {d.chunk_id for d in documents}
                assert ids == expected[risk]
                calls.append(("keyword", risk))
                return original_keyword(query, documents)

            async def vector_spy(
                session, *, candidate_ids, index_version_id, query_vector
            ):
                ids = set(candidate_ids)
                assert ids in expected.values()
                assert index_version_id == version.id
                risk = next(r for r, values in expected.items() if values == ids)
                calls.append(("vector", risk))
                return await original_vector(
                    session,
                    candidate_ids=candidate_ids,
                    index_version_id=index_version_id,
                    query_vector=query_vector,
                )

            async def forbidden_embedding(*args, **kwargs):
                raise AssertionError("Workflow must use stored query vectors")

            original_import = builtins.__import__

            def guarded_import(name, *args, **kwargs):
                assert not name.startswith("sentence_transformers")
                return original_import(name, *args, **kwargs)

            monkeypatch.setattr(builtins, "__import__", guarded_import)
            for provider in (FakeEmbedding, BGEEmbedding):
                monkeypatch.setattr(provider, "embed_queries", forbidden_embedding)
                monkeypatch.setattr(provider, "embed_documents", forbidden_embedding)
            monkeypatch.setattr(hybrid, "rank_keyword", keyword_spy)
            monkeypatch.setattr(hybrid, "rank_vector", vector_spy)
            result = await select_hybrid(
                session, investigation=investigation, contract=IndexContract.fake()
            )
            assert result == await select_hybrid(
                session, investigation=investigation, contract=IndexContract.fake()
            )
            assert result.index_version_id == version.id
            assert result.query_version == "stage4-risk-queries-v1"
            assert len(calls) == 16
            assert len(result.selections) == 20
            for risk, ids in expected.items():
                selected = [r for r in result.selections if r.chunk.risk_type == risk]
                assert [r.rank for r in selected] == [1, 2, 3, 4, 5]
                assert {r.chunk.id for r in selected} <= ids
                assert (
                    len(
                        {
                            (r.chunk.evidence_id, r.chunk.evidence_content_hash)
                            for r in selected
                        }
                    )
                    == 5
                )
            assert (
                await session.scalar(
                    select(func.count()).select_from(RetrievedEvidence)
                )
                == 0
            )
            assert not session.new and not session.dirty and not session.deleted
    finally:
        await engine.dispose()


async def test_creator_permissions_checked_before_any_ranker(database, monkeypatch):
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine)
    try:
        async with sessions.begin() as session:
            investigation, _, _ = await seed(session, 1)
            investigation.created_by = uuid4()

            def forbidden(*args, **kwargs):
                raise AssertionError("Unauthorized evidence reached a ranker")

            monkeypatch.setattr(hybrid, "check_index", forbidden)
            monkeypatch.setattr(hybrid, "rank_keyword", forbidden)
            monkeypatch.setattr(hybrid, "rank_vector", forbidden)
            with pytest.raises(PermissionError, match="cannot read"):
                await select_hybrid(
                    session, investigation=investigation, contract=IndexContract.fake()
                )
            assert (
                await session.scalar(
                    select(func.count()).select_from(RetrievedEvidence)
                )
                == 0
            )
    finally:
        await engine.dispose()


async def test_pgvector_cosine_order_uses_stored_query_and_stable_ties(database):
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            investigation, version, sources = await seed(session, 3)
            query = workflow_queries()[0]
            stored = await session.scalar(
                select(IndexQueryEmbedding).where(
                    IndexQueryEmbedding.risk_type == query.risk_type
                )
            )
            stored.embedding = AXIS
            ids = sorted(chunk_evidence(source)[0].id for source in sources[:3])
            for chunk_id, vector in zip(
                ids, [AXIS, AXIS, [-v for v in AXIS]], strict=True
            ):
                row = await session.get(ChunkEmbedding, (chunk_id, version.id))
                row.embedding = vector
            await session.flush()
            vector = await load_query_vector(
                session,
                index_version_id=version.id,
                query=query,
                contract=IndexContract.fake(),
            )
            assert vector == AXIS
            for invalid_query in (
                replace(query, text="unversioned edit"),
                replace(query, version="wrong-version"),
                replace(query, risk_type="unknown"),
            ):
                with pytest.raises(IndexReadinessError):
                    await load_query_vector(
                        session,
                        index_version_id=version.id,
                        query=invalid_query,
                        contract=IndexContract.fake(),
                    )
            result = await rank_vector(
                session,
                candidate_ids=ids[::-1],
                index_version_id=version.id,
                query_vector=vector,
            )
            assert [row.chunk_id for row in result] == ids
            assert [row.rank for row in result] == [1, 2, 3]
            assert [row.score for row in result] == pytest.approx([1, 1, -1])
            assert (
                await rank_vector(
                    session,
                    candidate_ids=[],
                    index_version_id=version.id,
                    query_vector=vector,
                )
                == ()
            )
            investigation.as_of_date = date(1900, 1, 1)
            empty = await select_hybrid(
                session, investigation=investigation, contract=IndexContract.fake()
            )
            assert empty.selections == ()
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "version",
        "hash",
        "index",
        "zero",
        "incompatible",
        "missing_document",
        "inactive",
    ],
)
@pytest.mark.parametrize("query_number", [0, 3])
async def test_query_and_document_readiness_fail_before_ranking(
    database, monkeypatch, corruption, query_number
):
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            investigation, version, sources = await seed(session, 1)
            query = workflow_queries()[query_number]
            stored = await session.scalar(
                select(IndexQueryEmbedding).where(
                    IndexQueryEmbedding.risk_type == query.risk_type
                )
            )
            if corruption == "missing":
                await session.delete(stored)
            elif corruption == "version":
                stored.query_version = "wrong-version"
            elif corruption == "hash":
                stored.query_hash = "f" * 64
            elif corruption == "zero":
                stored.embedding = [0.0] * 512
            elif corruption == "incompatible":
                version.model = "incompatible"
            elif corruption == "inactive":
                version.active = False
            elif corruption == "index":
                other = EmbeddingIndexVersion(
                    version_key="other",
                    provider="fake",
                    model="old",
                    dimensions=512,
                    distance="cosine",
                    chunk_schema_version="v1",
                    active=False,
                )
                session.add(other)
                await session.flush()
                stored.index_version_id = other.id
            else:
                row = await session.get(
                    ChunkEmbedding, (chunk_evidence(sources[0])[0].id, version.id)
                )
                await session.delete(row)
            await session.flush()

            def forbidden(*args, **kwargs):
                raise AssertionError("Unready index reached a ranker")

            monkeypatch.setattr(hybrid, "rank_keyword", forbidden)
            monkeypatch.setattr(hybrid, "rank_vector", forbidden)
            with pytest.raises(IndexReadinessError):
                await select_hybrid(
                    session, investigation=investigation, contract=IndexContract.fake()
                )
            if corruption != "missing_document":
                with pytest.raises(IndexReadinessError):
                    await load_query_vector(
                        session,
                        index_version_id=version.id,
                        query=query,
                        contract=IndexContract.fake(),
                    )
            with pytest.raises(IndexReadinessError):
                await load_query_vector(
                    session,
                    index_version_id=uuid4(),
                    query=query,
                    contract=IndexContract.fake(),
                )
            with pytest.raises(IndexReadinessError):
                await load_query_vector(
                    session,
                    index_version_id=version.id,
                    query=replace(query, text="unversioned edit"),
                    contract=IndexContract.fake(),
                )
    finally:
        await engine.dispose()
