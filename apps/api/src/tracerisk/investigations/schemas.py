from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from tracerisk.reviews.schemas import PendingFindingResponse


class InvestigationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    as_of_date: date


class CurrentRunResponse(BaseModel):
    id: UUID
    run_number: int
    status: Literal[
        "queued", "running", "awaiting_review", "resume_queued", "completed", "failed"
    ]
    report_id: UUID | None


class RerunResponse(BaseModel):
    id: UUID
    run_number: int
    status: Literal["queued"] = "queued"


class InvestigationResponse(BaseModel):
    id: UUID
    company_id: UUID
    company_name: str
    created_by: UUID
    as_of_date: date
    current_run: CurrentRunResponse


class InvestigationListResponse(BaseModel):
    items: list[InvestigationResponse]


class StepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    attempt_count: int


class RuleEvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    evidence_id: UUID
    content_hash: str
    chunk_id: UUID | None
    chunk_content_hash: str | None


class RuleFindingResponse(BaseModel):
    id: UUID
    rule_code: str
    rule_version: str
    score: int
    parameters: dict[str, object]
    evidence: list[RuleEvidenceResponse]


class RetrievedEvidenceResponse(BaseModel):
    risk_type: str
    rank: int
    chunk_id: UUID
    evidence_id: UUID
    source_uri: str
    title: str
    observed_at: date
    fact_type: str
    fact_key: str
    fact_value: str
    polarity: str
    content: str
    content_hash: str


class DecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ai_finding_id: UUID | None
    finding_version: int | None
    reviewer_id: UUID
    decision: Literal["approve", "modify", "reject"]
    reason: str
    human_finding_version_id: UUID | None = None
    human_version: int | None = None
    replacement: dict[str, object] | None = None


class CoverageEntryResponse(BaseModel):
    source_type: Literal["business", "judicial", "news", "intellectual_property"]
    status: Literal["available", "unavailable", "stale", "missing"]
    coverage_id: UUID | None
    checked_at: datetime | None


class CoverageResponse(BaseModel):
    entries: list[CoverageEntryResponse]
    insufficiency_details: list[str]
    evidence_sufficiency: Literal["sufficient", "insufficient", "unavailable"]


class ConflictSideResponse(BaseModel):
    source_type: str
    fact_value: str
    observed_at: date
    evidence_id: UUID
    content_hash: str


class ConflictResponse(BaseModel):
    company_id: UUID
    risk_type: str
    fact_type: str
    fact_key: str
    authority_source: str | None
    resolved_value: str | None
    resolution_state: Literal["resolved", "unresolved"]
    sides: list[ConflictSideResponse]


class InvestigationDetailResponse(InvestigationResponse):
    retrieval_state: Literal["pending", "legacy_unavailable", "ready"]
    steps: list[StepResponse]
    rule_score: int
    rule_findings: list[RuleFindingResponse]
    retrieved_evidence: list[RetrievedEvidenceResponse]
    ai_findings: list[PendingFindingResponse]
    decisions: list[DecisionResponse]
    coverage: CoverageResponse
    conflicts: list[ConflictResponse]
