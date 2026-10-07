"""Atomic, caller-owned index construction and fail-closed readiness checks."""

from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.companies import models as company_models  # noqa: F401
from tracerisk.config import Settings
from tracerisk.evidence.models import Evidence, EvidenceChunk
from tracerisk.retrieval.chunking import ChunkDraft, chunk_evidence
from tracerisk.retrieval.embeddings import EmbeddingProvider, validate_vectors
from tracerisk.retrieval.models import (
    ChunkEmbedding,
    EmbeddingIndexVersion,
    IndexQueryEmbedding,
)
from tracerisk.retrieval.queries import QUERY_VERSION as QUERY_VERSION
from tracerisk.retrieval.queries import RISK_QUERIES as RISK_QUERIES

_ACTIVATION_LOCK = 742091004


class IndexReadinessError(ValueError):
    """A missing, corrupt or incompatible index cannot serve retrieval."""


@dataclass(frozen=True)
class IndexContract:
    provider: str
    model: str
    dimensions: int = 512
    distance: str = "cosine"
    chunk_schema_version: str = "v1"

    @classmethod
    def fake(cls) -> "IndexContract":
        return cls("fake", "fake-sha256-v1")

    @classmethod
    def from_settings(cls, settings: Settings) -> "IndexContract":
        return cls(
            settings.embedding_provider,
            settings.embedding_model,
            settings.embedding_dimensions,
            settings.embedding_distance,
            settings.chunk_schema_version,
        )

    @property
    def version_key(self) -> str:
        return ":".join(str(value) for value in asdict(self).values())


def _compatible(version: EmbeddingIndexVersion, contract: IndexContract) -> None:
    if any(
        getattr(version, field) != value for field, value in asdict(contract).items()
    ):
        raise IndexReadinessError("Active embedding index is incompatible")


async def _documents(
    session: AsyncSession,
    company_id: UUID | None = None,
    as_of_date: date | None = None,
    risk_type: str | None = None,
) -> list[Evidence]:
    query = select(Evidence).order_by(Evidence.id)
    if company_id is not None:
        query = query.where(Evidence.company_id == company_id)
    if as_of_date is not None:
        query = query.where(Evidence.observed_at <= as_of_date)
    if risk_type is not None:
        query = query.where(Evidence.risk_type == risk_type)
    return list((await session.scalars(query)).all())


def _drafts(evidence: Evidence) -> tuple[ChunkDraft, ...]:
    if sha256(evidence.content.encode("utf-8")).hexdigest() != evidence.content_hash:
        raise IndexReadinessError("Evidence content digest differs")
    drafts = chunk_evidence(evidence)
    if not drafts:
        raise IndexReadinessError("Eligible evidence has no canonical chunks")
    return drafts


async def check_index(
    session: AsyncSession,
    contract: IndexContract,
    *,
    required: bool = True,
    company_id: UUID | None = None,
    as_of_date: date | None = None,
    risk_type: str | None = None,
) -> EmbeddingIndexVersion | None:
    """API checks compatibility only; workers check documents and four queries.

    An empty scoped domain is ready; eligible evidence missing any canonical
    paragraph/vector is not. No embedding provider is loaded by this path.
    """
    version = await session.scalar(
        select(EmbeddingIndexVersion).where(EmbeddingIndexVersion.active)
    )
    if version is None:
        if required:
            raise IndexReadinessError("No active embedding index")
        return None
    _compatible(version, contract)
    if not required:
        return version
    for evidence in await _documents(session, company_id, as_of_date, risk_type):
        for draft in _drafts(evidence):
            chunk = await session.get(EvidenceChunk, draft.id)
            if chunk is None or any(
                getattr(chunk, key) != value for key, value in asdict(draft).items()
            ):
                raise IndexReadinessError("Evidence is missing its canonical Chunk")
            vector = await session.get(ChunkEmbedding, (draft.id, version.id))
            if vector is None:
                raise IndexReadinessError("Eligible evidence is not indexed")
            try:
                validate_vectors([vector.embedding], 1)
            except ValueError as exc:
                raise IndexReadinessError("Invalid document vector") from exc
    queries = list(
        (
            await session.scalars(
                select(IndexQueryEmbedding).where(
                    IndexQueryEmbedding.index_version_id == version.id,
                    IndexQueryEmbedding.query_version == QUERY_VERSION,
                )
            )
        ).all()
    )
    by_risk = {query.risk_type: query for query in queries}
    if set(by_risk) != set(RISK_QUERIES):
        raise IndexReadinessError("Four fixed query embeddings are required")
    for risk, query_text in RISK_QUERIES.items():
        query = by_risk[risk]
        if query.query_hash != sha256(query_text.encode("utf-8")).hexdigest():
            raise IndexReadinessError("Fixed query hash differs")
        try:
            validate_vectors([query.embedding], 1)
        except ValueError as exc:
            raise IndexReadinessError("Invalid query vector") from exc
    return version


