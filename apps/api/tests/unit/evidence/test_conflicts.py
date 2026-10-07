from dataclasses import replace
from datetime import date
from hashlib import sha256
from uuid import UUID

import pytest

from tracerisk.evidence.conflicts import ConflictFact, evaluate_conflicts

BASELINE = date(2026, 9, 11)
COMPANY = UUID("10000000-0000-0000-0000-000000000001")


def fact(
    serial: int,
    source: str,
    value: str,
    *,
    day: date = BASELINE,
    risk: str = "judicial",
    fact_type: str = "judicial_case_status",
    key: str = "CASE-1",
    company: UUID = COMPANY,
) -> ConflictFact:
    return ConflictFact(
        evidence_id=UUID(f"20000000-0000-0000-0000-{serial:012d}"),
        company_id=company,
        source_type=source,
        risk_type=risk,
        fact_type=fact_type,
        fact_key=key,
        fact_value=value,
        observed_at=day,
        content_hash=sha256(str(serial).encode()).hexdigest(),
    )


def test_latest_per_source_ignores_superseded_and_future_states() -> None:
    rows = [
        fact(1, "judicial", "open", day=date(2026, 9, 1)),
        fact(2, "judicial", "closed"),
        fact(3, "business", "open", day=date(2026, 9, 2)),
        fact(4, "business", "closed"),
        fact(5, "news", "open", day=date(2026, 9, 12)),
    ]
    result = evaluate_conflicts(rows, as_of_date=BASELINE)
    assert result.conflicts == ()
    assert len(result.resolved_facts) == 1
    assert result.resolved_facts[0].fact_value == "closed"


def test_authority_resolves_for_scoring_but_conflict_keeps_both_sides() -> None:
    result = evaluate_conflicts(
        [fact(1, "business", "closed"), fact(2, "judicial", "open")],
        as_of_date=BASELINE,
    )
    assert result.requires_review
    assert result.resolved_facts == (fact(2, "judicial", "open"),)
    conflict = result.conflicts[0]
    assert conflict.authority_source == "judicial"
    assert conflict.resolved_value == "open"
    assert tuple(side.source_type for side in conflict.sides) == (
        "business",
        "judicial",
    )
    assert tuple(side.evidence_id for side in conflict.sides) == (
        fact(1, "business", "closed").evidence_id,
        fact(2, "judicial", "open").evidence_id,
    )


@pytest.mark.parametrize(
    ("risk", "fact_type", "authority", "bad", "good"),
    [
        ("business", "business_abnormal_status", "business", "resolved", "active"),
        (
            "intellectual_property",
            "core_ip_status",
            "intellectual_property",
            "valid",
            "invalid",
        ),
    ],
)
def test_business_and_ip_authority(
    risk: str, fact_type: str, authority: str, bad: str, good: str
) -> None:
    result = evaluate_conflicts(
        [
            fact(1, "news", bad, risk=risk, fact_type=fact_type),
            fact(2, authority, good, risk=risk, fact_type=fact_type),
        ],
        as_of_date=BASELINE,
    )
    assert result.resolved_facts[0].fact_value == good
    assert result.conflicts[0].authority_source == authority


def test_no_authority_excludes_only_conflicting_fact() -> None:
    conflicting = [
        fact(1, "business", "negative", risk="news", fact_type="negative_news_story"),
        fact(2, "judicial", "positive", risk="news", fact_type="negative_news_story"),
    ]
    unrelated = fact(
        3,
        "news",
        "negative",
        risk="news",
        fact_type="negative_news_story",
        key="STORY-2",
    )
    result = evaluate_conflicts([*conflicting, unrelated], as_of_date=BASELINE)
    assert result.conflicts[0].authority_source is None
    assert result.conflicts[0].resolved_value is None
    assert result.has_unresolved
    assert result.resolved_facts == (unrelated,)


def test_groups_are_isolated_and_output_is_stable() -> None:
    other_company = UUID("10000000-0000-0000-0000-000000000002")
    rows = [
        fact(4, "judicial", "open", key="B"),
        fact(3, "business", "closed", key="B"),
        fact(
            2,
            "intellectual_property",
            "valid",
            risk="intellectual_property",
            fact_type="core_ip_status",
            key="B",
        ),
        fact(1, "business", "closed", company=other_company, key="B"),
        fact(5, "judicial", "open", key="A"),
    ]
    first = evaluate_conflicts(rows, as_of_date=BASELINE)
    second = evaluate_conflicts(reversed(rows), as_of_date=BASELINE)
    assert first == second
    assert [(c.fact_type, c.fact_key) for c in first.conflicts] == [
        ("judicial_case_status", "B")
    ]


def test_same_day_lexical_uuid_wins_and_unknown_value_fails() -> None:
    newer = fact(9, "judicial", "closed")
    result = evaluate_conflicts(
        [fact(1, "judicial", "open"), newer], as_of_date=BASELINE
    )
    assert result.resolved_facts == (newer,)
    with pytest.raises(ValueError, match="Unknown normalized value"):
        evaluate_conflicts([replace(newer, fact_value="maybe")], as_of_date=BASELINE)
