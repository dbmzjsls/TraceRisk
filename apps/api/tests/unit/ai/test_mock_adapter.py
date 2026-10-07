from datetime import date
from hashlib import sha256
from uuid import UUID

import pytest
from pydantic import ValidationError

from tracerisk.ai.mock_adapter import MockModelAdapter
from tracerisk.ai.schemas import EvidenceContext, RiskContext, RiskFindingDraft

pytestmark = pytest.mark.anyio


def chunk(number: int = 1, **changes: object) -> EvidenceContext:
    data: dict[str, object] = dict(
        evidence_id=UUID(int=10),
        chunk_id=UUID(int=number),
        company_id=UUID(int=20),
        risk_type="judicial",
        observed_at=date(2026, 9, 11),
        fact_type="judicial_case_status",
        fact_key="CASE-1",
        fact_value="open",
        polarity="negative",
        content="Synthetic case remains open.\nExact excerpt.",
        content_hash=sha256(
            b"Synthetic case remains open.\nExact excerpt."
        ).hexdigest(),
    )
    data.update(changes)
    data["evidence_content_hash"] = data["content_hash"]
    return EvidenceContext.model_validate(data)


async def test_deterministic_open_case_and_strict_schema() -> None:
    context = RiskContext(company_id=UUID(int=20), evidence_chunks=[chunk(2), chunk()])
    result = await MockModelAdapter().analyze_risk(context)
    assert result == await MockModelAdapter().analyze_risk(context)
    assert result == await MockModelAdapter().analyze_risk(
        context.model_copy(
            update={"evidence_chunks": list(reversed(context.evidence_chunks))}
        )
    )
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.claim_code == "JUDICIAL_OPEN_CASE"
    assert finding.evidence_sufficiency == "sufficient"
    assert finding.confidence_level == "high"
    assert finding.review_recommended is True
    assert finding.evidence_citations[0].chunk_id == UUID(int=1)
    assert finding.evidence_citations[0].quoted_excerpt in chunk().content
    with pytest.raises(ValidationError):
        RiskFindingDraft.model_validate({**finding.model_dump(), "score": 35})


@pytest.mark.parametrize(
    "changes",
    [
        dict(fact_value="closed"),
        dict(polarity="positive"),
        dict(risk_type="news"),
        dict(fact_type="other"),
    ],
)
async def test_no_unsupported_finding(changes: dict[str, object]) -> None:
    result = await MockModelAdapter().analyze_risk(
        RiskContext(company_id=UUID(int=20), evidence_chunks=[chunk(**changes)])
    )
    assert not result.findings


async def test_no_evidence_and_fact_key_order() -> None:
    adapter = MockModelAdapter()
    assert not (
        await adapter.analyze_risk(RiskContext(company_id=UUID(int=20)))
    ).findings
    result = await adapter.analyze_risk(
        RiskContext(
            company_id=UUID(int=20),
            evidence_chunks=[chunk(2, fact_key="B"), chunk(1, fact_key="A")],
        )
    )
    assert len(result.findings) == 1
    assert result.findings[0].finding_key == "judicial-open-cases"
    assert [c.chunk_id for c in result.findings[0].evidence_citations] == [
        UUID(int=1),
        UUID(int=2),
    ]
    assert result == await adapter.analyze_risk(
        RiskContext(
            company_id=UUID(int=20),
            evidence_chunks=[chunk(1, fact_key="A"), chunk(2, fact_key="B")],
        )
    )
