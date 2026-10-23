"""IO-free response checks against frozen input and production support rules."""

from collections.abc import Sequence
from hashlib import sha256
from itertools import combinations
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError

from tracerisk.ai.claims import (
    ClaimFact,
    UnsupportedClaimError,
    decide_claim_support,
)
from tracerisk.ai.schemas import StructuredRiskFindings, ValidatedRiskFinding
from tracerisk.ai.validation import CitationValidationError, validate_draft_support
from tracerisk.challenge_eval.schemas import (
    Attribution,
    ChallengeCase,
    ExpectedAction,
    StrictModel,
)

ErrorType = Literal["structure_error", "citation_error", "support_error"]


class ValidationOutcome(StrictModel):
    actual_action: ExpectedAction
    error_type: ErrorType | None = None
    cited_chunk_ids: list[UUID] = Field(default_factory=list)
    feedback: str | None = None
    attribution_candidate: Attribution | None = None
    findings: list[ValidatedRiskFinding] = Field(default_factory=list)


def build_validation_feedback(error_type: ErrorType, detail: str) -> str:
    """Baseline feedback names only the failed boundary and correction contract."""
    return f"{error_type}: {detail}. Return corrected JSON matching the schema."


def route_review(case: ChallengeCase, findings: Sequence[ValidatedRiskFinding]) -> bool:
    """Route visible same-time source conflicts and valid uncertain drafts."""
    exclusive_values = {
        "judicial_case_status": {"open", "closed"},
        "business_abnormal_status": {"active", "resolved"},
        "negative_news_story": {"negative", "positive"},
        "core_ip_status": {"valid", "invalid", "ownership_dispute"},
        "noncore_ip_status": {"valid", "invalid", "ownership_dispute"},
    }
    conflict = any(
        left.company_id == right.company_id == case.company_id
        and left.risk_type == right.risk_type
        and left.fact_type == right.fact_type
        and left.fact_key == right.fact_key
        # EvidenceContext normalizes observation dates before this boundary.
        and left.observed_at == right.observed_at
        and left.source_type != right.source_type
        and left.fact_value != right.fact_value
        and {left.fact_value, right.fact_value}
        <= exclusive_values.get(left.fact_type, set())
        for left, right in combinations(case.chunks, 2)
    )
    return conflict or any(
        finding.support_status == "indeterminate"
        or finding.evidence_sufficiency == "insufficient"
        or finding.review_recommended
        for finding in findings
    )


def validate_response(case: ChallengeCase, response_content: str) -> ValidationOutcome:
    """Parse strictly, resolve citations, check support, then consider review."""
    cited_ids: list[UUID] = []

    def reject(error_type: ErrorType, detail: str) -> ValidationOutcome:
        return ValidationOutcome(
            actual_action="reject",
            error_type=error_type,
            cited_chunk_ids=cited_ids,
            feedback=build_validation_feedback(error_type, detail),
            attribution_candidate=error_type,
        )

    try:
        parsed = StructuredRiskFindings.model_validate_json(
            response_content, strict=True
        )
    except ValidationError:
        return reject("structure_error", "Invalid response schema")

    cited_ids = list(
        dict.fromkeys(
            citation.chunk_id
            for finding in parsed.findings
            for citation in finding.evidence_citations
        )
    )
    by_id = {chunk.chunk_id: chunk for chunk in case.chunks}
    distractors = set(case.distractor_chunk_ids)
    resolved: list[list[ClaimFact]] = []
    # Resolve every citation before support or review: a later invalid citation
    # must not be hidden by an earlier finding's uncertainty or claim error.
    for finding in parsed.findings:
        cited_facts: list[ClaimFact] = []
        for citation in finding.evidence_citations:
            chunk = by_id.get(citation.chunk_id)
            if chunk is None:
                return reject("citation_error", "Chunk is outside input evidence")
            if chunk.company_id != case.company_id:
                return reject("citation_error", "Chunk belongs to another company")
            if chunk.chunk_id in distractors:
                return reject(
                    "citation_error", "Citation does not match target evidence"
                )
            if citation.evidence_id != chunk.evidence_id:
                return reject("citation_error", "Evidence does not own cited chunk")
            if (
                citation.content_hash != chunk.content_hash
                or sha256(chunk.content.encode("utf-8")).hexdigest()
                != chunk.content_hash
            ):
                return reject("citation_error", "Citation content version differs")
            if (
                not citation.quoted_excerpt.strip()
                or citation.quoted_excerpt not in chunk.content
            ):
                return reject("citation_error", "Excerpt is not continuous input text")
            cited_facts.append(
                ClaimFact(
                    risk_type=chunk.risk_type,
                    source_type=chunk.source_type,
                    fact_type=chunk.fact_type,
                    fact_key=chunk.fact_key,
                    fact_value=chunk.fact_value,
                    polarity=chunk.polarity,
                )
            )
        resolved.append(cited_facts)

    validated = []
    for finding, facts in zip(parsed.findings, resolved, strict=True):
        if finding.claim_code not in case.allowed_claim_codes:
            return reject("support_error", "Claim code is not allowed")
        try:
            decision = decide_claim_support(
                finding.claim_code, finding.risk_type, facts
            )
            if decision.status != "supported":
                return reject("support_error", decision.reason)
            validated.append(validate_draft_support(finding, facts))
        except (UnsupportedClaimError, CitationValidationError) as exc:
            return reject("support_error", str(exc))

    if route_review(case, validated):
        return ValidationOutcome(
            actual_action="manual_review",
            cited_chunk_ids=cited_ids,
            attribution_candidate="human_judgment",
            findings=validated,
        )
    if not validated:
        return ValidationOutcome(actual_action="reject", cited_chunk_ids=cited_ids)
    return ValidationOutcome(
        actual_action="accept", cited_chunk_ids=cited_ids, findings=validated
    )
