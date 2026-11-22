from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from tracerisk.ai.schemas import ContentHash, RiskType
from tracerisk.investigations.schemas import (
    ModelExecutionResponse,
    RetrievedEvidenceResponse,
)

SectionKind = Literal[
    "overview",
    "rules",
    "findings",
    "original_ai",
    "coverage",
    "evidence",
    "review_actions",
    "limits",
]
AnswerStatus = Literal["answer", "clarify", "unsupported"]
ReasonCode = Literal["none", "scope_mismatch", "no_context", "outside_scope"]
AnswerRunStatus = Literal["awaiting_review", "completed"]
FindingSource = Literal["pending_ai", "original_ai", "ai", "human"]
SupportStatus = Literal["supported", "indeterminate"]
SupportReason = Literal[
    "canonical_fact_match", "unverified_free_text_extension", "legacy-stage3"
]
SelectionKey = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class AssistantAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation_id: UUID
    run_id: UUID
    question: Annotated[
        str, StringConstraints(min_length=1, max_length=2000, pattern=r"\S")
    ]


class AssistantSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AnswerStatus
    sections: list[SectionKind] = Field(max_length=8)
    rule_codes: list[SelectionKey] = Field(max_length=20)
    finding_keys: list[SelectionKey] = Field(max_length=80)
    chunk_ids: list[UUID] = Field(max_length=20)
    reason_code: ReasonCode

    @model_validator(mode="after")
    def coherent_selection(self) -> "AssistantSelection":
        values = (self.sections, self.rule_codes, self.finding_keys, self.chunk_ids)
        if any(len(items) != len(set(items)) for items in values):
            raise ValueError("Selection values must be unique")
        if self.status == "answer":
            if not self.sections or self.reason_code != "none":
                raise ValueError("Answer requires sections and reason none")
        elif (
            any(values)
            or self.reason_code
            not in {
                "clarify": {"scope_mismatch", "no_context"},
                "unsupported": {"outside_scope"},
            }[self.status]
        ):
            raise ValueError("Non-answer requires a reason and empty selections")
        return self


class AssistantFinding(BaseModel):
    id: UUID
    source: FindingSource
    version: int = Field(gt=0)
    risk_type: RiskType
    title: str
    summary: str
    support_status: SupportStatus
    support_reason: SupportReason
    citation_chunk_ids: list[UUID]


class AssistantSection(BaseModel):
    kind: SectionKind
    title: str
    lines: list[str]
    citation_chunk_ids: list[UUID]
    findings: list[AssistantFinding]


class AssistantUsage(BaseModel):
    prompt_tokens: int | None
    completion_tokens: int | None


class AssistantExecution(BaseModel):
    mode: Literal["mock", "real"]
    model: str
    prompt_version: Literal["investigation-assistant-v1"]
    latency_ms: int = Field(ge=0)
    usage: AssistantUsage | None


class AssistantAnswerResponse(BaseModel):
    answer_id: UUID
    investigation_id: UUID
    run_id: UUID
    report_id: UUID | None
    question: str
    context_sha256: ContentHash
    question_sha256: ContentHash
    answered_at: datetime
    status: AnswerStatus
    reason_code: ReasonCode
    sections: list[AssistantSection]
    citations: list[RetrievedEvidenceResponse]
    run_status: AnswerRunStatus
    rule_score: int = Field(ge=0, le=95)
    review_reasons: list[str]
    investigation_model_execution: ModelExecutionResponse
    assistant_execution: AssistantExecution
