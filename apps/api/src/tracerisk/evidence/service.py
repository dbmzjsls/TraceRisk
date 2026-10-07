"""Atomic hybrid selection and immutable, hash-bound retrieval replay."""

import hashlib
import json
from dataclasses import asdict, fields
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.ai.schemas import EvidenceContext, RiskContext
from tracerisk.config import Settings
from tracerisk.evidence.models import EvidenceChunk, RetrievedEvidence
from tracerisk.investigations.models import Investigation, WorkflowRun, WorkflowStep
from tracerisk.retrieval.chunking import ChunkDraft
from tracerisk.retrieval.hybrid import select_hybrid
from tracerisk.retrieval.indexing import _ACTIVATION_LOCK, IndexContract
from tracerisk.retrieval.models import EmbeddingIndexVersion
from tracerisk.retrieval.queries import QUERY_VERSION
from tracerisk.workflows.events import append_workflow_event

RISK_ORDER = ("business", "judicial", "news", "intellectual_property")
_RISK_POSITION = {risk: position for position, risk in enumerate(RISK_ORDER)}
RETRIEVAL_MANIFEST_PREFIX = "retrieve_evidence_manifest:"


class RetrievalIntegrityError(ValueError):
    """A frozen retrieval must never be repaired from the live catalog."""


def _retrieval_manifest(rows: list[tuple[str, UUID, int]]) -> str:
    """The historical Stage 3 manifest format remains readable."""
    ordered = sorted(
        rows, key=lambda row: (_RISK_POSITION[row[0]], row[2], str(row[1]))
    )
    payload = "\n".join(f"{risk}:{chunk_id}:{rank}" for risk, chunk_id, rank in ordered)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{RETRIEVAL_MANIFEST_PREFIX}{len(ordered)}:{digest}"


async def current_investigation(session: AsyncSession, run_id: UUID) -> Investigation:
    investigation = await session.scalar(
        select(Investigation)
        .join(WorkflowRun, WorkflowRun.investigation_id == Investigation.id)
        .where(WorkflowRun.id == run_id)
        .with_for_update(of=Investigation)
    )
    run = await session.get(WorkflowRun, run_id, with_for_update=True)
    if (
        investigation is None
        or run is None
        or investigation.current_run_id != run_id
        or run.status != "running"
    ):
        raise ValueError("Run is not current and running")
    return investigation


async def step_completed(session: AsyncSession, run_id: UUID, name: str) -> bool:
    return (
        await session.scalar(
            select(WorkflowStep.id).where(
                WorkflowStep.run_id == run_id, WorkflowStep.name == name
            )
        )
        is not None
    )


