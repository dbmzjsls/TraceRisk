from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tracerisk.reviews.router import IdempotencyKey, Reviewer
from tracerisk.reviews.schemas import ReviewResponse
from tracerisk.reviews.service import ReviewConflict, RunReview, submit_review

router = APIRouter(prefix="/runs", tags=["reviews"])


class RunReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Reason must not be blank")
        return value


@router.post("/{run_id}/review", response_model=ReviewResponse)
async def review_run(
    run_id: UUID,
    payload: RunReviewRequest,
    idempotency_key: IdempotencyKey,
    user: Reviewer,
    request: Request,
) -> ReviewResponse:
    try:
        decision_id = await submit_review(
            request.app.state.session_factory,
            RunReview(
                run_id=run_id,
                reviewer_id=user.id,
                idempotency_key=idempotency_key,
                **payload.model_dump(),
            ),
        )
    except ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return ReviewResponse(decision_id=decision_id)
