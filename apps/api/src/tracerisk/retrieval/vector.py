"""Stored workflow query vectors and scoped pgvector cosine ranking."""

from collections.abc import Sequence
from math import isfinite
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.retrieval.embeddings import validate_vectors
from tracerisk.retrieval.indexing import IndexContract, IndexReadinessError, check_index
from tracerisk.retrieval.keyword import RankedScore
from tracerisk.retrieval.models import ChunkEmbedding, IndexQueryEmbedding
from tracerisk.retrieval.queries import QUERY_VERSION, RISK_QUERIES, RiskQuery


async def load_query_vector(
    session: AsyncSession,
    *,
    index_version_id: UUID,
    query: RiskQuery,
    contract: IndexContract,
) -> list[float]:
    """Require the active compatible index and the exact fixed query contract."""
    version = await check_index(session, contract, required=False)
    if version is None or version.id != index_version_id:
        raise IndexReadinessError("Query index is not active")
    if (
        query.version != QUERY_VERSION
        or RISK_QUERIES.get(query.risk_type) != query.text
    ):
        raise IndexReadinessError("Unknown fixed query contract")
    stored = await session.scalar(
        select(IndexQueryEmbedding).where(
            IndexQueryEmbedding.index_version_id == index_version_id,
            IndexQueryEmbedding.query_version == query.version,
            IndexQueryEmbedding.risk_type == query.risk_type,
        )
    )
    if stored is None or stored.query_hash != query.content_hash:
        raise IndexReadinessError("Fixed query embedding missing or hash differs")
    try:
        return validate_vectors([stored.embedding], 1)[0]
    except ValueError as exc:
        raise IndexReadinessError("Invalid query vector") from exc


async def rank_vector(
    session: AsyncSession,
    *,
    candidate_ids: Sequence[UUID],
    index_version_id: UUID,
    query_vector: Sequence[float],
) -> tuple[RankedScore, ...]:
    """Rank only IDs already authorized and scoped by the hybrid service."""
    try:
        vector = validate_vectors([query_vector], 1)[0]
    except ValueError as exc:
        raise IndexReadinessError("Invalid query vector") from exc
    if not candidate_ids:
        return ()
    distance = ChunkEmbedding.embedding.cosine_distance(vector).label("distance")
    rows = (
        await session.execute(
            select(ChunkEmbedding.chunk_id, distance)
            .where(
                ChunkEmbedding.chunk_id.in_(candidate_ids),
                ChunkEmbedding.index_version_id == index_version_id,
            )
            .order_by(distance, ChunkEmbedding.chunk_id)
        )
    ).all()
    if len(rows) != len(set(candidate_ids)) or any(
        not isfinite(row.distance) for row in rows
    ):
        raise IndexReadinessError("Candidate vectors missing or invalid")
    return tuple(
        RankedScore(row.chunk_id, rank, 1.0 - row.distance)
        for rank, row in enumerate(rows, 1)
    )