def _snapshot_manifest(
    investigation: Investigation,
    version: EmbeddingIndexVersion,
    rows: list[tuple[EvidenceChunk, RetrievedEvidence]],
) -> str:
    # A bounded indexed name binds every field, both hashes, ranks and scope.
    payload = {
        "company_id": str(investigation.company_id),
        "as_of_date": str(investigation.as_of_date),
        "principal": str(investigation.created_by),
        "index": {
            key: str(getattr(version, key))
            for key in (
                "id",
                "version_key",
                "provider",
                "model",
                "dimensions",
                "distance",
                "chunk_schema_version",
            )
        },
        "query_version": QUERY_VERSION,
        "rows": [
            {
                "rank": retrieval.rank,
                "risk_type": retrieval.risk_type,
                "chunk": {
                    field.name: str(getattr(chunk, field.name))
                    for field in fields(ChunkDraft)
                },
            }
            for chunk, retrieval in rows
        ],
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return (
        f"{RETRIEVAL_MANIFEST_PREFIX}v2:{QUERY_VERSION}:"
        f"{version.id}:{len(rows)}:{digest}"
    )


async def _saved_rows(
    session: AsyncSession, run_id: UUID
) -> list[tuple[EvidenceChunk, RetrievedEvidence]]:
    # Read persisted scalar values without accepting or overwriting dirty ORM facts.
    rows = (
        await session.execute(
            select(*EvidenceChunk.__table__.c, *RetrievedEvidence.__table__.c)
            .join(RetrievedEvidence, RetrievedEvidence.chunk_id == EvidenceChunk.id)
            .where(RetrievedEvidence.run_id == run_id)
        )
    ).all()
    return sorted(
        [
            (
                EvidenceChunk(
                    **{
                        column.name: row._mapping[column]
                        for column in EvidenceChunk.__table__.c
                    }
                ),
                RetrievedEvidence(
                    **{
                        column.name: row._mapping[column]
                        for column in RetrievedEvidence.__table__.c
                    }
                ),
            )
            for row in rows
        ],
        key=lambda row: (row[1].risk_type, row[1].rank, str(row[0].id)),
    )


async def _manifests(session: AsyncSession, run_id: UUID) -> list[str]:
    return list(
        (
            await session.scalars(
                select(WorkflowStep.name).where(
                    WorkflowStep.run_id == run_id,
                    WorkflowStep.name.startswith(RETRIEVAL_MANIFEST_PREFIX),
                )
            )
        ).all()
    )


async def load_frozen_retrieval(
    session: AsyncSession, *, run_id: UUID
) -> list[tuple[EvidenceChunk, RetrievedEvidence]]:
    """Validate the committed selection without consulting live Evidence.

    Legacy manifests retain their identity/rank digest. New manifests bind all
    frozen fields and the active-at-selection version, even after reindexing.
    """
    investigation = await session.scalar(
        select(Investigation)
        .join(WorkflowRun, WorkflowRun.investigation_id == Investigation.id)
        .where(WorkflowRun.id == run_id)
    )
    manifests = await _manifests(session, run_id)
    if (
        investigation is None
        or len(manifests) != 1
        or not await step_completed(session, run_id, "retrieve_evidence")
    ):
        raise RetrievalIntegrityError(
            "Retrieval manifest or completion marker is missing"
        )
    rows = await _saved_rows(session, run_id)
    manifest = manifests[0]
    modern = manifest.startswith(f"{RETRIEVAL_MANIFEST_PREFIX}v2:")
    ranks = {risk: 0 for risk in RISK_ORDER}
    seen: set[tuple[str, UUID, str]] = set()
    for chunk, retrieval in rows:
        risk = retrieval.risk_type
        if risk not in ranks:
            raise RetrievalIntegrityError("Existing retrieval has an unknown risk type")
        ranks[risk] += 1
        if retrieval.rank != ranks[risk] or (modern and retrieval.rank > 5):
            raise RetrievalIntegrityError(
                "Existing retrieval ranks are partial or inconsistent"
            )
        if (
            chunk.company_id != investigation.company_id
            or chunk.risk_type != risk
            or chunk.observed_at > investigation.as_of_date
        ):
            raise RetrievalIntegrityError(
                "Existing retrieval differs from investigation scope"
            )
        if (
            hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
            != chunk.content_hash
        ):
            raise RetrievalIntegrityError(
                "Existing retrieval Chunk content hash differs"
            )
        if modern:
            key = (risk, chunk.evidence_id, chunk.evidence_content_hash)
            if key in seen or chunk.chunk_schema_version != "v1":
                raise RetrievalIntegrityError(
                    "Existing retrieval has duplicate or noncanonical Chunks"
                )
            seen.add(key)
        elif (
            chunk.chunk_schema_version != "legacy-stage3"
            or chunk.content_hash != chunk.evidence_content_hash
        ):
            raise RetrievalIntegrityError("Legacy retrieval Chunk version differs")
    if modern:
        parts = manifest.removeprefix(RETRIEVAL_MANIFEST_PREFIX).split(":")
        try:
            if len(parts) != 5 or parts[1] != QUERY_VERSION:
                raise ValueError("query")
            version_id = UUID(parts[2])
        except ValueError as exc:
            raise RetrievalIntegrityError(
                "Retrieval manifest version is invalid"
            ) from exc
        version = await session.get(
            EmbeddingIndexVersion, version_id, populate_existing=True
        )
        if (
            version is None
            or _snapshot_manifest(investigation, version, rows) != manifest
        ):
            raise RetrievalIntegrityError(
                "Existing retrieval set differs from its manifest"
            )
    elif (
        _retrieval_manifest([(r.risk_type, r.chunk_id, r.rank) for _, r in rows])
        != manifest
    ):
        raise RetrievalIntegrityError(
            "Existing retrieval set differs from its manifest"
        )
    return rows


async def retrieve_evidence(
    session: AsyncSession, *, run_id: UUID, contract: IndexContract | None = None
) -> None:
    """Caller owns transaction: selection, ranks, marker and manifest are atomic."""
    investigation = await current_investigation(session, run_id)
    if await step_completed(session, run_id, "retrieve_evidence"):
        await load_frozen_retrieval(session, run_id=run_id)
        return
    if await _manifests(session, run_id) or await session.scalar(
        select(RetrievedEvidence.id).where(RetrievedEvidence.run_id == run_id).limit(1)
    ):
        raise RetrievalIntegrityError(
            "Partial retrieval exists without completion marker"
        )
    # Index activation takes the same lock. Hold it through the snapshot commit.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ACTIVATION_LOCK}
    )
    try:
        result = await select_hybrid(
            session,
            investigation=investigation,
            contract=contract or IndexContract.from_settings(Settings()),
        )
    except PermissionError as exc:
        raise RetrievalIntegrityError(str(exc)) from exc
    version = await session.get(EmbeddingIndexVersion, result.index_version_id)
    if version is None or not version.active or result.query_version != QUERY_VERSION:
        raise RetrievalIntegrityError(
            "Retrieval selection version is not active or compatible"
        )
    for selection in result.selections:
        # Indexed canonical Chunks already exist; validate, never rewrite them.
        chunk = await session.get(EvidenceChunk, selection.chunk.id)
        if chunk is None or any(
            getattr(chunk, key) != value
            for key, value in asdict(selection.chunk).items()
        ):
            raise RetrievalIntegrityError("Selected immutable Chunk differs from index")
        session.add(
            RetrievedEvidence(
                run_id=run_id,
                risk_type=chunk.risk_type,
                chunk_id=chunk.id,
                rank=selection.rank,
            )
        )
    await session.flush()
    rows = await _saved_rows(session, run_id)
    session.add_all(
        (
            WorkflowStep(run_id=run_id, name="retrieve_evidence"),
            WorkflowStep(
                run_id=run_id, name=_snapshot_manifest(investigation, version, rows)
            ),
        )
    )
    await session.flush()
    await load_frozen_retrieval(session, run_id=run_id)
    await append_workflow_event(
        session,
        run_id=run_id,
        event_key="node:retrieve_evidence:completed",
        event_type="node.completed",
        node_name="retrieve_evidence",
        run_status="running",
    )


async def build_risk_context(session: AsyncSession, *, run_id: UUID) -> RiskContext:
    investigation = await current_investigation(session, run_id)
    rows = await load_frozen_retrieval(session, run_id=run_id)
    return RiskContext(
        company_id=investigation.company_id,
        evidence_chunks=[
            EvidenceContext.model_validate(
                {
                    "evidence_id": chunk.evidence_id,
                    "chunk_id": chunk.id,
                    "risk_type": retrieval.risk_type,
                    **{
                        name: getattr(chunk, name)
                        for name in (
                            "company_id",
                            "observed_at",
                            "fact_type",
                            "fact_key",
                            "fact_value",
                            "polarity",
                            "content",
                            "content_hash",
                            "evidence_content_hash",
                        )
                    },
                }
            )
            for chunk, retrieval in rows
        ],
    )
