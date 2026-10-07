from datetime import date, timedelta
from uuid import UUID

from tracerisk.rules.business import BusinessFact, evaluate_business_abnormal

AS_OF_DATE = date(2026, 9, 11)


def fact(
    number: int, key: str, status: str, observed_at: date = AS_OF_DATE
) -> BusinessFact:
    return BusinessFact(UUID(int=number), key, status, observed_at, f"{number:064x}")


def test_active_and_after_baseline_resolution_score_once() -> None:
    result = evaluate_business_abnormal(
        [
            fact(1, "ABN-1", "active", AS_OF_DATE - timedelta(days=1)),
            fact(2, "ABN-1", "resolved", AS_OF_DATE + timedelta(days=1)),
            fact(3, "ABN-2", "active"),
        ],
        as_of_date=AS_OF_DATE,
    )

    assert result.rule_code == "BUSINESS_ABNORMAL_V1"
    assert result.rule_version == "1"
    assert result.score == 25
    assert result.matched_fact_keys == ("ABN-1", "ABN-2")
    assert tuple(ref.evidence_id for ref in result.evidence_references) == (
        UUID(int=1),
        UUID(int=3),
    )


def test_resolution_on_baseline_clears_and_future_occurrence_is_excluded() -> None:
    result = evaluate_business_abnormal(
        [
            fact(1, "ABN-1", "active", AS_OF_DATE - timedelta(days=1)),
            fact(2, "ABN-1", "resolved"),
            fact(3, "ABN-2", "active", AS_OF_DATE + timedelta(days=1)),
        ],
        as_of_date=AS_OF_DATE,
    )

    assert result.score == 0
    assert result.evidence_references == ()


def test_duplicate_key_uses_latest_uuid_tiebreak_and_stable_reference_order() -> None:
    result = evaluate_business_abnormal(
        [
            fact(1, "Z", "active"),
            fact(3, "A", "active"),
            fact(2, "A", "active"),
            fact(1, "Z", "active"),
        ],
        as_of_date=AS_OF_DATE,
    )

    assert result.matched_fact_keys == ("A", "Z")
    assert tuple(ref.evidence_id for ref in result.evidence_references) == (
        UUID(int=3),
        UUID(int=1),
    )
