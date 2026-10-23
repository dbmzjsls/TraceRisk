"""Pure per-case measurements; usage gaps and unmeasured human work stay null."""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from tracerisk.challenge_eval.schemas import AttemptRecord, CaseResult, ChallengeCase
from tracerisk.challenge_eval.validation import ErrorType, ValidationOutcome


class ScoredCase(CaseResult):
    logical_call_count: int = Field(ge=1)
    http_attempt_count: int = Field(ge=1)
    retry_count: int = Field(ge=0)
    transport_retry_count: int = Field(ge=0)
    validation_repair_count: int = Field(ge=0)
    input_chunk_count: int = Field(ge=0)
    cited_chunk_count: int = Field(ge=0)
    cited_chunk_ids: list[UUID]
    input_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    cache_hit_tokens: int | None = Field(ge=0)
    cache_miss_tokens: int | None = Field(ge=0)
    total_tokens: int | None = Field(ge=0)
    conservative_estimated_cost_usd: Decimal | None = Field(ge=0)
    total_model_latency_ms: int = Field(ge=0)
    correct_accept_count: int = Field(ge=0, le=1)
    correct_reject_count: int = Field(ge=0, le=1)
    correct_manual_review_count: int = Field(ge=0, le=1)
    unnecessary_review_count: int = Field(ge=0, le=1)
    missed_review_count: int = Field(ge=0, le=1)
    gold_field_differences: list[str]
    gold_field_difference_count: int = Field(ge=0)
    validation_errors: list[ErrorType]
    execution_status: Literal["completed", "failed"]
    execution_error_categories: list[str]

    @model_validator(mode="after")
    def measurement_consistency(self) -> Self:
        if (self.failure_localization_ms is not None) != (
            self.failure_localization_status == "measured"
        ):
            raise ValueError("localization value and status must agree")
        measured = (
            self.human_modified_field_count is not None
            and self.review_duration_ms is not None
        )
        if (self.human_modified_field_count is None) != (
            self.review_duration_ms is None
        ) or measured != (self.review_measurement_status == "measured"):
            raise ValueError("human measurements must be supplied together")
        return self


def _usage_sum(attempts: Sequence[AttemptRecord], field: str) -> int | None:
    values = [getattr(attempt, field) for attempt in attempts]
    return None if any(value is None for value in values) else sum(values)


def _cost(
    attempts: Sequence[AttemptRecord], config: Mapping[str, object]
) -> Decimal | None:
    prices = config["price_per_million_tokens"]
    if not isinstance(prices, Mapping):
        raise ValueError("price_per_million_tokens must be a mapping")
    cost = Decimal(0)
    for attempt in attempts:
        for field, rate in (
            ("cache_hit_tokens", "cache_hit_input"),
            ("cache_miss_tokens", "cache_miss_input"),
            ("output_tokens", "output"),
        ):
            tokens = getattr(attempt, field)
            if tokens is None:
                return None
            price = Decimal(str(prices[f"{attempt.pricing_tier}_{rate}"]))
            if not price.is_finite() or price < 0:
                raise ValueError("prices must be finite and nonnegative")
            cost += Decimal(tokens) * price
    return cost / Decimal(1_000_000)


