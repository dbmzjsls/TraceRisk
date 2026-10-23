from hashlib import sha256

import pytest
from test_scoring import CONFIG, attempt

from tracerisk.challenge_eval.attribution import (
    attribute_case,
    guard_single_dimension,
    impact_for_case,
    select_improvement,
    verify_analysis,
)
from tracerisk.challenge_eval.scoring import score_case
from tracerisk.challenge_eval.validation import ValidationOutcome

WEIGHTS = {
    "unsafe_accept_or_missed_review": 4,
    "wrong_reject": 3,
    "unnecessary_review": 2,
    "format_retry_only": 1,
}


def scored(case, error=None, *, actual="reject", expected="reject", **updates):
    case = case.model_copy(update={"expected_action": expected})
    result = score_case(
        case,
        [attempt(case)],
        [ValidationOutcome(actual_action=actual, error_type=error)],
        CONFIG,
        failure_localization_ms=10,
    )
    return result.model_copy(update=updates)


def analyze(results, **kwargs):
    return select_improvement(
        results,
        impact_weights=WEIGHTS,
        baseline_raw_inputs=kwargs.pop(
            "baseline_raw_inputs", {"raw.jsonl": b"baseline"}
        ),
        **kwargs,
    )


def test_config_freezes_behavior_impact_before_baseline():
    assert CONFIG["impact_weights"] == WEIGHTS


@pytest.mark.parametrize(
    "errors,winner",
    [
        (["support_error", "citation_error", "structure_error"], "structure_error"),
        (["support_error", "citation_error"], "citation_error"),
        (["support_error"], "support_error"),
    ],
)
def test_mutually_exclusive_error_precedence(valid_case, errors, winner):
    result = scored(valid_case, validation_errors=errors)
    assert attribute_case(result) == winner


def test_human_judgment_only_for_nonideal_behavior(valid_case):
    assert (
        attribute_case(scored(valid_case, actual="manual_review")) == "human_judgment"
    )
    assert (
        attribute_case(
            scored(valid_case, actual="manual_review", expected="manual_review")
        )
        is None
    )
    assert attribute_case(scored(valid_case)) is None
    assert (
        attribute_case(scored(valid_case, human_modified_field_count=1))
        == "human_judgment"
    )


def test_external_failures_and_recovered_transport_do_not_select_model_change(
    valid_case,
):
    result = scored(
        valid_case,
        execution_status="failed",
        actual_action=None,
        execution_error_categories=["authentication"],
    )
    assert attribute_case(result) is None
    assert analyze([result]).selected_problem is None
    recovered = scored(
        valid_case,
        execution_error_categories=["rate_limit"],
        first_attempt_usable=False,
        transport_retry_count=1,
    )
    assert attribute_case(recovered) is None


@pytest.mark.parametrize(
    "expected,actual,weight",
    [
        ("reject", "accept", 4),
        ("manual_review", "reject", 4),
        ("manual_review", "accept", 4),
        ("accept", "reject", 3),
        ("reject", "manual_review", 2),
        ("reject", "reject", 1),
    ],
)
def test_impact_uses_expected_and_actual_not_error_category(
    valid_case, expected, actual, weight
):
    for error in ["structure_error", "citation_error", "support_error"]:
        assert (
            impact_for_case(
                scored(valid_case, error, expected=expected, actual=actual), WEIGHTS
            )
            == weight
        )


def test_weighted_impact_precedes_count_and_mapping_is_fixed(valid_case):
    cases = [
        scored(valid_case, "structure_error", case_id="format-a"),
        scored(valid_case, "structure_error", case_id="format-b"),
        scored(valid_case, "citation_error", case_id="unsafe", actual="accept"),
    ]
    selection = analyze(cases).selected_problem
    assert selection.problem == "citation_error"
    assert selection.changed_dimension == "context_order"
    assert selection.evidence_case_ids == ["unsafe"]
    assert selection.weighted_impact == 4
    assert selection.affected_case_count == 1


@pytest.mark.parametrize(
    "problem,dimension",
    [
        ("structure_error", "prompt"),
        ("citation_error", "context_order"),
        ("support_error", "validation_feedback"),
        ("human_judgment", "review_routing"),
    ],
)
def test_deterministic_dimension_mapping(valid_case, problem, dimension):
    error = None if problem == "human_judgment" else problem
    actual = "manual_review" if error is None else "reject"
    assert (
        analyze(
            [scored(valid_case, error, actual=actual)]
        ).selected_problem.changed_dimension
        == dimension
    )


