from datetime import date, timedelta
from uuid import UUID

from tracerisk.rules.judicial import (
    JUDICIAL_RULE_CODE,
    JUDICIAL_RULE_SCORE,
    JUDICIAL_RULE_VERSION,
    JudicialFact,
    evaluate_judicial_open_cases,
)

AS_OF_DATE = date(2026, 9, 11)


def fact(
    number: int, case_number: str, status: str, observed_at: date = AS_OF_DATE
) -> JudicialFact:
    return JudicialFact(
        evidence_id=UUID(int=number),
        case_number=case_number,
        status=status,
        observed_at=observed_at,
        content_hash=f"{number:064x}",
    )


def test_multiple_open_cases_score_once_and_keep_distinct_references() -> None:
    result = evaluate_judicial_open_cases(
        [fact(1, "SYN-CASE-001", "open"), fact(2, "SYN-CASE-002", "open")],
        as_of_date=AS_OF_DATE,
    )

    assert result.rule_code == JUDICIAL_RULE_CODE
    assert result.rule_version == JUDICIAL_RULE_VERSION
    assert result.score == JUDICIAL_RULE_SCORE
    assert result.matched_fact_keys == ("SYN-CASE-001", "SYN-CASE-002")
    assert tuple(ref.evidence_id for ref in result.evidence_references) == (
        UUID(int=1),
        UUID(int=2),
    )
    assert result.parameters == {
        "as_of_date": "2026-09-11",
        "required_status": "open",
        "minimum_cases": 1,
        "dedup_key": "case_number",
        "state_selection": "latest_on_or_before_as_of_date",
    }


def test_latest_state_at_baseline_controls_each_case() -> None:
    result = evaluate_judicial_open_cases(
        [
            fact(1, "SYN-CASE-001", "open", AS_OF_DATE - timedelta(days=2)),
            fact(2, "SYN-CASE-001", "closed", AS_OF_DATE - timedelta(days=1)),
            fact(3, "SYN-CASE-002", "open", AS_OF_DATE + timedelta(days=1)),
        ],
        as_of_date=AS_OF_DATE,
    )

    assert not result.is_match
    assert result.score == 0
    assert result.matched_fact_keys == ()
    assert result.evidence_references == ()


def test_duplicate_case_uses_only_latest_supporting_evidence() -> None:
    result = evaluate_judicial_open_cases(
        [
            fact(1, "SYN-CASE-001", "open", AS_OF_DATE - timedelta(days=2)),
            fact(2, "SYN-CASE-001", "open", AS_OF_DATE - timedelta(days=1)),
        ],
        as_of_date=AS_OF_DATE,
    )

    assert result.score == 35
    assert result.matched_fact_keys == ("SYN-CASE-001",)
    assert tuple(ref.evidence_id for ref in result.evidence_references) == (
        UUID(int=2),
    )


def test_equal_day_uses_lexically_greater_uuid_and_orders_references() -> None:
    result = evaluate_judicial_open_cases(
        [
            fact(9, "Z-CASE", "open"),
            fact(2, "A-CASE", "open"),
            fact(3, "A-CASE", "closed"),
            fact(1, "FUTURE", "open", AS_OF_DATE + timedelta(days=1)),
        ],
        as_of_date=AS_OF_DATE,
    )

    assert result.score == 35
    assert result.matched_fact_keys == ("Z-CASE",)
    assert tuple(ref.evidence_id for ref in result.evidence_references) == (
        UUID(int=9),
    )


def test_duplicate_identical_fact_does_not_duplicate_reference() -> None:
    duplicate = fact(1, "SYN-CASE-001", "open")
    result = evaluate_judicial_open_cases([duplicate, duplicate], as_of_date=AS_OF_DATE)

    assert result.matched_fact_keys == ("SYN-CASE-001",)
    assert len(result.evidence_references) == 1
