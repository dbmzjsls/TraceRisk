from datetime import date
from hashlib import sha256
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.test_stage4_workflow_retrieval import seed
from tracerisk.ai.claims import ClaimFact, canonical_statement
from tracerisk.ai.models import AIFinding
from tracerisk.ai.schemas import RiskFindingDraft, StructuredRiskFindings
from tracerisk.ai.service import generate_validate_findings
from tracerisk.ai.validation import CitationValidationError, validate_findings
from tracerisk.auth.models import User
from tracerisk.companies.models import Company
from tracerisk.config import Settings
from tracerisk.evidence.conflicts import load_retrieved_conflicts
from tracerisk.evidence.models import Evidence, EvidenceChunk, RetrievedEvidence
from tracerisk.evidence.service import (
    _retrieval_manifest,
    _snapshot_manifest,
    build_risk_context,
    retrieve_evidence,
)
from tracerisk.investigations.models import WorkflowStep
from tracerisk.investigations.service import create_investigation
from tracerisk.reports.models import RiskReport
from tracerisk.reports.service import generate_report
from tracerisk.retrieval.embeddings import FakeEmbedding
from tracerisk.retrieval.indexing import IndexContract, build_index
from tracerisk.retrieval.models import EmbeddingIndexVersion
from tracerisk.reviews.models import ReviewDecision
from tracerisk.rules.engine import evaluate_all_rules_from_facts, save_rule_findings
from tracerisk.workflows.errors import PermanentWorkflowError
from tracerisk.workflows.spike_graph import build_graph

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("paragraph", "legacy"), [(False, False), (True, False), (False, True)]
)
async def test_saved_citation_boundaries(
    database: Settings, paragraph: bool, legacy: bool
) -> None:
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            companies = [
                Company(
                    synthetic_key=f"citation-{n}",
                    name=f"Company {n}",
                    unified_credit_code=f"SYNTHETIC-CITATION-{n}",
                )
                for n in range(2)
            ]
            user = User(
                username="citation-analyst", password_hash="unused", role="analyst"
            )
            session.add_all([*companies, user])
            await session.flush()
            investigation = await create_investigation(
                session,
                company_id=companies[0].id,
                created_by=user.id,
                as_of_date=date(2026, 9, 11),
            )
            chunks = []
            sources = []
            for n in range(3):
                content = f"Case {n} remains open.\nStored original text."
                source = Evidence(
                    company_id=companies[n == 1].id,
                    source_type="judicial",
                    risk_type="judicial",
                    source_uri=f"synthetic://citation/{n}",
                    title="Case",
                    observed_at=date(2026, 9, 11),
                    fact_type="judicial_case_status",
                    fact_key=f"CASE-{n}",
                    fact_value="open",
                    polarity="negative",
                    content=content,
                    content_hash=sha256(content.encode()).hexdigest(),
                )
                session.add(source)
                await session.flush()
                chunk = EvidenceChunk(
                    evidence_id=source.id,
                    source_type=source.source_type,
                    risk_type=source.risk_type,
                    evidence_content_hash=source.content_hash,
                    chunk_schema_version="legacy-stage3" if legacy else "v1",
                    company_id=source.company_id,
                    observed_at=source.observed_at,
                    fact_type=source.fact_type,
                    fact_key=source.fact_key,
                    fact_value=source.fact_value,
                    polarity=source.polarity,
                    content=source.content.splitlines()[0]
                    if paragraph
                    else source.content,
                    content_hash=sha256(
                        source.content.splitlines()[0].encode()
                    ).hexdigest()
                    if paragraph
                    else source.content_hash,
                )
                session.add(chunk)
                await session.flush()
                sources.append(source)
                chunks.append(chunk)
                if n == 0:
                    retrieval = RetrievedEvidence(
                        run_id=investigation.current_run_id,
                        risk_type="judicial",
                        chunk_id=chunk.id,
                        rank=n + 1,
                    )
                    session.add(retrieval)
            version = EmbeddingIndexVersion(
                version_key="citation-test",
                provider="fake",
                model="fake-sha256-v1",
                dimensions=512,
                distance="cosine",
                chunk_schema_version="v1",
                active=True,
            )
            session.add(version)
            await session.flush()
            session.add_all(
                [
                    WorkflowStep(
                        run_id=investigation.current_run_id, name="retrieve_evidence"
                    ),
                    WorkflowStep(
                        run_id=investigation.current_run_id,
                        name=(
                            _retrieval_manifest([("judicial", chunks[0].id, 1)])
                            if legacy
                            else _snapshot_manifest(
                                investigation, version, [(chunks[0], retrieval)]
                            )
                        ),
                    ),
                ]
            )
        async with sessions() as session:
            citation = dict(
                evidence_id=chunks[0].evidence_id,
                chunk_id=chunks[0].id,
                content_hash=chunks[0].content_hash,
                quoted_excerpt=chunks[0].content,
            )
            draft = dict(
                finding_key="case",
                risk_type="judicial",
                claim_code="JUDICIAL_OPEN_CASE",
                title="Open case",
                summary="An open case exists.",
                reasoning="Stored open fact.",
                evidence_citations=[citation],
                evidence_sufficiency="sufficient",
                confidence_level="high",
                review_recommended=True,
            )

            async def validate(
                data: dict[str, object], user_id: object = user.id
            ) -> None:
                await validate_findings(
                    session,
                    run_id=investigation.current_run_id,
                    user_id=user_id,
                    findings=StructuredRiskFindings.model_validate(
                        {"findings": [data]}
                    ),
                )

            await validate(draft)
            for role in ("reviewer", "admin"):
                saved_user = await session.get(User, user.id)
                assert saved_user is not None
                saved_user.role = role
                await session.flush()
                await validate(draft)
            for changes in [
                dict(
                    chunk_id=chunks[1].id,
                    evidence_id=chunks[1].evidence_id,
                    content_hash=chunks[1].content_hash,
                    quoted_excerpt=chunks[1].content,
                ),
                dict(
                    chunk_id=chunks[2].id,
                    evidence_id=chunks[2].evidence_id,
                    content_hash=chunks[2].content_hash,
                    quoted_excerpt=chunks[2].content,
                ),
                dict(evidence_id=chunks[1].evidence_id),
                dict(content_hash="a" * 64),
                dict(quoted_excerpt="Case 0Stored original"),
            ]:
                with pytest.raises(CitationValidationError):
                    await validate(
                        {**draft, "evidence_citations": [{**citation, **changes}]}
                    )
            with pytest.raises(CitationValidationError):
                await validate({**draft, "claim_code": "UNKNOWN"})
            with pytest.raises(CitationValidationError):
                await validate(draft, uuid4())
            # Emulate a tampered candidate that bypassed schema checks.
            finding = RiskFindingDraft.model_validate(draft)
            empty = finding.evidence_citations[0].model_copy(
                update={"quoted_excerpt": ""}
            )
            with pytest.raises(CitationValidationError):
                await validate_findings(
                    session,
                    run_id=investigation.current_run_id,
                    user_id=user.id,
                    findings=StructuredRiskFindings(
                        findings=[
                            finding.model_copy(update={"evidence_citations": [empty]})
                        ]
                    ),
                )
            stored = await session.get(EvidenceChunk, chunks[0].id)
            source = await session.get(Evidence, sources[0].id)
            assert stored is not None and source is not None
            for row, field, value in [
                (stored, "fact_value", "closed"),
                (stored, "polarity", "positive"),
                (stored, "content", stored.content + " "),
                (stored, "content_hash", "b" * 64),
            ]:
                original = getattr(row, field)
                setattr(row, field, value)
                await session.flush()
                with pytest.raises(CitationValidationError):
                    await validate(draft)
                setattr(row, field, original)
                # Dirty identity-map attributes must not replace persisted facts.
                with pytest.raises(CitationValidationError):
                    await validate(draft)
                assert getattr(row, field) == original
                assert row in session.dirty
                with session.no_autoflush:
                    persisted = await session.scalar(
                        select(getattr(type(row), field)).where(type(row).id == row.id)
                    )
                assert persisted == value
                await session.flush()
            # Live Evidence no longer supplies or invalidates frozen citations.
            source.fact_value = "closed"
            source.polarity = "positive"
            source.content = "changed live source"
            await session.flush()
            await validate(draft)
            assert (
                await session.scalar(select(func.count()).select_from(AIFinding)) == 0
            )
    finally:
        await engine.dispose()


