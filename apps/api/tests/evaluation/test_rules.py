import pytest

from tracerisk.evaluation.metrics import MetricResult, canonical_json, ratio_metric
from tracerisk.evaluation.runner import RULE_CASES, evaluate_rule_cases


def test_metric_counts_and_canonical_serialization():
    metric = ratio_metric(
        "rule_exact_match_accuracy",
        "exact matching cases / labeled cases",
        {"a": True, "b": False},
        source_snapshot_sha256="a" * 64,
    )
    assert metric.numerator == 1
    assert metric.denominator == 2
    assert metric.value == 0.5
    assert metric.status == "ok"
    assert metric.sample_ids == ("a", "b")
    assert canonical_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}\n'


@pytest.mark.parametrize(
    "kwargs",
    [
        {"numerator": -1, "denominator": 1, "sample_ids": ("a",)},
        {"numerator": 2, "denominator": 1, "sample_ids": ("a",)},
        {"numerator": 1, "denominator": 1, "sample_ids": ()},
        {"numerator": 1, "denominator": 1, "sample_ids": ("a", "a")},
    ],
)
def test_metric_rejects_invalid_counts(kwargs):
    with pytest.raises(ValueError):
        MetricResult(
            name="sample",
            formula="passed / total",
            source_snapshot_sha256="a" * 64,
            mode="mock",
            **kwargs,
        )


def test_empty_denominator_is_not_applicable():
    metric = ratio_metric(
        "coverage", "covered / required", {}, source_snapshot_sha256="a" * 64
    )
    assert metric.status == "not_applicable"
    assert metric.value is None


def test_fixed_rule_labels_exact_match_and_domain_groups():
    result = evaluate_rule_cases("a" * 64)
    metric = result["overall"]
    assert metric.numerator == len(RULE_CASES)
    assert metric.denominator == len(RULE_CASES)
    assert metric.value == 1.0
    assert set(result["per_domain"]) == {
        "business",
        "judicial",
        "news",
        "intellectual_property",
    }
    assert all(item["passed"] for item in result["cases"])
    assert all(
        item["actual_total"] == sum(item["expected_points"].values())
        for item in result["cases"]
    )
    assert any(item["case_id"] == "news-d364-included" for item in result["cases"])
    assert any(item["case_id"] == "source-insufficient" for item in result["cases"])
