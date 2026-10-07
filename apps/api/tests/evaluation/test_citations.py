from collections.abc import AsyncIterator
from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tracerisk.ai.models import AIFinding
from tracerisk.ai.schemas import RiskFindingDraft, StructuredRiskFindings
from tracerisk.ai.validation import CitationValidationError, validate_findings
from tracerisk.auth.models import User
from tracerisk.config import Settings
from tracerisk.evaluation.metrics import ratio_metric
from tracerisk.evaluation.runner import FrozenCitation, final_coverage_samples
from tracerisk.investigations.service import create_investigation
from tracerisk.retrieval.embeddings import FakeEmbedding
from tracerisk.retrieval.indexing import IndexContract, build_index
from tracerisk.synthetic.generator import generate_catalog
from tracerisk.synthetic.importer import import_catalog
from tracerisk.workflows.spike_graph import execute_workflow, initialize_checkpoints

pytestmark = pytest.mark.anyio
Resources = tuple[async_sessionmaker[AsyncSession], UUID, UUID, RiskFindingDraft]


def test_final_coverage_uses_final_citation_payload_and_any_valid_citation() -> None:
    frozen = (
        FrozenCitation("judicial", "chunk-1", "evidence-1", "a" * 64, "valid excerpt"),
    )
    valid = {
        "chunk_id": "chunk-1",
        "evidence_id": "evidence-1",
        "content_hash": "a" * 64,
        "quoted_excerpt": "excerpt",
    }
    changed = {**valid, "content_hash": "b" * 64}

    def final(citations: list[dict[str, str]]) -> list[dict[str, object]]:
        return [
            {
                "finding_key": "finding-1",
                "risk_type": "judicial",
                "citations": citations,
            }
        ]

    assert final_coverage_samples(final([changed]), frozen) == {"finding-1": False}
    assert final_coverage_samples(final([changed, valid]), frozen) == {
        "finding-1": True
    }
    assert final_coverage_samples(final([valid, changed]), frozen) == {
        "finding-1": True
    }
    assert final_coverage_samples(final([]), frozen) == {"finding-1": False}
    assert final_coverage_samples([], frozen) == {}
    empty_metric = ratio_metric(
        "evidence_coverage",
        "covered final findings / final evidence findings",
        final_coverage_samples([], frozen),
        source_snapshot_sha256="a" * 64,
    )
    assert (empty_metric.numerator, empty_metric.denominator) == (0, 0)
    assert empty_metric.status == "not_applicable"


@pytest.mark.parametrize(
    "change",
    [
        {"chunk_id": "other"},
        {"evidence_id": "other"},
        {"quoted_excerpt": "not contiguous"},
    ],
)
def test_final_coverage_rejects_changed_identity_or_excerpt(
    change: dict[str, str],
) -> None:
    frozen = (
        FrozenCitation("judicial", "chunk-1", "evidence-1", "a" * 64, "valid excerpt"),
    )
    citation = {
        "chunk_id": "chunk-1",
        "evidence_id": "evidence-1",
        "content_hash": "a" * 64,
        "quoted_excerpt": "excerpt",
        **change,
    }
    final = [
        {"finding_key": "finding-1", "risk_type": "judicial", "citations": [citation]}
    ]
    assert final_coverage_samples(final, frozen) == {"finding-1": False}