def test_case_count_breaks_equal_impact(valid_case):
    cases = [
        scored(valid_case, "structure_error", case_id=f"format-{i}") for i in range(4)
    ]
    cases.append(
        scored(valid_case, "citation_error", case_id="unsafe", actual="accept")
    )
    assert analyze(cases).selected_problem.problem == "structure_error"


@pytest.mark.parametrize("incomplete", [False, True])
def test_human_edits_only_break_ties_when_both_candidates_complete(
    valid_case, incomplete
):
    cases = [
        scored(
            valid_case,
            "structure_error",
            case_id="a",
            human_modified_field_count=None if incomplete else 1,
        ),
        scored(valid_case, "citation_error", case_id="b", human_modified_field_count=2),
    ]
    assert analyze(cases).selected_problem.problem == (
        "structure_error" if incomplete else "citation_error"
    )


@pytest.mark.parametrize("incomplete", [False, True])
def test_time_only_breaks_ties_when_both_candidates_complete(valid_case, incomplete):
    cases = [
        scored(
            valid_case,
            "structure_error",
            case_id="a",
            failure_localization_ms=None if incomplete else 1,
        ),
        scored(valid_case, "citation_error", case_id="b", failure_localization_ms=50),
    ]
    analysis = analyze(cases)
    assert analysis.selected_problem.problem == (
        "structure_error" if incomplete else "citation_error"
    )
    assert analyze(list(reversed(cases))) == analysis


def test_any_null_within_candidate_skips_entire_time_tiebreaker(valid_case):
    cases = [
        scored(valid_case, "structure_error", case_id="a", failure_localization_ms=1),
        scored(valid_case, "structure_error", case_id="b", failure_localization_ms=1),
        scored(valid_case, "citation_error", case_id="c", failure_localization_ms=999),
        scored(valid_case, "citation_error", case_id="d", failure_localization_ms=None),
    ]
    assert analyze(cases).selected_problem.problem == "structure_error"


def test_saved_analysis_hashes_every_input_and_complete_recomputation(valid_case):
    cases = [scored(valid_case, "structure_error")]
    raw = {
        "raw.jsonl": b"raw",
        "manifest.json": b"manifest",
        "dataset.jsonl": b"dataset",
    }
    analysis = analyze(cases, baseline_raw_inputs=raw)
    assert analysis.baseline_input_hashes == {
        key: sha256(value).hexdigest() for key, value in raw.items()
    }
    assert analysis.impact_weights == WEIGHTS
    assert len(analysis.impact_weights_hash) == 64
    verify_analysis(analysis, cases, impact_weights=WEIGHTS, baseline_raw_inputs=raw)
    for altered in [
        analysis.model_copy(update={"impact_weights_hash": "0" * 64}),
        analysis.model_copy(update={"attributions": {valid_case.case_id: None}}),
        analysis.model_copy(update={"selected_problem": None}),
    ]:
        with pytest.raises(ValueError, match="recomputed"):
            verify_analysis(
                altered, cases, impact_weights=WEIGHTS, baseline_raw_inputs=raw
            )
    with pytest.raises(ValueError, match="recomputed"):
        verify_analysis(
            analysis,
            cases,
            impact_weights=WEIGHTS,
            baseline_raw_inputs={**raw, "raw.jsonl": b"tampered"},
        )


def test_duplicate_cases_and_empty_input_inventory_rejected(valid_case):
    result = scored(valid_case, "structure_error")
    with pytest.raises(ValueError, match="duplicate"):
        analyze([result, result])
    with pytest.raises(ValueError, match="baseline"):
        analyze([result], baseline_raw_inputs={})


@pytest.mark.parametrize("changed", [[], ["prompt", "context_order"], ["unknown"]])
def test_single_dimension_guard_rejects_zero_multiple_and_unknown(changed):
    baseline = {
        "prompt": {"hash": "a"},
        "context_order": ["a", "b"],
        "validation_feedback": "baseline",
        "review_routing": "baseline",
    }
    improved = {**baseline, **{key: "changed" for key in changed}}
    with pytest.raises(ValueError):
        guard_single_dimension(baseline, improved, changed_dimension="prompt")


def test_single_dimension_guard_checks_actual_and_declared_dimension():
    baseline = {"prompt": "a", "context_order": "a", "model": "frozen"}
    improved = {**baseline, "prompt": "b"}
    assert (
        guard_single_dimension(baseline, improved, changed_dimension="prompt")
        == "prompt"
    )
    with pytest.raises(ValueError):
        guard_single_dimension(baseline, improved, changed_dimension="context_order")
    with pytest.raises(ValueError):
        guard_single_dimension(
            baseline, {**improved, "model": "other"}, changed_dimension="prompt"
        )
