from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tracerisk.ai.models import AIFinding
from tracerisk.ai.schemas import StructuredRiskFindings
from tracerisk.ai.validation import CitationValidationError, validate_findings
from tracerisk.audit.service import append_audit_event
from tracerisk.investigations.models import DispatchOutbox, Investigation, WorkflowRun
from tracerisk.reviews.models import HumanFindingVersion, ReviewDecision
from tracerisk.reviews.schemas import ReviewRequest
from tracerisk.workflows.events import append_workflow_event


class ReviewConflict(ValueError):
    """The request cannot change the current review state."""


class RunReview(BaseModel):
    run_id: UUID
    reviewer_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=200)
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=1)


class FindingReview(ReviewRequest):
    run_id: UUID
    reviewer_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=200)
    ai_finding_id: UUID


async def replay(
    session: AsyncSession, request: RunReview | FindingReview
) -> UUID | None:
    existing = await session.scalar(
        select(ReviewDecision).where(
            ReviewDecision.reviewer_id == request.reviewer_id,
            ReviewDecision.idempotency_key == request.idempotency_key,
        )
    )
    if existing is None:
        return None
    human = await session.scalar(
        select(HumanFindingVersion).where(
            HumanFindingVersion.review_decision_id == existing.id
        )
    )
    submitted = (
        request.replacement.model_dump(mode="json")
        if isinstance(request, FindingReview) and request.replacement is not None
        else None
    )
    if (
        existing.run_id,
        existing.ai_finding_id,
        existing.finding_version,
        existing.decision,
        existing.reason,
        human.submitted_replacement if human else None,
    ) != (
        request.run_id,
        getattr(request, "ai_finding_id", None),
        getattr(request, "finding_version", None),
        request.decision,
        request.reason,
        submitted,
    ):
        raise ReviewConflict("Idempotency key was used for a different request")
    return existing.id


async def submit_review(
    sessions: async_sessionmaker[AsyncSession], request: RunReview | FindingReview
) -> UUID:
    """Commit a review and enqueue resume only when every finding is resolved."""
    try:
        async with sessions.begin() as session:
            previous = await replay(session, request)
            if previous is not None:
                return previous
            investigation = await session.scalar(
                select(Investigation)
                .join(WorkflowRun, WorkflowRun.investigation_id == Investigation.id)
                .where(WorkflowRun.id == request.run_id)
                .with_for_update(of=Investigation)
            )
            run = await session.get(WorkflowRun, request.run_id, with_for_update=True)
            previous = await replay(session, request)
            if previous is not None:
                return previous
            if (
                investigation is None
                or run is None
                or investigation.current_run_id != run.id
                or run.status != "awaiting_review"
                or investigation.created_by == request.reviewer_id
            ):
                raise ReviewConflict("Run is not eligible for this review")
            replacement = None
            if isinstance(request, FindingReview):
                finding = await session.get(AIFinding, request.ai_finding_id)
                if (
                    finding is None
                    or finding.run_id != run.id
                    or finding.version != request.finding_version
                ):
                    raise ReviewConflict("Finding is not eligible for this review")
                if await session.scalar(
                    select(ReviewDecision.id).where(
                        ReviewDecision.run_id == run.id,
                        ReviewDecision.ai_finding_id == finding.id,
                    )
                ):
                    raise ReviewConflict("Finding already has a review")
                if request.replacement is not None:
                    if (
                        request.replacement.finding_key != finding.finding_key
                        or request.replacement.risk_type != finding.risk_type
                    ):
                        raise CitationValidationError(
                            "Replacement must preserve finding key and risk type"
                        )
                    validated = await validate_findings(
                        session,
                        run_id=run.id,
                        user_id=request.reviewer_id,
                        findings=StructuredRiskFindings(findings=[request.replacement]),
                    )
                    replacement = validated.findings[0].model_dump(mode="json")
                    replacement["citations"] = replacement.pop("evidence_citations")
            elif await session.scalar(
                select(AIFinding.id).where(AIFinding.run_id == run.id).limit(1)
            ):
                raise ReviewConflict("Run requires finding-level reviews")
            decision = ReviewDecision(**request.model_dump(exclude={"replacement"}))
            session.add(decision)
            await session.flush()
            human = None
            if isinstance(request, FindingReview) and replacement is not None:
                assert request.replacement is not None
                human = HumanFindingVersion(
                    review_decision_id=decision.id,
                    run_id=run.id,
                    ai_finding_id=request.ai_finding_id,
                    finding_version=request.finding_version,
                    reviewer_id=request.reviewer_id,
                    submitted_replacement=request.replacement.model_dump(mode="json"),
                    replacement=replacement,
                )
                session.add(human)
                await session.flush()
            await append_audit_event(
                session,
                event_type="review.submitted",
                action_key=f"review:{decision.id}:submitted",
                actor_id=request.reviewer_id,
                investigation_id=investigation.id,
                run_id=run.id,
                resource_type="review_decision",
                resource_id=decision.id,
                metadata={
                    "decision": request.decision,
                    "finding_version": getattr(request, "finding_version", None),
                    "human_finding_version_id": str(human.id) if human else None,
                    "scope": (
                        "finding" if isinstance(request, FindingReview) else "run"
                    ),
                },
            )
            unresolved = await session.scalar(
                select(AIFinding.id)
                .where(
                    AIFinding.run_id == run.id,
                    ~select(ReviewDecision.id)
                    .where(
                        ReviewDecision.ai_finding_id == AIFinding.id,
                        ReviewDecision.run_id == run.id,
                        ReviewDecision.finding_version == AIFinding.version,
                    )
                    .exists(),
                )
                .limit(1)
            )
            if unresolved is not None:
                return decision.id
            changed = await session.scalar(
                update(WorkflowRun)
                .where(
                    WorkflowRun.id == run.id, WorkflowRun.status == "awaiting_review"
                )
                .values(status="resume_queued")
                .returning(WorkflowRun.id)
            )
            if changed is None:
                raise ReviewConflict("Run state changed")
            session.add(DispatchOutbox(run_id=run.id, operation="resume"))
            await append_workflow_event(
                session,
                run_id=run.id,
                event_key="run:resume_queued",
                event_type="run.resume_queued",
                run_status="resume_queued",
            )
            return decision.id
    except IntegrityError as exc:
        # A concurrent same-key request on another Run can race the initial read.
        async with sessions() as session:
            previous = await replay(session, request)
            if previous is not None:
                return previous
        raise ReviewConflict("A review or resume request already exists") from exc