@pytest.fixture
async def support_resources(database, request):
    engine = create_async_engine(database.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            run_id, _ = await seed(session, indexed=False)
            if getattr(request, "param", None) == "contradiction":
                await session.execute(
                    update(Evidence)
                    .where(Evidence.risk_type == "judicial")
                    .values(fact_value="closed", polarity="positive")
                )
            await build_index(session, FakeEmbedding(), IndexContract.fake())
            await retrieve_evidence(session, run_id=run_id)
            context = await build_risk_context(session, run_id=run_id)
            conflicts = await load_retrieved_conflicts(
                session, run_id=run_id, as_of_date=date(2026, 9, 11)
            )
            await save_rule_findings(
                session,
                run_id=run_id,
                score=evaluate_all_rules_from_facts(
                    conflicts.resolved_facts, as_of_date=date(2026, 9, 11)
                ),
            )
        drafts = []
        codes = {
            "judicial": "JUDICIAL_OPEN_CASE",
            "business": "BUSINESS_ABNORMAL",
            "news": "NEGATIVE_NEWS",
            "intellectual_property": "CORE_IP_ANOMALY",
        }
        for risk, code in codes.items():
            chunk = next(c for c in context.evidence_chunks if c.risk_type == risk)
            wording = canonical_statement(
                ClaimFact(
                    risk,
                    risk,
                    chunk.fact_type,
                    chunk.fact_key,
                    chunk.fact_value,
                    chunk.polarity,
                )
            )
            drafts.append(
                RiskFindingDraft.model_validate(
                    {
                        "finding_key": risk,
                        "risk_type": risk,
                        "claim_code": code,
                        "title": wording,
                        "summary": wording,
                        "reasoning": wording,
                        "evidence_citations": [
                            {
                                "evidence_id": chunk.evidence_id,
                                "chunk_id": chunk.chunk_id,
                                "content_hash": chunk.content_hash,
                                "quoted_excerpt": chunk.content,
                            }
                        ],
                        "evidence_sufficiency": "sufficient",
                        "confidence_level": "high",
                        "review_recommended": False,
                    }
                )
            )
        yield sessions, run_id, drafts, context
    finally:
        await engine.dispose()


async def rule_bytes(sessions):
    async with sessions() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT row_to_json(r)::text "
                        "FROM rule_findings r ORDER BY rule_code"
                    )
                )
            )
            .scalars()
            .all()
        )
        return tuple(row.encode("utf-8") for row in rows)


