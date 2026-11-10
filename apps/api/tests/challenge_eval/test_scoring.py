import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from tracerisk.challenge_eval.schemas import AttemptRecord, ChallengeCase
from tracerisk.challenge_eval.scoring import score_case
from tracerisk.challenge_eval.validation import validate_response

CONFIG = json.loads(
    (
        Path(__file__).resolve().parents[4]
        / "datasets/evaluation/model_challenge_config.json"
    ).read_text(encoding="utf-8")
)


def attempt(case: ChallengeCase, content: str = '{"findings": []}', **updates: object):
    values = {
        "case_id": case.case_id,
        "attempt_number": 1,
        "request_kind": "original",
        "requested_at": "2026-10-23T01:00:00Z",
        "pricing_tier": "peak",
        "latency_ms": 10,
        "status": "succeeded",
        "retryable": False,
        "requested_model": "deepseek-flash",
        "response_content": content,
        "input_tokens": 100,
        "cache_hit_tokens": 40,
        "cache_miss_tokens": 60,
        "output_tokens": 20,
        "total_tokens": 120,
        **updates,
    }
    return AttemptRecord.model_validate(values)


def score(case: ChallengeCase, attempts=None, **kwargs):
    attempts = attempts or [attempt(case)]
    return score_case(
        case,
        attempts,
        [
            validate_response(case, item.response_content)
            if item.status == "succeeded"
            else None
            for item in attempts
        ],
        CONFIG,
        failure_localization_ms=7,
        **kwargs,
    )


def test_valid_empty_is_usable_and_gold_differences_are_not_human_edits(valid_case):
    result = score(valid_case)
    assert result.first_attempt_usable
    assert result.actual_action == "reject"
    assert result.gold_field_differences == ["actual_action"]
    assert result.gold_field_difference_count == 1
    assert result.human_modified_field_count is None
    assert result.review_duration_ms is None
    assert result.review_measurement_status == "not_measured"


@pytest.mark.parametrize(
    "content,error",
    [
        ("invalid", "structure_error"),
        ('{"findings": [{"finding_key": "broken"}]}', "structure_error"),
    ],
)
def test_unusable_first_response_preserved_after_repair(valid_case, content, error):
    records = [
        attempt(valid_case, content),
        attempt(valid_case, attempt_number=2, request_kind="validation_repair"),
    ]
    result = score(valid_case, records)
    assert not result.first_attempt_usable
    assert result.validation_errors == [error]
    assert (
        result.logical_call_count,
        result.http_attempt_count,
        result.retry_count,
    ) == (2, 2, 1)
    assert result.validation_repair_count == 1
    assert result.total_model_latency_ms == 20


def test_transport_retry_is_one_logical_call_and_preserves_execution_failure(
    valid_case,
):
    records = [
        attempt(
            valid_case,
            status="failed",
            response_content=None,
            error_category="rate_limit",
            retryable=True,
        ),
        attempt(valid_case, attempt_number=2, request_kind="transport_retry"),
    ]
    result = score(valid_case, records)
    assert result.logical_call_count == 1
    assert result.transport_retry_count == 1
    assert result.execution_status == "completed"
    assert result.execution_error_categories == ["rate_limit"]
    assert not result.first_attempt_usable


def test_unavailable_actual_action_is_not_a_rejection(valid_case):
    record = attempt(
        valid_case,
        status="failed",
        response_content=None,
        error_category="authentication",
    )
    result = score(valid_case, [record])
    assert result.actual_action is None
    assert result.execution_status == "failed"
    assert result.correct_reject_count == 0
    assert result.missed_review_count == 0


def test_decimal_cost_and_json_string_serialization(valid_case):
    result = score(valid_case)
    expected = (
        Decimal(40) * Decimal("0.006")
        + Decimal(60) * Decimal("0.3")
        + Decimal(20) * Decimal("1.2")
    ) / Decimal(1_000_000)
    assert result.conservative_estimated_cost_usd == expected
    assert result.model_dump(mode="json")["conservative_estimated_cost_usd"] == str(
        expected
    )
    off_peak = score_case(
        valid_case,
        [attempt(valid_case, pricing_tier="off_peak")],
        [validate_response(valid_case, '{"findings": []}')],
        CONFIG,
        failure_localization_ms=0,
    )
    assert off_peak.conservative_estimated_cost_usd == expected / 2


@pytest.mark.parametrize("field", ["requested_at", "pricing_tier"])
def test_each_attempt_requires_auditable_request_time_and_tier(valid_case, field):
    values = attempt(valid_case).model_dump()
    values.pop(field, None)
    with pytest.raises(ValidationError, match=field):
        AttemptRecord.model_validate(values)


@pytest.mark.parametrize(
    "timestamp", ["2026-10-23T01:00:00", "2026-10-23T09:00:00+08:00"]
)
def test_attempt_request_timestamp_must_be_utc(valid_case, timestamp):
    with pytest.raises(ValidationError, match="requested_at"):
        attempt(valid_case, requested_at=timestamp, pricing_tier="peak")


