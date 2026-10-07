"""Read-only four-domain retrieval; workflow owns persistence and error mapping."""

from collections.abc import Sequence
from dataclasses import dataclass, fields
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.auth.models import Role, User
from tracerisk.evidence.models import Evidence, EvidenceChunk
from tracerisk.investigations.models import Investigation
from tracerisk.retrieval.chunking import ChunkDraft
from tracerisk.retrieval.indexing import IndexContract, IndexReadinessError, check_index
from tracerisk.retrieval.keyword import KeywordDocument, RankedScore, rank_keyword
from tracerisk.retrieval.models import ChunkEmbedding
from tracerisk.retrieval.queries import QUERY_VERSION, RiskQuery, workflow_queries
from tracerisk.retrieval.vector import load_query_vector, rank_vector


@dataclass(frozen=True)
class RetrievalCandidate:
    chunk: ChunkDraft
    title: str


@dataclass(frozen=True)
class RetrievalSelection:
    chunk: ChunkDraft
    title: str
    rank: int
    score: float
    keyword_rank: int | None
    vector_rank: int | None


@dataclass(frozen=True)
class HybridResult:
    index_version_id: UUID
    query_version: str
    selections: tuple[RetrievalSelection, ...]


def fuse_rankings(
    candidates: Sequence[RetrievalCandidate],
    keyword: Sequence[RankedScore],
    vector: Sequence[RankedScore],
) -> tuple[RetrievalSelection, ...]:
    """RRF(k=60), then lexical rank, vector rank and Chunk ID; unique Top 5."""
    by_id = {candidate.chunk.id: candidate for candidate in candidates}
    if len({candidate.chunk.risk_type for candidate in candidates}) > 1:
        raise ValueError("Fusion requires a single risk domain")
    ranks = []
    for ranking in (keyword, vector):
        values = {row.chunk_id: row.rank for row in ranking}
        if not values.keys() <= by_id.keys() or any(r < 1 for r in values.values()):
            raise ValueError("Ranking contains unscoped candidates or invalid ranks")
        ranks.append(values)
    lexical, semantic = ranks
    scores = {
        chunk_id: sum(1.0 / (60 + rank[chunk_id]) for rank in ranks if chunk_id in rank)
        for chunk_id in lexical.keys() | semantic.keys()
    }
    ordered = sorted(
        scores,
        key=lambda chunk_id: (
            -scores[chunk_id],
            lexical.get(chunk_id, float("inf")),
            semantic.get(chunk_id, float("inf")),
            chunk_id,
        ),
    )
    seen: set[tuple[UUID, str]] = set()
    result: list[RetrievalSelection] = []
    for chunk_id in ordered:
        candidate = by_id[chunk_id]
        key = (candidate.chunk.evidence_id, candidate.chunk.evidence_content_hash)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            RetrievalSelection(
                candidate.chunk,
                candidate.title,
                len(result) + 1,
                scores[chunk_id],
                lexical.get(chunk_id),
                semantic.get(chunk_id),
            )
        )
        if len(result) == 5:
            break
    return tuple(result)


async def select_hybrid(
    session: AsyncSession,
    *,
    investigation: Investigation,
    contract: IndexContract,
) -> HybridResult:
    """Resolve background principal before readiness or access to evidence."""
    return await _select_queries(
        session,
        investigation=investigation,
        contract=contract,
        queries=[(query, None) for query in workflow_queries()],
    )


async def select_evaluation_query(
    session: AsyncSession,
    *,
    investigation: Investigation,
    contract: IndexContract,
    query: RiskQuery,
    query_vector: Sequence[float],
) -> HybridResult:
    """Explicit evaluation supplies a matching provider's case-specific vector."""
    return await _select_queries(
        session,
        investigation=investigation,
        contract=contract,
        queries=[(query, query_vector)],
    )


async def _select_queries(
    session: AsyncSession,
    *,
    investigation: Investigation,
    contract: IndexContract,
    queries: Sequence[tuple[RiskQuery, Sequence[float] | None]],
) -> HybridResult:
    with session.no_autoflush:
        principal = await session.get(User, investigation.created_by)
        if principal is None or principal.role not in {role.value for role in Role}:
            raise PermissionError("Investigation creator cannot read demo evidence")
        version = await check_index(
            session,
            contract,
            company_id=investigation.company_id,
            as_of_date=investigation.as_of_date,
        )
        if version is None:
            raise IndexReadinessError("No active embedding index")
        selections: list[RetrievalSelection] = []
        for query, supplied_vector in queries:
            query_vector = (
                supplied_vector
                if supplied_vector is not None
                else await load_query_vector(
                    session, index_version_id=version.id, query=query, contract=contract
                )
            )
            rows = (
                await session.execute(
                    select(EvidenceChunk, Evidence.title)
                    .join(
                        Evidence,
                        (Evidence.id == EvidenceChunk.evidence_id)
                        & (Evidence.company_id == EvidenceChunk.company_id)
                        & (
                            Evidence.content_hash == EvidenceChunk.evidence_content_hash
                        ),
                    )
                    .join(ChunkEmbedding, ChunkEmbedding.chunk_id == EvidenceChunk.id)
                    .where(
                        Evidence.company_id == investigation.company_id,
                        Evidence.observed_at <= investigation.as_of_date,
                        Evidence.risk_type == query.risk_type,
                        EvidenceChunk.company_id == investigation.company_id,
                        EvidenceChunk.observed_at <= investigation.as_of_date,
                        EvidenceChunk.risk_type == query.risk_type,
                        EvidenceChunk.chunk_schema_version
                        == version.chunk_schema_version,
                        ChunkEmbedding.index_version_id == version.id,
                    )
                    .order_by(EvidenceChunk.id)
                )
            ).all()
            candidates = tuple(
                RetrievalCandidate(
                    ChunkDraft(
                        **{f.name: getattr(chunk, f.name) for f in fields(ChunkDraft)}
                    ),
                    title,
                )
                for chunk, title in rows
            )
            keyword = rank_keyword(
                query.text,
                [
                    KeywordDocument(
                        candidate.chunk.id,
                        candidate.chunk.content,
                        candidate.title,
                        candidate.chunk.fact_type,
                        candidate.chunk.fact_key,
                        candidate.chunk.fact_value,
                        candidate.chunk.polarity,
                    )
                    for candidate in candidates
                ],
            )
            vector = await rank_vector(
                session,
                candidate_ids=[candidate.chunk.id for candidate in candidates],
                index_version_id=version.id,
                query_vector=query_vector,
            )
            selections.extend(fuse_rankings(candidates, keyword, vector))
        return HybridResult(version.id, QUERY_VERSION, tuple(selections))
