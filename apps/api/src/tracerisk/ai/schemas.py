from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Text = Annotated[str, StringConstraints(min_length=1, max_length=10000, pattern=r"\S")]
Key = Annotated[str, StringConstraints(min_length=1, max_length=256, pattern=r"\S")]
ContentHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
RiskType = Literal["business", "judicial", "news", "intellectual_property"]


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceCitation(StrictSchema):
    evidence_id: UUID
    chunk_id: UUID
    content_hash: ContentHash
    quoted_excerpt: Text


class RiskFindingDraft(StrictSchema):
    finding_key: Annotated[
        str, StringConstraints(min_length=1, max_length=512, pattern=r"\S")
    ]
    risk_type: RiskType
    claim_code: Key
    title: Annotated[
        str, StringConstraints(min_length=1, max_length=512, pattern=r"\S")
    ]
    summary: Text
    reasoning: Text
    evidence_citations: list[EvidenceCitation] = Field(min_length=1, max_length=100)
    evidence_sufficiency: Literal["sufficient", "insufficient"]
    confidence_level: Literal["low", "medium", "high"]
    review_recommended: bool


class StructuredRiskFindings(StrictSchema):
    findings: list[RiskFindingDraft] = Field(default_factory=list, max_length=1000)


class ValidatedRiskFinding(RiskFindingDraft):
    """Internal result only; adapters and future human drafts use RiskFindingDraft."""

    support_status: Literal["supported", "indeterminate"]
    support_reason: Literal["canonical_fact_match", "unverified_free_text_extension"]


class ValidatedRiskFindings(StrictSchema):
    findings: list[ValidatedRiskFinding] = Field(default_factory=list, max_length=1000)


class EvidenceContext(StrictSchema):
    evidence_id: UUID
    chunk_id: UUID
    company_id: UUID
    risk_type: RiskType
    observed_at: date
    fact_type: Key
    fact_key: Key
    fact_value: Key
    polarity: Literal["positive", "negative", "neutral"]
    content: Text
    content_hash: ContentHash
    evidence_content_hash: ContentHash


class RiskContext(StrictSchema):
    company_id: UUID
    evidence_chunks: list[EvidenceContext] = Field(
        default_factory=list, max_length=1000
    )
