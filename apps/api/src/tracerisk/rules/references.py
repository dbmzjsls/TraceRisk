from dataclasses import dataclass
from uuid import UUID

from tracerisk.evidence.models import EvidenceChunk, RetrievedEvidence
from tracerisk.evidence.service import RISK_ORDER
from tracerisk.rules.models import RuleFindingEvidence

_RISK_POSITION = {risk: position for position, risk in enumerate(RISK_ORDER)}


@dataclass(frozen=True)
class RuleReferenceTarget:
    chunk_id: UUID | None
    chunk_content_hash: str | None


def resolve_rule_evidence_targets(
    references: list[RuleFindingEvidence],
    frozen_rows: list[tuple[EvidenceChunk, RetrievedEvidence]],
) -> dict[tuple[UUID, UUID], RuleReferenceTarget]:
    """Resolve stored Evidence versions only against this Run's frozen chunks."""
    resolved: dict[tuple[UUID, UUID], RuleReferenceTarget] = {}
    for reference in references:
        candidates = sorted(
            (
                (chunk, retrieval)
                for chunk, retrieval in frozen_rows
                if chunk.evidence_id == reference.evidence_id
                and chunk.evidence_content_hash == reference.content_hash
            ),
            key=lambda row: (
                _RISK_POSITION.get(row[1].risk_type, len(RISK_ORDER)),
                row[1].rank,
                str(row[0].id),
            ),
        )
        target = candidates[0][0] if candidates else None
        resolved[(reference.rule_finding_id, reference.evidence_id)] = (
            RuleReferenceTarget(
                chunk_id=target.id if target else None,
                chunk_content_hash=target.content_hash if target else None,
            )
        )
    return resolved