def score_case(
    case: ChallengeCase,
    attempts: Sequence[AttemptRecord],
    validations: Sequence[ValidationOutcome | None],
    config: Mapping[str, object],
    *,
    failure_localization_ms: int | None,
    human_modified_field_count: int | None = None,
    review_duration_ms: int | None = None,
) -> ScoredCase:
    """Score aligned HTTP attempts/outcomes, including failed transport slots.

    A logical call is the original generation or a validation repair; a transport
    retry repeats that logical call. Citation counts cover all actual responses,
    including unknown IDs and repaired responses, and deduplicate across attempts.
    Only routing has an exact frozen gold field: support IDs and allowed claims
    describe permissible evidence, not an exact required output or wording.
    The runner stamps each attempt with its UTC request time and explicit tier
    using the frozen calendar policy (including unknown-holiday peak fallback).
    Scoring uses those per-attempt tiers and never infers a tier from latency.
    """
    if not attempts or len(attempts) != len(validations):
        raise ValueError("attempts and validations must be nonempty and aligned")
    if len(attempts) > 2:
        raise ValueError("at most two HTTP attempts are allowed")
    for index, (attempt, validation) in enumerate(
        zip(attempts, validations, strict=True)
    ):
        if attempt.case_id != case.case_id or attempt.attempt_number != index + 1:
            raise ValueError("attempt identity/order does not match case")
        if (index == 0) != (attempt.request_kind == "original"):
            raise ValueError("only the first attempt can be original")
        if (attempt.status == "succeeded") != (validation is not None):
            raise ValueError("successful attempts require aligned validation outcomes")
    if (human_modified_field_count is None) != (review_duration_ms is None):
        raise ValueError("human measurements must be supplied together")

    final = validations[-1]
    actual = final.actual_action if final is not None else None
    first = validations[0]
    cited_ids = list(
        dict.fromkeys(
            chunk_id
            for validation in validations
            if validation is not None
            for chunk_id in validation.cited_chunk_ids
        )
    )
    errors = list(
        dict.fromkeys(
            validation.error_type
            for validation in validations
            if validation is not None and validation.error_type is not None
        )
    )
    differences = ["actual_action"] if actual != case.expected_action else []
    repair_count = sum(item.request_kind == "validation_repair" for item in attempts)
    result = ScoredCase(
        case_id=case.case_id,
        expected_action=case.expected_action,
        actual_action=actual,
        first_attempt_usable=first is not None and first.error_type is None,
        attempts=list(attempts),
        failure_localization_ms=failure_localization_ms,
        failure_localization_status=(
            "measured"
            if failure_localization_ms is not None
            else "interrupted_before_finalize"
        ),
        human_modified_field_count=human_modified_field_count,
        review_duration_ms=review_duration_ms,
        review_measurement_status=(
            "measured" if human_modified_field_count is not None else "not_measured"
        ),
        logical_call_count=1 + repair_count,
        http_attempt_count=len(attempts),
        retry_count=len(attempts) - 1,
        transport_retry_count=sum(
            item.request_kind == "transport_retry" for item in attempts
        ),
        validation_repair_count=repair_count,
        input_chunk_count=len({chunk.chunk_id for chunk in case.chunks}),
        cited_chunk_count=len(cited_ids),
        cited_chunk_ids=cited_ids,
        input_tokens=_usage_sum(attempts, "input_tokens"),
        output_tokens=_usage_sum(attempts, "output_tokens"),
        cache_hit_tokens=_usage_sum(attempts, "cache_hit_tokens"),
        cache_miss_tokens=_usage_sum(attempts, "cache_miss_tokens"),
        total_tokens=_usage_sum(attempts, "total_tokens"),
        conservative_estimated_cost_usd=_cost(attempts, config),
        total_model_latency_ms=sum(item.latency_ms for item in attempts),
        correct_accept_count=int(actual == case.expected_action == "accept"),
        correct_reject_count=int(actual == case.expected_action == "reject"),
        correct_manual_review_count=int(
            actual == case.expected_action == "manual_review"
        ),
        unnecessary_review_count=int(
            actual == "manual_review" and case.expected_action != actual
        ),
        missed_review_count=int(
            case.expected_action == "manual_review"
            and actual is not None
            and actual != "manual_review"
        ),
        gold_field_differences=differences,
        gold_field_difference_count=len(differences),
        validation_errors=errors,
        execution_status="completed" if final is not None else "failed",
        execution_error_categories=list(
            dict.fromkeys(
                item.error_category
                for item in attempts
                if item.status == "failed" and item.error_category is not None
            )
        ),
    )
    # Local import keeps the scoring contract available to attribution's models.
    from tracerisk.challenge_eval.attribution import attribute_case

    result.attribution = attribute_case(result)
    return result
