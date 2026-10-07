from tracerisk.ai.schemas import (
    EvidenceCitation,
    RiskContext,
    RiskFindingDraft,
    StructuredRiskFindings,
)


class MockModelAdapter:
    async def analyze_risk(self, context: RiskContext) -> StructuredRiskFindings:
        if not isinstance(context, RiskContext):
            raise TypeError("MockModelAdapter requires RiskContext")
        citations: list[EvidenceCitation] = []
        seen: set[str] = set()
        for chunk in sorted(
            context.evidence_chunks, key=lambda c: (c.fact_key, c.chunk_id)
        ):
            if (
                chunk.company_id != context.company_id
                or chunk.risk_type != "judicial"
                or chunk.fact_type != "judicial_case_status"
                or chunk.fact_value != "open"
                or chunk.polarity != "negative"
                or chunk.fact_key in seen
            ):
                continue
            seen.add(chunk.fact_key)
            citations.append(
                EvidenceCitation(
                    evidence_id=chunk.evidence_id,
                    chunk_id=chunk.chunk_id,
                    content_hash=chunk.content_hash,
                    quoted_excerpt=chunk.content,
                )
            )
        findings: list[RiskFindingDraft] = []
        if citations:
            findings.append(
                RiskFindingDraft(
                    finding_key="judicial-open-cases",
                    risk_type="judicial",
                    claim_code="JUDICIAL_OPEN_CASE",
                    title="Open judicial case",
                    summary=(
                        f"Synthetic judicial cases {', '.join(sorted(seen))} "
                        "remain open."
                    ),
                    reasoning="The supplied judicial facts record open cases.",
                    evidence_citations=citations,
                    evidence_sufficiency="sufficient",
                    confidence_level="high",
                    review_recommended=True,
                )
            )
        return StructuredRiskFindings(findings=findings)