def test_retry_across_pricing_boundary_uses_each_attempt_tier(valid_case):
    records = [
        attempt(
            valid_case,
            "invalid",
            requested_at="2026-10-23T03:59:59Z",
            pricing_tier="peak",
        ),
        attempt(
            valid_case,
            attempt_number=2,
            request_kind="validation_repair",
            requested_at="2026-10-23T04:00:01Z",
            pricing_tier="off_peak",
        ),
    ]
    result = score_case(
        valid_case,
        records,
        [validate_response(valid_case, record.response_content) for record in records],
        CONFIG,
        failure_localization_ms=0,
    )
    assert result.conservative_estimated_cost_usd == Decimal("0.00006336")


@pytest.mark.parametrize(
    "field", ["cache_hit_tokens", "cache_miss_tokens", "output_tokens"]
)
def test_missing_required_usage_nulls_cost_without_inventing_splits(valid_case, field):
    result = score(valid_case, [attempt(valid_case, **{field: None})])
    assert getattr(result, field) is None
    assert result.conservative_estimated_cost_usd is None


def test_usage_sums_preserve_each_missing_field(valid_case):
    records = [
        attempt(valid_case, "invalid", input_tokens=None, total_tokens=None),
        attempt(valid_case, attempt_number=2, request_kind="validation_repair"),
    ]
    result = score(valid_case, records)
    assert result.input_tokens is None
    assert result.total_tokens is None
    assert result.output_tokens == 40
    assert result.conservative_estimated_cost_usd is not None


@pytest.mark.parametrize(
    "expected,actual,correct,false_review,missed_review",
    [
        ("accept", "accept", "correct_accept_count", 0, 0),
        ("reject", "reject", "correct_reject_count", 0, 0),
        ("manual_review", "manual_review", "correct_manual_review_count", 0, 0),
        ("accept", "manual_review", None, 1, 0),
        ("manual_review", "reject", None, 0, 1),
    ],
)
def test_routing_counts(
    valid_case, expected, actual, correct, false_review, missed_review
):
    from tracerisk.challenge_eval.validation import ValidationOutcome

    valid_case.expected_action = expected
    result = score_case(
        valid_case,
        [attempt(valid_case)],
        [ValidationOutcome(actual_action=actual)],
        CONFIG,
        failure_localization_ms=0,
    )
    if correct:
        assert getattr(result, correct) == 1
    assert result.unnecessary_review_count == false_review
    assert result.missed_review_count == missed_review


def test_localization_null_only_means_interrupted_finalization(valid_case):
    outcome = validate_response(valid_case, '{"findings": []}')
    result = score_case(
        valid_case,
        [attempt(valid_case)],
        [outcome],
        CONFIG,
        failure_localization_ms=None,
    )
    assert result.failure_localization_ms is None
    assert result.failure_localization_status == "interrupted_before_finalize"
    assert score(valid_case).failure_localization_status == "measured"
    with pytest.raises(ValidationError):
        result.model_copy().model_validate(
            {**result.model_dump(), "failure_localization_status": "measured"}
        )


def test_unique_chunks_include_invalid_actual_citations_and_ignore_duplicates(
    valid_case,
):
    from uuid import UUID

    from tracerisk.challenge_eval.validation import ValidationOutcome

    valid_case.chunks.append(valid_case.chunks[0])
    ids = [valid_case.chunks[0].chunk_id, UUID(int=99)]
    result = score_case(
        valid_case,
        [attempt(valid_case)],
        [
            ValidationOutcome(
                actual_action="reject",
                error_type="citation_error",
                cited_chunk_ids=[*ids, ids[0]],
            )
        ],
        CONFIG,
        failure_localization_ms=0,
    )
    assert result.input_chunk_count == 1
    assert result.cited_chunk_count == 2
    assert result.cited_chunk_ids == ids
    assert not result.first_attempt_usable


def test_support_failure_is_not_first_usable(valid_case):
    from tracerisk.challenge_eval.validation import ValidationOutcome

    result = score_case(
        valid_case,
        [attempt(valid_case)],
        [ValidationOutcome(actual_action="reject", error_type="support_error")],
        CONFIG,
        failure_localization_ms=0,
    )
    assert not result.first_attempt_usable


def test_reject_misaligned_attempt_validation_and_implicit_pricing(valid_case):
    with pytest.raises(ValueError, match="aligned"):
        score_case(
            valid_case,
            [attempt(valid_case)],
            [],
            CONFIG,
            failure_localization_ms=0,
        )
    with pytest.raises(ValidationError, match="pricing_tier"):
        attempt(valid_case, pricing_tier="unknown")


def test_review_measurements_must_be_complete_and_genuine(valid_case):
    with pytest.raises(ValueError, match="together"):
        score(valid_case, human_modified_field_count=2)
    measured = score(valid_case, human_modified_field_count=2, review_duration_ms=30)
    assert measured.gold_field_difference_count == 1
    assert measured.human_modified_field_count == 2
    assert measured.review_measurement_status == "measured"
