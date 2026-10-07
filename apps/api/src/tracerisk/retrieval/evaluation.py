"""Static source-grounded labels, explicit query embedding, deterministic Recall@5."""

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date
from hashlib import sha256
from pathlib import Path
from statistics import mean
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.companies.models import Company, CompanyAlias
from tracerisk.evidence.models import Evidence, SourceCoverage
from tracerisk.investigations.models import Investigation
from tracerisk.retrieval.embeddings import EmbeddingProvider, validate_vectors
from tracerisk.retrieval.hybrid import select_evaluation_query, select_hybrid
from tracerisk.retrieval.indexing import IndexContract, check_index
from tracerisk.retrieval.queries import QUERY_VERSION, RiskQuery
from tracerisk.synthetic.generator import generate_catalog
from tracerisk.synthetic.importer import import_catalog
from tracerisk.synthetic.scenarios import BASELINE_DATE, RiskType


class RetrievalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1, pattern=r"^[a-z0-9-]+$")
    company_id: UUID
    as_of_date: date
    risk_type: RiskType
    query: str = Field(min_length=1)
    relevant_evidence_ids: tuple[UUID, ...] = Field(min_length=1)
    tags: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @field_validator("query", "rationale")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("Text must be nonblank without outer whitespace")
        return value

    @field_validator("relevant_evidence_ids")
    @classmethod
    def stable_labels(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if value != tuple(sorted(set(value), key=str)):
            raise ValueError("Relevant Evidence IDs must be unique and sorted")
        return value

    @field_validator("tags")
    @classmethod
    def stable_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))) or any(
            not item.strip() for item in value
        ):
            raise ValueError("Tags must be nonblank, unique and sorted")
        return value


def load_cases(path: Path) -> tuple[RetrievalCase, ...]:
    cases = tuple(
        RetrievalCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    )
    if not cases:
        raise ValueError("Evaluation dataset must not be empty")
    ids = [case.case_id for case in cases]
    if ids != sorted(set(ids)):
        raise ValueError("Case IDs must be unique and sorted")
    catalog = generate_catalog()
    companies = {scenario.company.id for scenario in catalog}
    evidence = {row.id: row for scenario in catalog for row in scenario.evidence}
    for case in cases:
        if case.company_id not in companies:
            raise ValueError("Unknown evaluation company")
        for identity in case.relevant_evidence_ids:
            source = evidence.get(identity)
            if source is None:
                raise ValueError("Unknown relevant Evidence ID")
            if (
                source.company_id != case.company_id
                or source.risk_type != case.risk_type
                or source.observed_at > case.as_of_date
            ):
                raise ValueError("Relevant Evidence is outside the case scope")
    return cases


def source_snapshot_hash() -> str:
    payload = json.dumps(
        [asdict(scenario) for scenario in generate_catalog()],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def recall_at_5(relevant: Sequence[UUID], retrieved: Sequence[UUID]) -> float:
    if not relevant:
        raise ValueError("Recall requires nonempty relevance labels")
    return len(set(relevant) & set(retrieved[:5])) / len(set(relevant))


def report_bytes(report: dict[str, object]) -> bytes:
    return (
        json.dumps(
            report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


async def evaluate(
    session: AsyncSession,
    *,
    dataset_path: Path,
    provider: EmbeddingProvider,
    principal_id: UUID,
) -> dict[str, object]:
    """Run only in a source-identical evaluation database, never the application DB.

    The caller owns the transaction. Catalog verification rejects changed source
    rows; a nonempty insert result rejects an incomplete source snapshot.
    """
    cases = load_cases(dataset_path)
    if {case.risk_type for case in cases} != set(RiskType):
        raise ValueError("Evaluation must cover all four risk domains")
    verified = await import_catalog(session, generate_catalog())
    if any(verified.inserted.values()):
        raise ValueError("Evaluation requires the complete preloaded source snapshot")
    for name, model in (
        ("companies", Company),
        ("company_aliases", CompanyAlias),
        ("evidence", Evidence),
        ("source_coverage", SourceCoverage),
    ):
        if (
            await session.scalar(select(func.count()).select_from(model))
            != verified.totals[name]
        ):
            raise ValueError("Evaluation database contains extra source rows")
    contract = IndexContract(
        provider.provider, provider.model, provider.dimensions, provider.distance
    )
    version = await check_index(session, contract)
    assert version is not None
    vectors = validate_vectors(
        await provider.embed_queries([case.query for case in cases]), len(cases)
    )
    results: list[dict[str, object]] = []
    per_domain: dict[str, list[float]] = {risk.value: [] for risk in RiskType}
    for case, vector in zip(cases, vectors, strict=True):
        investigation = Investigation(
            company_id=case.company_id,
            created_by=principal_id,
            as_of_date=case.as_of_date,
        )
        selected = await select_evaluation_query(
            session,
            investigation=investigation,
            contract=contract,
            query=RiskQuery(case.risk_type, case.query),
            query_vector=vector,
        )
        retrieved = [row.chunk.evidence_id for row in selected.selections]
        recall = recall_at_5(case.relevant_evidence_ids, retrieved)
        per_domain[case.risk_type].append(recall)
        results.append(
            {
                "case_id": case.case_id,
                "risk_type": case.risk_type,
                "query_sha256": sha256(case.query.encode("utf-8")).hexdigest(),
                "relevant_evidence_ids": list(map(str, case.relevant_evidence_ids)),
                "retrieved_evidence_ids": list(map(str, retrieved)),
                "retrieved_chunk_ids": [str(r.chunk.id) for r in selected.selections],
                "recall_at_5": recall,
            }
        )
    # Missing-source Recall is undefined: keep this scope probe out of the mean.
    missing = next(s for s in generate_catalog() if s.key == "unavailable-source")
    empty = await select_hybrid(
        session,
        investigation=Investigation(
            company_id=missing.company.id,
            created_by=principal_id,
            as_of_date=BASELINE_DATE,
        ),
        contract=contract,
    )
    if empty.selections:
        raise AssertionError("Unavailable-source scenario must remain empty")
    return {
        "schema_version": "stage4-retrieval-evaluation-v1",
        "mode": provider.provider,
        "model": provider.model,
        "device": "cpu",
        "dimensions": provider.dimensions,
        "distance": provider.distance,
        "index_version": version.version_key,
        "chunk_schema_version": version.chunk_schema_version,
        "workflow_query_version": QUERY_VERSION,
        "query_embedding": "explicit-case-query-with-matching-provider",
        "ranking_pipeline": "scoped-keyword-pgvector-cosine-rrf60-evidence-dedup-top5",
        "case_count": len(cases),
        "dataset_sha256": sha256(dataset_path.read_bytes()).hexdigest(),
        "source_snapshot_sha256": source_snapshot_hash(),
        "per_domain": {
            risk: {"case_count": len(values), "recall_at_5": mean(values)}
            for risk, values in per_domain.items()
        },
        "overall_recall_at_5": mean(
            value for values in per_domain.values() for value in values
        ),
        "cases": results,
        "scope_probes": [
            {
                "case_id": "missing-source-empty-four-domains",
                "company_id": str(missing.company.id),
                "tags": ["missing_sources"],
                "retrieved_count": 0,
                "passed": True,
                "excluded_from_recall": True,
            }
        ],
        "limitations": [
            "Synthetic catalog; scoped candidate counts do not exceed five.",
            "Recall@5 is a ceiling smoke metric, not ranking-quality evidence.",
            "Fake scores are a regression baseline, not BGE semantic quality.",
        ],
    }
