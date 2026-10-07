from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select

from tracerisk.ai.models import AIFinding
from tracerisk.auth.models import Role, User
from tracerisk.auth.router import Session, require_roles
from tracerisk.companies.models import Company
from tracerisk.evidence.conflicts import load_retrieved_conflicts
from tracerisk.evidence.coverage import load_coverage
from tracerisk.evidence.models import Evidence, EvidenceChunk, RetrievedEvidence
from tracerisk.evidence.service import (
    RETRIEVAL_MANIFEST_PREFIX,
    RetrievalIntegrityError,
    load_frozen_retrieval,
)
from tracerisk.investigations.models import Investigation, WorkflowRun, WorkflowStep
from tracerisk.investigations.schemas import (
    ConflictResponse,
    ConflictSideResponse,
    CoverageEntryResponse,
    CoverageResponse,
    CurrentRunResponse,
    DecisionResponse,
    InvestigationCreateRequest,
    InvestigationDetailResponse,
    InvestigationListResponse,
    InvestigationResponse,
    RerunResponse,
    RetrievedEvidenceResponse,
    RuleEvidenceResponse,
    RuleFindingResponse,
    StepResponse,
)
from tracerisk.investigations.service import (
    CompanyNotFound,
    CreationConflict,
    InvestigationNotFound,
    RerunConflict,
    create_authenticated_investigation,
    create_rerun,
)
from tracerisk.reports.models import RiskReport
from tracerisk.reviews.models import HumanFindingVersion, ReviewDecision
from tracerisk.reviews.router import IdempotencyKey
from tracerisk.reviews.schemas import PendingFindingResponse
from tracerisk.rules.models import RuleFinding, RuleFindingEvidence
from tracerisk.rules.references import resolve_rule_evidence_targets

router = APIRouter(prefix="/investigations", tags=["investigations"])
InvestigationReader = Annotated[
    User, Depends(require_roles(Role.ANALYST, Role.REVIEWER, Role.ADMIN))
]
InvestigationCreator = Annotated[User, Depends(require_roles(Role.ANALYST, Role.ADMIN))]


async def summary(
    session: Session, investigation: Investigation
) -> InvestigationResponse:
    company = await session.get(Company, investigation.company_id)
    run = await session.get(WorkflowRun, investigation.current_run_id)
    if company is None or run is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return InvestigationResponse(
        id=investigation.id,
        company_id=company.id,
        company_name=company.name,
        created_by=investigation.created_by,
        as_of_date=investigation.as_of_date,
        current_run=CurrentRunResponse.model_validate(
            {
                "id": run.id,
                "run_number": run.run_number,
                "status": run.status,
                "report_id": await session.scalar(
                    select(RiskReport.id).where(RiskReport.run_id == run.id)
                ),
            }
        ),
    )


@router.post("", response_model=InvestigationResponse)
async def create(
    payload: InvestigationCreateRequest,
    idempotency_key: IdempotencyKey,
    user: InvestigationCreator,
    session: Session,
    request: Request,
) -> InvestigationResponse:
    try:
        investigation = await create_authenticated_investigation(
            request.app.state.session_factory,
            company_id=payload.company_id,
            created_by=user.id,
            as_of_date=payload.as_of_date,
            idempotency_key=idempotency_key,
        )
    except CompanyNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except CreationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return await summary(session, investigation)


@router.post("/{investigation_id}/runs", response_model=RerunResponse)
async def rerun(
    investigation_id: UUID,
    idempotency_key: IdempotencyKey,
    user: InvestigationCreator,
    request: Request,
) -> RerunResponse:
    try:
        replay = await create_rerun(
            request.app.state.session_factory,
            investigation_id=investigation_id,
            actor_id=user.id,
            idempotency_key=idempotency_key,
        )
    except InvestigationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except RerunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return RerunResponse(id=replay.run_id, run_number=replay.run_number)