async def build_index(
    session: AsyncSession, provider: EmbeddingProvider, contract: IndexContract
) -> EmbeddingIndexVersion:
    """Never commit: caller must roll back the entire job on any failure."""
    # A transaction advisory lock also serializes the initial empty-table case.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ACTIVATION_LOCK}
    )
    if (
        contract.dimensions != 512
        or contract.distance != "cosine"
        or contract.chunk_schema_version != "v1"
        or any(
            getattr(provider, key) != getattr(contract, key)
            for key in ("provider", "model", "dimensions", "distance")
        )
    ):
        raise IndexReadinessError("Unsupported embedding provider contract")
    await check_index(session, contract, required=False)
    version = await session.scalar(
        select(EmbeddingIndexVersion).where(
            EmbeddingIndexVersion.version_key == contract.version_key
        )
    )
    if version is None:
        version = EmbeddingIndexVersion(
            version_key=contract.version_key, **asdict(contract)
        )
        session.add(version)
        await session.flush()
    _compatible(version, contract)
    missing: list[EvidenceChunk] = []
    for evidence in await _documents(session):
        for draft in _drafts(evidence):
            chunk = await session.get(EvidenceChunk, draft.id)
            if chunk is None:
                chunk = EvidenceChunk(**asdict(draft))
                session.add(chunk)
            elif any(
                getattr(chunk, key) != value for key, value in asdict(draft).items()
            ):
                raise IndexReadinessError("Canonical immutable Chunk differs")
            existing_vector = await session.get(ChunkEmbedding, (draft.id, version.id))
            if existing_vector is None:
                missing.append(chunk)
            else:
                validate_vectors([existing_vector.embedding], 1)
    if missing:
        vectors = validate_vectors(
            await provider.embed_documents([chunk.content for chunk in missing]),
            len(missing),
        )
        for chunk, vector in zip(missing, vectors, strict=True):
            session.add(
                ChunkEmbedding(
                    chunk_id=chunk.id, index_version_id=version.id, embedding=vector
                )
            )
    missing_queries: list[tuple[str, str]] = []
    for risk, query_text in RISK_QUERIES.items():
        query = await session.scalar(
            select(IndexQueryEmbedding).where(
                IndexQueryEmbedding.index_version_id == version.id,
                IndexQueryEmbedding.query_version == QUERY_VERSION,
                IndexQueryEmbedding.risk_type == risk,
            )
        )
        if query is None:
            missing_queries.append((risk, query_text))
        elif query.query_hash != sha256(query_text.encode("utf-8")).hexdigest():
            raise IndexReadinessError("Fixed query hash differs")
        else:
            validate_vectors([query.embedding], 1)
    if missing_queries:
        vectors = validate_vectors(
            await provider.embed_queries([q for _, q in missing_queries]),
            len(missing_queries),
        )
        for (risk, query_text), vector in zip(missing_queries, vectors, strict=True):
            session.add(
                IndexQueryEmbedding(
                    index_version_id=version.id,
                    query_version=QUERY_VERSION,
                    risk_type=risk,
                    query_hash=sha256(query_text.encode("utf-8")).hexdigest(),
                    embedding=vector,
                )
            )
    version.active = True
    await session.flush()
    await check_index(session, contract)
    return version
