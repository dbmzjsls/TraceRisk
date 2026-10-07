from collections.abc import Sequence
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracerisk.ai.claims import (
    ClaimFact,
    UnsupportedClaimError,
    canonical_statement,
    decide_claim_support,
    structured_statement,
)
from tracerisk.ai.schemas import (
    RiskFindingDraft,
    StructuredRiskFindings,
    ValidatedRiskFinding,
    ValidatedRiskFindings,
)
from tracerisk.auth.models import Role, User
from tracerisk.evidence.service import RetrievalIntegrityError, load_frozen_retrieval
from tracerisk.investigations.models import Investigation, WorkflowRun


class CitationValidationError(ValueError):
    """A permanent citation boundary or structured support failure."""


def validate_draft_support(
    draft: RiskFindingDraft, cited_facts: Sequence[ClaimFact]
) -> ValidatedRiskFinding:
    """Shared pure support boundary for model and future human drafts.

    Callers must first resolve all citations against integrity-checked frozen Run
    evidence. This callable has no database access and never writes rule scores.
    Text is accepted only as complete catalog statements separated by newlines,
    or fixed JSON combinations of their structured fields. No prose is rewritten.
    """
    try:
        # model_copy/model_construct can bypass Pydantic's extra-field boundary.
        if set(vars(draft)) - set(RiskFindingDraft.model_fields):
            raise CitationValidationError("Unexpected draft fields")
        original = RiskFindingDraft.model_validate(draft.model_dump())
        decision = decide_claim_support(
            original.claim_code, original.risk_type, cited_facts
        )
    except (ValidationError, UnsupportedClaimError) as exc:
        raise CitationValidationError(str(exc)) from exc
    if decision.status != "supported":
        raise CitationValidationError(decision.reason)
    allowed = {
        wording
        for fact in cited_facts
        for wording in (canonical_statement(fact), structured_statement(fact))
    }
    canonical = all(
        all(line in allowed for line in text.split("\n"))
        for text in (original.title, original.summary, original.reasoning)
    )
    values = original.model_dump()
    if not canonical:
        values.update(evidence_sufficiency="insufficient", review_recommended=True)
    return ValidatedRiskFinding.model_validate(
        {
            **values,
            "support_status": "supported" if canonical else "indeterminate",
            "support_reason": (
                decision.reason if canonical else "unverified_free_text_extension"
            ),
        }
    )


async def validate_findings(
    session: AsyncSession,
    *,
    run_id: UUID,
    user_id: UUID,
    findings: StructuredRiskFindings,
) -> ValidatedRiskFindings:
    """Validate against saved retrieval and demo read permissions; never persist drafts.

    The caller owns the transaction and must persist only the returned schemas.
    """
    try:
        validated = StructuredRiskFindings.model_validate(findings.model_dump())
    except ValidationError as exc:
        raise CitationValidationError("Invalid finding schema") from exc
    with session.no_autoflush:
        company_id = await session.scalar(
            select(Investigation.company_id)
            .join(WorkflowRun, WorkflowRun.investigation_id == Investigation.id)
            .where(WorkflowRun.id == run_id)
        )
        if company_id is None:
            raise CitationValidationError("Run has no investigation company")
        role = await session.scalar(select(User.role).where(User.id == user_id))
        if role not in {role.value for role in Role}:
            raise CitationValidationError("User cannot read demo company evidence")
        try:
            frozen = await load_frozen_retrieval(session, run_id=run_id)
        except RetrievalIntegrityError as exc:
            raise CitationValidationError(str(exc)) from exc
        by_id = {(row.risk_type, chunk.id): chunk for chunk, row in frozen}
        supported = []
        for finding in validated.findings:
            cited_facts = []
            for citation in finding.evidence_citations:
                chunk = by_id.get((finding.risk_type, citation.chunk_id))
                if chunk is None:
                    raise CitationValidationError(
                        "Chunk is outside saved Run retrieval"
                    )
                if citation.evidence_id != chunk.evidence_id:
                    raise CitationValidationError("Evidence does not own cited Chunk")
                if citation.content_hash != chunk.content_hash:
                    raise CitationValidationError("Citation content version differs")
                if (
                    not citation.quoted_excerpt.strip()
                    or citation.quoted_excerpt not in chunk.content
                ):
                    raise CitationValidationError(
                        "Excerpt is not continuous stored text"
                    )
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
            supported.append(validate_draft_support(finding, cited_facts))
        return ValidatedRiskFindings(findings=supported)