@router.get("", response_model=InvestigationListResponse)
async def list_investigations(
    session: Session,
    _user: InvestigationReader,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InvestigationListResponse:
    investigations = await session.scalars(
        select(Investigation)
        .join(Company, Company.id == Investigation.company_id)
        .join(WorkflowRun, WorkflowRun.id == Investigation.current_run_id)
        .order_by(Investigation.id)
        .limit(limit)
        .offset(offset)
    )
    return InvestigationListResponse(
        items=[
            await summary(session, investigation) for investigation in investigations
        ]
    )


@router.get("/{investigation_id}", response_model=InvestigationDetailResponse)
async def detail(
    investigation_id: UUID,
    session: Session,
    _user: InvestigationReader,
) -> InvestigationDetailResponse:
    investigation = await session.get(Investigation, investigation_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail="Investigation not found")
    base = await summary(session, investigation)
    run_id = investigation.current_run_id
    steps = await session.scalars(
        select(WorkflowStep)
        .where(
            WorkflowStep.run_id == run_id,
            ~WorkflowStep.name.startswith(RETRIEVAL_MANIFEST_PREFIX),
        )
        .order_by(WorkflowStep.name)
    )
    rules = (
        await session.scalars(
            select(RuleFinding)
            .where(RuleFinding.run_id == run_id)
            .order_by(RuleFinding.rule_code)
        )
    ).all()
    marker_count = await session.scalar(
        select(func.count())
        .select_from(WorkflowStep)
        .where(WorkflowStep.run_id == run_id, WorkflowStep.name == "retrieve_evidence")
    )
    manifest_count = await session.scalar(
        select(func.count())
        .select_from(WorkflowStep)
        .where(
            WorkflowStep.run_id == run_id,
            WorkflowStep.name.startswith(RETRIEVAL_MANIFEST_PREFIX),
        )
    )
    retrieved_count = await session.scalar(
        select(func.count())
        .select_from(RetrievedEvidence)
        .where(RetrievedEvidence.run_id == run_id)
    )
    marker_count = marker_count or 0
    manifest_count = manifest_count or 0
    retrieved_count = retrieved_count or 0
    if marker_count == 1 and manifest_count == 1:
        frozen_rows = await load_frozen_retrieval(session, run_id=run_id)
        retrieval_state: Literal["pending", "legacy_unavailable", "ready"] = "ready"
    elif marker_count or manifest_count or retrieved_count:
        raise RetrievalIntegrityError("Partial frozen retrieval state")
    else:
        step_count = await session.scalar(
            select(func.count())
            .select_from(WorkflowStep)
            .where(WorkflowStep.run_id == run_id)
        )
        retrieval_state = (
            "legacy_unavailable"
            if base.current_run.status in {"completed", "failed"} or (step_count or 0)
            else "pending"
        )
        frozen_rows = []
    all_references: list[RuleFindingEvidence] = []
    references_by_rule: dict[UUID, list[RuleFindingEvidence]] = {}
    for rule in rules:
        refs = list(
            (
                await session.scalars(
                    select(RuleFindingEvidence)
                    .where(RuleFindingEvidence.rule_finding_id == rule.id)
                    .order_by(RuleFindingEvidence.evidence_id)
                )
            ).all()
        )
        references_by_rule[rule.id] = refs
        all_references.extend(refs)
    targets = resolve_rule_evidence_targets(all_references, frozen_rows)
    rule_responses = []
    for rule in rules:
        evidence = references_by_rule[rule.id]
        evidence_targets = {
            item.evidence_id: targets[(item.rule_finding_id, item.evidence_id)]
            for item in evidence
        }
        rule_responses.append(
            RuleFindingResponse(
                id=rule.id,
                rule_code=rule.rule_code,
                rule_version=rule.rule_version,
                score=rule.score,
                parameters=rule.parameters,
                evidence=[
                    RuleEvidenceResponse(
                        evidence_id=item.evidence_id,
                        content_hash=item.content_hash,
                        chunk_id=evidence_targets[item.evidence_id].chunk_id,
                        chunk_content_hash=evidence_targets[
                            item.evidence_id
                        ].chunk_content_hash,
                    )
                    for item in evidence
                ],
            )
        )
    retrieved = await session.execute(
        select(RetrievedEvidence, EvidenceChunk, Evidence)
        .join(EvidenceChunk, EvidenceChunk.id == RetrievedEvidence.chunk_id)
        .join(Evidence, Evidence.id == EvidenceChunk.evidence_id)
        .where(RetrievedEvidence.run_id == run_id)
        .order_by(
            RetrievedEvidence.risk_type,
            RetrievedEvidence.rank,
            RetrievedEvidence.chunk_id,
        )
    )
    findings = await session.scalars(
        select(AIFinding)
        .where(AIFinding.run_id == run_id)
        .order_by(AIFinding.finding_key)
    )
    decisions = await session.scalars(
        select(ReviewDecision)
        .where(ReviewDecision.run_id == run_id)
        .order_by(ReviewDecision.id)
    )
    human_versions = {
        human.review_decision_id: human
        for human in (
            await session.scalars(
                select(HumanFindingVersion).where(HumanFindingVersion.run_id == run_id)
            )
        ).all()
    }
    coverage = await load_coverage(
        session,
        company_id=investigation.company_id,
        as_of_date=investigation.as_of_date,
    )
    conflict_evaluation = (
        await load_retrieved_conflicts(
            session, run_id=run_id, as_of_date=investigation.as_of_date
        )
        if retrieval_state == "ready"
        else None
    )
    evidence_sufficiency: Literal["sufficient", "insufficient", "unavailable"] = (
        "unavailable"
        if retrieval_state != "ready"
        else (
            "sufficient"
            if coverage.is_sufficient
            and conflict_evaluation is not None
            and not conflict_evaluation.has_unresolved
            else "insufficient"
        )
    )
    return InvestigationDetailResponse(
        **base.model_dump(),
        retrieval_state=retrieval_state,
        steps=[StepResponse.model_validate(step) for step in steps],
        rule_score=sum(rule.score for rule in rules),
        rule_findings=rule_responses,
        retrieved_evidence=[
            RetrievedEvidenceResponse(
                risk_type=retrieval.risk_type,
                rank=retrieval.rank,
                chunk_id=chunk.id,
                evidence_id=evidence.id,
                source_uri=evidence.source_uri,
                title=evidence.title,
                **{
                    name: getattr(chunk, name)
                    for name in (
                        "observed_at",
                        "fact_type",
                        "fact_key",
                        "fact_value",
                        "polarity",
                        "content",
                        "content_hash",
                    )
                },
            )
            for retrieval, chunk, evidence in retrieved
        ],
        ai_findings=[
            PendingFindingResponse(
                finding_id=finding.id,
                finding_key=finding.finding_key,
                finding_version=finding.version,
                run_id=run_id,
                investigation_id=investigation.id,
                company_id=base.company_id,
                company_name=base.company_name,
                **{
                    name: getattr(finding, name)
                    for name in (
                        "risk_type",
                        "claim_code",
                        "title",
                        "summary",
                        "reasoning",
                        "citations",
                        "evidence_sufficiency",
                        "confidence_level",
                        "review_recommended",
                        "support_status",
                        "support_reason",
                    )
                },
            )
            for finding in findings
        ],
        decisions=[
            DecisionResponse(
                **DecisionResponse.model_validate(decision).model_dump(
                    exclude={"human_finding_version_id", "human_version", "replacement"}
                ),
                human_finding_version_id=(
                    human_versions[decision.id].id
                    if decision.id in human_versions
                    else None
                ),
                human_version=(
                    human_versions[decision.id].version
                    if decision.id in human_versions
                    else None
                ),
                replacement=(
                    human_versions[decision.id].replacement
                    if decision.id in human_versions
                    else None
                ),
            )
            for decision in decisions
        ],
        coverage=CoverageResponse(
            entries=[
                CoverageEntryResponse.model_validate(entry.__dict__)
                for entry in coverage.entries
            ],
            insufficiency_details=list(coverage.insufficiency_details),
            evidence_sufficiency=evidence_sufficiency,
        ),
        conflicts=[
            ConflictResponse(
                company_id=conflict.company_id,
                risk_type=conflict.risk_type,
                fact_type=conflict.fact_type,
                fact_key=conflict.fact_key,
                authority_source=conflict.authority_source,
                resolved_value=conflict.resolved_value,
                resolution_state=(
                    "resolved" if conflict.resolved_value is not None else "unresolved"
                ),
                sides=[
                    ConflictSideResponse(
                        source_type=side.source_type,
                        fact_value=side.fact_value,
                        observed_at=side.observed_at,
                        evidence_id=side.evidence_id,
                        content_hash=side.content_hash,
                    )
                    for side in conflict.sides
                ],
            )
            for conflict in (
                conflict_evaluation.conflicts if conflict_evaluation else ()
            )
        ],
    )