@pytest.fixture
async def resources(database: Settings) -> AsyncIterator[Resources]:
    await initialize_checkpoints()
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        catalog = generate_catalog()
        scenario = next(item for item in catalog if item.key == "existing-judicial")
        async with sessions.begin() as session:
            await import_catalog(session, catalog)
            await build_index(session, FakeEmbedding(), IndexContract.fake())
            creator = User(
                username="citation-evaluation", password_hash="disabled", role="analyst"
            )
            session.add(creator)
            await session.flush()
            investigation = await create_investigation(
                session,
                company_id=scenario.company.id,
                created_by=creator.id,
                as_of_date=date(2026, 9, 11),
            )
            run_id = investigation.current_run_id
        await execute_workflow(database, sessions, run_id, "start")
        async with sessions() as session:
            finding = await session.scalar(
                select(AIFinding).where(AIFinding.run_id == run_id)
            )
            assert finding is not None
            draft = RiskFindingDraft.model_validate(
                {
                    "finding_key": finding.finding_key,
                    "risk_type": finding.risk_type,
                    "claim_code": finding.claim_code,
                    "title": finding.title,
                    "summary": finding.summary,
                    "reasoning": finding.reasoning,
                    "evidence_citations": finding.citations,
                    "evidence_sufficiency": finding.evidence_sufficiency,
                    "confidence_level": finding.confidence_level,
                    "review_recommended": finding.review_recommended,
                }
            )
        yield sessions, run_id, creator.id, draft
    finally:
        await engine.dispose()


async def test_persisted_mock_citations_and_structured_response(resources: Resources):
    sessions, run_id, user_id, draft = resources
    async with sessions() as session:
        validated = await validate_findings(
            session,
            run_id=run_id,
            user_id=user_id,
            findings=StructuredRiskFindings(findings=[draft]),
        )
        assert len(validated.findings) == 1
        citations = (await session.scalars(select(AIFinding.citations))).all()
        assert sum(len(item) for item in citations) == len(draft.evidence_citations)
    citation_samples = {
        f"citation-{n}": True for n, _ in enumerate(draft.evidence_citations, start=1)
    }
    citation_metric = ratio_metric(
        "citation_legality",
        "valid persisted citations / persisted citations",
        citation_samples,
        source_snapshot_sha256="a" * 64,
    )
    structured_metric = ratio_metric(
        "structured_output_success",
        "validated responses / evaluated responses",
        {"existing-judicial": True},
        source_snapshot_sha256="a" * 64,
    )
    assert citation_metric.numerator == citation_metric.denominator > 0
    assert structured_metric.value == 1.0
    assert (
        ratio_metric(
            "evidence_coverage",
            "covered final findings / final evidence findings",
            {},
            source_snapshot_sha256="a" * 64,
        ).status
        == "not_applicable"
    )


async def test_reject_invalid_citations_and_missing_frozen_retrieval(
    resources: Resources,
):
    sessions, run_id, user_id, draft = resources
    original = draft.evidence_citations[0]
    mutations = {
        "wrong_evidence_id": {"evidence_id": UUID(int=999)},
        "wrong_chunk_id": {"chunk_id": UUID(int=999)},
        "wrong_content_hash": {"content_hash": "b" * 64},
        "noncontiguous_excerpt": {
            "quoted_excerpt": original.quoted_excerpt + " not contiguous"
        },
    }
    async with sessions() as session:
        for changes in mutations.values():
            candidate = draft.model_copy(
                update={"evidence_citations": [original.model_copy(update=changes)]}
            )
            with pytest.raises(CitationValidationError):
                await validate_findings(
                    session,
                    run_id=run_id,
                    user_id=user_id,
                    findings=StructuredRiskFindings(findings=[candidate]),
                )
        assert await session.scalar(select(func.count()).select_from(AIFinding)) == 1
    async with sessions.begin() as session:
        company = next(
            item for item in generate_catalog() if item.key == "all-rules-95"
        )
        other = await create_investigation(
            session,
            company_id=company.company.id,
            created_by=user_id,
            as_of_date=date(2026, 9, 11),
        )
        other_run = other.current_run_id
    async with sessions() as session:
        with pytest.raises(CitationValidationError, match="Retrieval manifest"):
            await validate_findings(
                session,
                run_id=other_run,
                user_id=user_id,
                findings=StructuredRiskFindings(findings=[draft]),
            )
    await execute_workflow(Settings(), sessions, other_run, "start")
    async with sessions() as session:
        with pytest.raises(
            CitationValidationError, match="outside saved Run retrieval"
        ):
            await validate_findings(
                session,
                run_id=other_run,
                user_id=user_id,
                findings=StructuredRiskFindings(findings=[draft]),
            )
