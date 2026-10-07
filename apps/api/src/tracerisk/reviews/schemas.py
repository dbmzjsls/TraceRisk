from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tracerisk.ai.schemas import RiskFindingDraft


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "modify", "reject"]
    finding_version: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=2000)
    replacement: RiskFindingDraft | None = None

    @model_validator(mode="after")
    def modification_requires_replacement(self) -> "ReviewRequest":
        if (self.decision == "modify") != (self.replacement is not None):
            raise ValueError("Only modify requires replacement content")
        return self

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Reason must not be blank")
        return value


class ReviewResponse(BaseModel):
    decision_id: UUID


class PendingFindingResponse(BaseModel):
    finding_id: UUID
    finding_key: str
    finding_version: int
    run_id: UUID
    investigation_id: UUID
    company_id: UUID
    company_name: str
    risk_type: str
    claim_code: str
    title: str
    summary: str
    reasoning: str
    citations: list[dict[str, object]]
    evidence_sufficiency: str
    confidence_level: str
    review_recommended: bool
    support_status: Literal["supported", "indeterminate"]
    support_reason: Literal[
        "canonical_fact_match", "unverified_free_text_extension", "legacy-stage3"
    ]
