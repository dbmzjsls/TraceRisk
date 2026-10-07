from datetime import UTC, date, datetime
from uuid import uuid4

from tracerisk.evidence.coverage import CoverageRecord, evaluate_coverage

BASELINE = date(2026, 9, 11)


def record(source_type: str, status: str) -> CoverageRecord:
    return CoverageRecord(
        source_type=source_type,
        status=status,
        as_of_date=BASELINE,
        checked_at=datetime(2026, 9, 11, tzinfo=UTC),
        coverage_id=uuid4(),
    )


def test_available_empty_is_sufficient_in_stable_four_source_order() -> None:
    result = evaluate_coverage(
        [
            record("news", "available"),
            record("judicial", "available"),
            record("intellectual_property", "available"),
            record("business", "available"),
        ],
        as_of_date=BASELINE,
    )
    assert result.is_sufficient
    assert result.insufficiency_details == ()
    assert tuple(item.source_type for item in result.entries) == (
        "business",
        "judicial",
        "news",
        "intellectual_property",
    )


def test_unavailable_stale_and_missing_are_deterministic_insufficiencies() -> None:
    result = evaluate_coverage(
        [
            record("business", "unavailable"),
            record("judicial", "available"),
            record("news", "stale"),
        ],
        as_of_date=BASELINE,
    )
    assert not result.is_sufficient
    assert [(item.source_type, item.status) for item in result.entries] == [
        ("business", "unavailable"),
        ("judicial", "available"),
        ("news", "stale"),
        ("intellectual_property", "missing"),
    ]
    assert result.insufficiency_details == (
        "business:unavailable",
        "news:stale",
        "intellectual_property:missing",
    )


def test_wrong_date_duplicate_and_unknown_values_fail() -> None:
    duplicate = record("business", "available")
    try:
        evaluate_coverage([duplicate, duplicate], as_of_date=BASELINE)
    except ValueError as exc:
        assert "Duplicate" in str(exc)
    else:
        raise AssertionError("duplicate coverage must fail")

    try:
        evaluate_coverage([record("unknown", "available")], as_of_date=BASELINE)
    except ValueError as exc:
        assert "source" in str(exc)
    else:
        raise AssertionError("unknown source must fail")

    wrong_date = record("business", "available")
    wrong_date = CoverageRecord(
        source_type=wrong_date.source_type,
        status=wrong_date.status,
        as_of_date=date(2026, 9, 10),
        checked_at=wrong_date.checked_at,
        coverage_id=wrong_date.coverage_id,
    )
    try:
        evaluate_coverage([wrong_date], as_of_date=BASELINE)
    except ValueError as exc:
        assert "date" in str(exc)
    else:
        raise AssertionError("wrong coverage date must fail")

    try:
        evaluate_coverage([record("business", "unknown")], as_of_date=BASELINE)
    except ValueError as exc:
        assert "status" in str(exc)
    else:
        raise AssertionError("unknown coverage status must fail")
