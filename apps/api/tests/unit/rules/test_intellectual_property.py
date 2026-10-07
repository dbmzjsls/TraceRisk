from datetime import date, timedelta
from uuid import UUID

from tracerisk.rules.intellectual_property import (
    IntellectualPropertyFact,
    evaluate_core_ip_anomaly,
)

AS_OF_DATE = date(2026, 9, 11)


def fact(
    number: int,
    key: str,
    status: str,
    *,
    fact_type: str = "core_ip_status",
    days: int = 0,
) -> IntellectualPropertyFact:
    return IntellectualPropertyFact(
        UUID(int=number),
        key,
        status,
        fact_type,
        AS_OF_DATE + timedelta(days=days),
        f"{number:064x}",
    )


def test_invalid_and_ownership_dispute_score_with_stable_references() -> None:
    result = evaluate_core_ip_anomaly(
        [fact(2, "Z", "ownership_dispute"), fact(1, "A", "invalid")],
        as_of_date=AS_OF_DATE,
    )

    assert result.rule_code == "CORE_IP_ANOMALY_V1"
    assert result.rule_version == "1"
    assert result.score == 15
    assert result.matched_fact_keys == ("A", "Z")
    assert tuple(ref.evidence_id for ref in result.evidence_references) == (
        UUID(int=1),
        UUID(int=2),
    )


def test_valid_noncore_and_future_anomalies_do_not_score() -> None:
    result = evaluate_core_ip_anomaly(
        [
            fact(1, "VALID", "valid"),
            fact(2, "NONCORE", "invalid", fact_type="noncore_ip_status"),
            fact(3, "FUTURE", "ownership_dispute", days=1),
        ],
        as_of_date=AS_OF_DATE,
    )

    assert result.score == 0
    assert result.evidence_references == ()


def test_latest_state_and_duplicate_rows_choose_one_reference() -> None:
    duplicate = fact(1, "IP-1", "invalid")
    result = evaluate_core_ip_anomaly(
        [duplicate, duplicate, fact(2, "IP-1", "valid")],
        as_of_date=AS_OF_DATE,
    )

    assert result.score == 0
    assert result.evidence_references == ()
