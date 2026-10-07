from datetime import date
from uuid import UUID

import pytest

from tracerisk.rules.common import EvidenceReference, RuleEvaluation
from tracerisk.rules.engine import aggregate_rule_evaluations


def evaluation(code: str, score: int) -> RuleEvaluation:
    return RuleEvaluation(
        rule_code=code,
        rule_version="1",
        score=score,
        parameters={"as_of_date": date(2026, 9, 11).isoformat()},
        matched_fact_keys=(code,),
        evidence_references=(
            EvidenceReference(UUID(int=score + 1), f"{score + 1:064x}"),
        ),
    )


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        ([], 0),
        ([evaluation("JUDICIAL_OPEN_CASE_V1", 35)], 35),
        ([evaluation("BUSINESS_ABNORMAL_V1", 25)], 25),
        ([evaluation("NEGATIVE_NEWS_V1", 20)], 20),
        ([evaluation("CORE_IP_ANOMALY_V1", 15)], 15),
        (
            [
                evaluation("JUDICIAL_OPEN_CASE_V1", 35),
                evaluation("BUSINESS_ABNORMAL_V1", 25),
            ],
            60,
        ),
    ],
)
def test_aggregate_scores_zero_single_and_mixed(
    items: list[RuleEvaluation], expected: int
) -> None:
    assert aggregate_rule_evaluations(items).total_score == expected


def test_aggregate_is_fixed_order_exact_95_and_ignores_nonrule_inputs() -> None:
    items = [
        evaluation("CORE_IP_ANOMALY_V1", 15),
        evaluation("NEGATIVE_NEWS_V1", 20),
        evaluation("BUSINESS_ABNORMAL_V1", 25),
        evaluation("JUDICIAL_OPEN_CASE_V1", 35),
    ]
    result = aggregate_rule_evaluations(
        items,
        non_rule_inputs=({"ai_score": -500}, {"review_decision": "override"}),
    )

    assert result.total_score == 95
    assert tuple(item.rule_code for item in result.evaluations) == (
        "JUDICIAL_OPEN_CASE_V1",
        "BUSINESS_ABNORMAL_V1",
        "NEGATIVE_NEWS_V1",
        "CORE_IP_ANOMALY_V1",
    )


def test_aggregate_rejects_duplicate_codes_unknown_codes_and_invalid_bounds() -> None:
    item = evaluation("JUDICIAL_OPEN_CASE_V1", 35)
    with pytest.raises(ValueError, match="Duplicate rule code"):
        aggregate_rule_evaluations([item, item])
    with pytest.raises(ValueError, match="Unknown rule code"):
        aggregate_rule_evaluations([evaluation("UNKNOWN", 1)])
    with pytest.raises(ValueError, match="outside 0..95"):
        aggregate_rule_evaluations([evaluation("JUDICIAL_OPEN_CASE_V1", 96)])
    with pytest.raises(ValueError, match="total is outside 0..95"):
        aggregate_rule_evaluations(
            [
                evaluation("JUDICIAL_OPEN_CASE_V1", 50),
                evaluation("BUSINESS_ABNORMAL_V1", 46),
            ]
        )
    with pytest.raises(ValueError, match="Invalid fixed score"):
        aggregate_rule_evaluations([evaluation("JUDICIAL_OPEN_CASE_V1", 34)])
