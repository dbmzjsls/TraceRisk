from datetime import date, timedelta
from uuid import UUID

from tracerisk.rules.news import NewsFact, evaluate_negative_news

AS_OF_DATE = date(2026, 9, 11)


def fact(
    number: int,
    key: str,
    *,
    days: int = 0,
    polarity: str = "negative",
) -> NewsFact:
    return NewsFact(
        UUID(int=number),
        key,
        polarity,
        AS_OF_DATE + timedelta(days=days),
        f"{number:064x}",
    )


def test_inclusive_365_day_window_and_two_story_threshold() -> None:
    result = evaluate_negative_news(
        [
            fact(1, "D-365", days=-365),
            fact(2, "D-364", days=-364),
            fact(3, "D"),
            fact(4, "D+1", days=1),
        ],
        as_of_date=AS_OF_DATE,
    )

    assert result.rule_code == "NEGATIVE_NEWS_V1"
    assert result.rule_version == "1"
    assert result.score == 20
    assert result.matched_fact_keys == ("D", "D-364")
    assert tuple(ref.evidence_id for ref in result.evidence_references) == (
        UUID(int=3),
        UUID(int=2),
    )


def test_duplicate_story_across_sources_counts_once() -> None:
    result = evaluate_negative_news(
        [fact(1, "ONE"), fact(2, "ONE")], as_of_date=AS_OF_DATE
    )

    assert result.score == 0
    assert result.matched_fact_keys == ()
    assert result.evidence_references == ()


def test_duplicate_story_with_second_story_uses_stable_representative() -> None:
    result = evaluate_negative_news(
        [fact(1, "ONE"), fact(2, "ONE"), fact(3, "TWO")],
        as_of_date=AS_OF_DATE,
    )

    assert result.score == 20
    assert result.matched_fact_keys == ("ONE", "TWO")
    assert tuple(ref.evidence_id for ref in result.evidence_references) == (
        UUID(int=2),
        UUID(int=3),
    )


def test_nonnegative_story_does_not_qualify() -> None:
    result = evaluate_negative_news(
        [fact(1, "NEG"), fact(2, "NEUTRAL", polarity="neutral")],
        as_of_date=AS_OF_DATE,
    )

    assert result.score == 0
    assert result.matched_fact_keys == ()
