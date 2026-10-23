"""Strict contracts for frozen cases and measured evaluation artifacts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tracerisk.ai.schemas import ContentHash, EvidenceContext, Key, Text

Category = Literal[
    "explicit_evidence",
    "similar_entity_or_number",
    "source_conflict",
    "insufficient_evidence",
]
ExpectedAction = Literal["accept", "reject", "manual_review"]
Attribution = Literal[
    "structure_error", "citation_error", "support_error", "human_judgment"
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChallengeChunk(EvidenceContext):
    source_type: Key


class ChallengeCase(StrictModel):
    case_id: Key
    category: Category
    company_id: UUID
    description: Text
    chunks: list[ChallengeChunk] = Field(min_length=1)
    supporting_chunk_ids: list[UUID]
    distractor_chunk_ids: list[UUID]
    allowed_claim_codes: list[Key] = Field(min_length=1)
    expected_action: ExpectedAction
    rationale: Text


class AttemptRecord(StrictModel):
    case_id: Key
    attempt_number: int = Field(ge=1, le=2)
    request_kind: Literal["original", "transport_retry", "validation_repair"]
    latency_ms: int = Field(ge=0)
    status: Literal["succeeded", "failed"]
    retryable: bool
    requested_model: Key
    http_status: int | None = None
    error_category: Key | None = None
    returned_model: Key | None = None
    system_fingerprint: Key | None = None
    finish_reason: Key | None = None
    response_content: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_hit_tokens: int | None = Field(default=None, ge=0)
    cache_miss_tokens: int | None = Field(default=None, ge=0)


class CaseResult(StrictModel):
    case_id: str
    expected_action: ExpectedAction
    actual_action: ExpectedAction | None
    first_attempt_usable: bool
    attempts: list[AttemptRecord]
    failure_localization_ms: int | None = Field(ge=0)
    failure_localization_status: Literal["measured", "interrupted_before_finalize"]
    human_modified_field_count: int | None = Field(default=None, ge=0)
    review_duration_ms: int | None = Field(default=None, ge=0)
    review_measurement_status: Literal["measured", "not_measured"]
    attribution: Attribution | None = None


class ReviewRecord(StrictModel):
    case_id: Key
    decision: ExpectedAction
    changed_fields: list[Key]
    changed_field_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    validation_status: Literal["valid", "invalid"]
    reviewed_at: datetime


class RunManifest(StrictModel):
    run_id: Key
    phase: Literal["baseline", "improved"]
    created_at: datetime
    dataset_hash: ContentHash
    config_hash: ContentHash
    lock_hash: ContentHash
    prompt_hash: ContentHash
    fingerprint: ContentHash
    requested_model: Key
    status: Literal["incomplete", "completed"]
    environment: dict[str, object]
    source_inventory: dict[str, ContentHash]
    behavior_inventory: dict[str, dict[str, ContentHash]]
    changed_dimension: (
        Literal["prompt", "validation_feedback", "context_order", "review_routing"]
        | None
    ) = None


class RunSummary(StrictModel):
    run_id: Key
    case_count: int = Field(ge=0)
    first_attempt_usable_count: int = Field(ge=0)
    action_counts: dict[ExpectedAction, int]
    attribution_counts: dict[Attribution, int]
    total_human_modified_field_count: int | None = Field(default=None, ge=0)
    total_review_duration_ms: int | None = Field(default=None, ge=0)