class DraftAdapter:
    def __init__(self, drafts):
        self.drafts = StructuredRiskFindings(findings=drafts)

    async def analyze_risk(self, context):
        return self.drafts


@pytest.mark.parametrize("extension", [False, True])
async def test_four_claims_persist_original_text_and_report_support(
    support_resources, extension, database
):
    sessions, run_id, drafts, _ = support_resources
    if extension:
        drafts[-1] = drafts[-1].model_copy(
            update={
                "reasoning": drafts[-1].reasoning + " This proves imminent insolvency.",
            }
        )
    before = await rule_bytes(sessions)
    adapter = DraftAdapter(drafts)
    await generate_validate_findings(sessions, run_id=run_id, adapter=adapter)
    assert await rule_bytes(sessions) == before
    # Replays are idempotent, including derived support fields.
    await generate_validate_findings(sessions, run_id=run_id, adapter=adapter)
    graph = build_graph(InMemorySaver(), sessions, database)
    state = await graph.ainvoke(
        {"run_id": str(run_id)}, {"configurable": {"thread_id": str(run_id)}}
    )
    assert state["review_required"]
    assert ("unverified_free_text_extension" in state["review_reason"]) == extension
    async with sessions.begin() as session:
        findings = (
            await session.scalars(select(AIFinding).where(AIFinding.run_id == run_id))
        ).all()
        assert len(findings) == 4
        for finding in findings:
            original = next(d for d in drafts if d.finding_key == finding.finding_key)
            extended = extension and original == drafts[-1]
            assert (finding.title, finding.summary, finding.reasoning) == (
                original.title,
                original.summary,
                original.reasoning,
            )
            assert finding.support_status == (
                "indeterminate" if extended else "supported"
            )
            assert finding.support_reason == (
                "unverified_free_text_extension" if extended else "canonical_fact_match"
            )
            assert finding.evidence_sufficiency == (
                "insufficient" if extended else "sufficient"
            )
            assert finding.review_recommended == extended
    with pytest.raises(ValueError, match="complete human review"):
        await generate_report(sessions, run_id)
    async with sessions.begin() as session:
        for finding in findings:
            session.add(
                ReviewDecision(
                    run_id=run_id,
                    ai_finding_id=finding.id,
                    finding_version=finding.version,
                    reviewer_id=uuid4(),
                    idempotency_key=str(finding.id),
                    decision="approve",
                    reason="test review",
                )
            )
    report_id = await generate_report(sessions, run_id)
    async with sessions() as session:
        report = await session.get(RiskReport, report_id)
        assert report.snapshot["rule_score"] == 95
        for item in report.snapshot["ai_findings"]:
            finding = next(f for f in findings if str(f.id) == item["id"])
            assert item["support_status"] == finding.support_status
            assert item["support_reason"] == finding.support_reason
        assert (
            "unverified_free_text_extension" in report.snapshot["review_reasons"]
        ) == extension
        if extension:
            assert "unverified_free_text_extension" in report.html_content
    assert await rule_bytes(sessions) == before


