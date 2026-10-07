from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select

from tracerisk.ai.models import AIFinding
from tracerisk.ai.validation import CitationValidationError
from tracerisk.auth.models import Role, User
from tracerisk.auth.router import Session, require_roles
from tracerisk.companies.models import Company
from tracerisk.investigations.models import Investigation, WorkflowRun
from tracerisk.reviews.models import ReviewDecision
from tracerisk.reviews.schemas import (
    PendingFindingResponse,
    ReviewRequest,
    ReviewResponse,
)
from tracerisk.reviews.service import FindingReview, ReviewConflict, submit_review

router = APIRouter(prefix="/reviews", tags=["reviews"])
Reviewer = Annotated[User, Depends(require_roles(Role.REVIEWER, Role.ADMIN))]
IdempotencyKey = Annotated[str, Header(min_length=1, max_length=200)]


@router.get("/pending", response_model=list[PendingFindingResponse])
async def pending(session: Session, user: Reviewer) -> list[PendingFindingResponse]:
    rows = await session.execute(
        select(AIFinding, Investigation, Company)
        .join(WorkflowRun, WorkflowRun.id == AIFinding.run_id)
        .join(Investigation, Investigation.id == WorkflowRun.investigation_id)
        .join(Company, Company.id == Investigation.company_id)
        .where(
            WorkflowRun.status == "awaiting_review",
            Investigation.current_run_id == WorkflowRun.id,
            Investigation.created_by != user.id,
            ~select(ReviewDecision.id)
            .where(
                ReviewDecision.ai_finding_id == AIFinding.id,
                ReviewDecision.finding_version == AIFinding.version,
            )
            .exists(),
        )
        .order_by(AIFinding.run_id, AIFinding.finding_key)
    )
    return [
        PendingFindingResponse(
            finding_id=finding.id,
            finding_key=finding.finding_key,
            finding_version=finding.version,
            run_id=finding.run_id,
            investigation_id=investigation.id,
            company_id=company.id,
            company_name=company.name,
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
        for finding, investigation, company in rows
    ]


@router.post("/{finding_id}", response_model=ReviewResponse)
async def review(
    finding_id: UUID,
    payload: ReviewRequest,
    idempotency_key: IdempotencyKey,
    user: Reviewer,
    session: Session,
    request: Request,
) -> ReviewResponse:
    finding = await session.get(AIFinding, finding_id)
    if finding is None:
        raise HTTPException(
            status_code=409, detail="Finding is not eligible for review"
        )
    try:
        decision_id = await submit_review(
            request.app.state.session_factory,
            FindingReview(
                run_id=finding.run_id,
                ai_finding_id=finding_id,
                reviewer_id=user.id,
                idempotency_key=idempotency_key,
                **payload.model_dump(),
            ),
        )
    except ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except CitationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return ReviewResponse(decision_id=decision_id)
