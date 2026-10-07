from dataclasses import FrozenInstanceError, replace

import pytest
from pydantic import ValidationError

from tracerisk.ai.claims import (
    CLAIM_CATALOG,
    ClaimFact,
    UnsupportedClaimError,
    canonical_statement,
    decide_claim_support,
    structured_statement,
)
from tracerisk.ai.schemas import RiskFindingDraft
from tracerisk.ai.validation import CitationValidationError, validate_draft_support

CASES = (
    ("JUDICIAL_OPEN_CASE", "judicial", "judicial_case_status", "open", "closed"),
    ("BUSINESS_ABNORMAL", "business", "business_abnormal_status", "active", "resolved"),
    ("NEGATIVE_NEWS", "news", "negative_news_story", "negative", "positive"),
    ("CORE_IP_ANOMALY", "intellectual_property", "core_ip_status", "invalid", "valid"),
    (
        "CORE_IP_ANOMALY",
        "intellectual_property",
        "core_ip_status",
        "ownership_dispute",
        "valid",
    ),
)


def fact(risk: str, fact_type: str, value: str) -> ClaimFact:
    return ClaimFact(risk, risk, fact_type, "RECORD-1", value, "negative")


def draft(code: str, risk: str, wording: str) -> RiskFindingDraft:
    return RiskFindingDraft.model_validate(
        {
            "finding_key": "human-or-model-draft",
            "risk_type": risk,
            "claim_code": code,
            "title": wording,
            "summary": wording,
            "reasoning": wording,
            "evidence_citations": [
                {
                    "evidence_id": "00000000-0000-0000-0000-000000000001",
                    "chunk_id": "00000000-0000-0000-0000-000000000002",
                    "content_hash": "a" * 64,
                    "quoted_excerpt": "frozen text",
                }
            ],
            "evidence_sufficiency": "sufficient",
            "confidence_level": "high",
            "review_recommended": False,
        }
    )


def test_catalog_is_exact_and_immutable() -> None:
    assert set(CLAIM_CATALOG) == {row[0] for row in CASES}
    with pytest.raises(TypeError):
        CLAIM_CATALOG["NEW"] = CLAIM_CATALOG["JUDICIAL_OPEN_CASE"]
    with pytest.raises(FrozenInstanceError):
        CLAIM_CATALOG["JUDICIAL_OPEN_CASE"].risk_type = "news"
    with pytest.raises(UnsupportedClaimError):
        decide_claim_support("UNKNOWN", "judicial", ())


@pytest.mark.parametrize(("code", "risk", "fact_type", "value", "inverse"), CASES)
def test_four_domain_support(code, risk, fact_type, value, inverse) -> None:
    cited = fact(risk, fact_type, value)
    assert decide_claim_support(code, risk, (cited,)).status == "supported"
    for changed in (
        replace(cited, fact_value=inverse),
        replace(cited, polarity="positive"),
        replace(cited, polarity="neutral"),
        replace(cited, risk_type="wrong"),
        replace(cited, source_type="wrong"),
        replace(cited, fact_type="noncore_ip_status"),
    ):
        assert decide_claim_support(code, risk, (changed,)).status == "contradicted"
        # A valid citation cannot hide another mismatching citation.
        assert (
            decide_claim_support(code, risk, (cited, changed)).status == "contradicted"
        )
    assert decide_claim_support(code, "wrong", (cited,)).status == "contradicted"
    assert decide_claim_support(code, risk, ()).status == "indeterminate"


@pytest.mark.parametrize(("code", "risk", "fact_type", "value", "inverse"), CASES)
def test_shared_pure_boundary_for_model_and_future_human_drafts(
    code,
    risk,
    fact_type,
    value,
    inverse,
) -> None:
    cited = fact(risk, fact_type, value)
    for wording in (canonical_statement(cited), structured_statement(cited)):
        original = draft(code, risk, wording)
        result = validate_draft_support(original, (cited,))
        assert result.support_status == "supported"
        assert result.support_reason == "canonical_fact_match"
        assert result.evidence_sufficiency == "sufficient"
        assert not result.review_recommended
        assert result.title == original.title
        assert "support_status" not in original.model_dump()
    for field in ("title", "summary", "reasoning"):
        original = draft(code, risk, canonical_statement(cited))
        original = original.model_copy(
            update={field: getattr(original, field) + " Will default soon."}
        )
        before = original.model_dump_json()
        result = validate_draft_support(original, (cited,))
        assert result.support_status == "indeterminate"
        assert result.support_reason == "unverified_free_text_extension"
        assert result.evidence_sufficiency == "insufficient"
        assert result.review_recommended
        assert (result.title, result.summary, result.reasoning) == (
            original.title,
            original.summary,
            original.reasoning,
        )
        assert original.model_dump_json() == before
    for facts in ((), (replace(cited, fact_value=inverse),)):
        with pytest.raises(CitationValidationError):
            validate_draft_support(original, facts)


def test_derived_fields_and_scores_cannot_be_supplied_by_any_draft() -> None:
    cited = fact("judicial", "judicial_case_status", "open")
    original = draft("JUDICIAL_OPEN_CASE", "judicial", canonical_statement(cited))
    for key, value in (
        ("support_status", "supported"),
        ("support_reason", "safe"),
        ("score", 0),
    ):
        with pytest.raises(ValidationError):
            RiskFindingDraft.model_validate({**original.model_dump(), key: value})
        with pytest.raises(CitationValidationError):
            validate_draft_support(original.model_copy(update={key: value}), (cited,))


def test_exact_statement_combinations_cannot_introduce_uncited_facts() -> None:
    first = fact("judicial", "judicial_case_status", "open")
    second = replace(first, fact_key="RECORD-2")
    original = draft("JUDICIAL_OPEN_CASE", "judicial", canonical_statement(first))
    combined = original.model_copy(
        update={
            "summary": canonical_statement(first) + "\n" + structured_statement(second),
        }
    )
    assert (
        validate_draft_support(combined, (first, second)).support_status == "supported"
    )
    assert validate_draft_support(combined, (first,)).support_status == "indeterminate"
    assert (
        validate_draft_support(
            original.model_copy(update={"title": original.title + " "}),
            (first,),
        ).support_reason
        == "unverified_free_text_extension"
    )