@pytest.mark.parametrize("support_resources", ["contradiction"], indirect=True)
async def test_contradicted_valid_code_is_permanent_and_atomic(support_resources):
    sessions, run_id, drafts, _ = support_resources
    before = await rule_bytes(sessions)
    # Put the valid finding first to detect partial persistence on later rejection.
    with pytest.raises(PermanentWorkflowError):
        await generate_validate_findings(
            sessions, run_id=run_id, adapter=DraftAdapter(drafts[1:] + drafts[:1])
        )
    await assert_nothing_persisted(sessions, run_id)
    assert await rule_bytes(sessions) == before


@pytest.mark.parametrize(
    "attack",
    ["unsupported", "wrong_domain", "hash", "excerpt", "company", "evidence_version"],
)
async def test_rejection_persists_no_partial_findings(support_resources, attack):
    sessions, run_id, drafts, context = support_resources
    before = await rule_bytes(sessions)
    bad = drafts[-1]
    if attack == "unsupported":
        bad = bad.model_copy(update={"claim_code": "CORE_IP_ANOMALY_V1"})
    elif attack == "wrong_domain":
        bad = bad.model_copy(update={"claim_code": "JUDICIAL_OPEN_CASE"})
    else:
        citation = bad.evidence_citations[0]
        change = (
            {"content_hash": "b" * 64}
            if attack == "hash"
            else {"quoted_excerpt": "made up"}
        )
        if attack == "company":
            async with sessions.begin() as session:
                company = Company(
                    synthetic_key="other-company",
                    name="Other company",
                    unified_credit_code="OTHER-COMPANY",
                )
                session.add(company)
                await session.flush()
                other = Evidence(
                    company_id=company.id,
                    source_type="intellectual_property",
                    risk_type="intellectual_property",
                    source_uri="synthetic://other",
                    title="Other IP",
                    observed_at=date(2026, 9, 11),
                    fact_type="core_ip_status",
                    fact_key="OTHER-IP",
                    fact_value="invalid",
                    polarity="negative",
                    content="Other company IP fact",
                    content_hash=sha256(b"Other company IP fact").hexdigest(),
                )
                session.add(other)
                await session.flush()
                await session.execute(
                    update(EvidenceChunk)
                    .where(EvidenceChunk.id == citation.chunk_id)
                    .values(
                        company_id=company.id,
                        evidence_id=other.id,
                        evidence_content_hash=other.content_hash,
                    )
                )
        elif attack == "evidence_version":
            # Another real version owned by this company is still not this citation.
            other = next(c for c in context.evidence_chunks if c.risk_type == "news")
            async with sessions.begin() as session:
                await session.execute(
                    update(EvidenceChunk)
                    .where(EvidenceChunk.id == citation.chunk_id)
                    .values(
                        evidence_id=other.evidence_id,
                        evidence_content_hash=other.evidence_content_hash,
                    )
                )
        else:
            bad = bad.model_copy(
                update={"evidence_citations": [citation.model_copy(update=change)]}
            )
    drafts[-1] = bad
    with pytest.raises(PermanentWorkflowError):
        await generate_validate_findings(
            sessions, run_id=run_id, adapter=DraftAdapter(drafts)
        )
    await assert_nothing_persisted(sessions, run_id)
    assert await rule_bytes(sessions) == before


async def assert_nothing_persisted(sessions, run_id):
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AIFinding)) == 0
        assert (
            await session.scalar(
                select(WorkflowStep.id).where(
                    WorkflowStep.run_id == run_id,
                    WorkflowStep.name == "generate_validate_findings",
                )
            )
            is None
        )
