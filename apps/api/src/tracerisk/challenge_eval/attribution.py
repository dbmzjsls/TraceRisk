"""Deterministic behavioral attribution and a single permitted improvement."""

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Literal, cast

from pydantic import Field

from tracerisk.ai.schemas import ContentHash
from tracerisk.challenge_eval.schemas import Attribution, StrictModel
from tracerisk.challenge_eval.scoring import ScoredCase

Dimension = Literal["prompt", "context_order", "validation_feedback", "review_routing"]
ATTRIBUTION_ORDER: tuple[Attribution, ...] = (
    "structure_error",
    "citation_error",
    "support_error",
    "human_judgment",
)
DIMENSION_BY_ATTRIBUTION: dict[Attribution, Dimension] = {
    "structure_error": "prompt",
    "citation_error": "context_order",
    "support_error": "validation_feedback",
    "human_judgment": "review_routing",
}
WEIGHT_KEYS = {
    "unsafe_accept_or_missed_review",
    "wrong_reject",
    "unnecessary_review",
    "format_retry_only",
}


class ProblemEvidence(StrictModel):
    problem: Attribution
    changed_dimension: Dimension
    evidence_case_ids: list[str]
    weighted_impact: int = Field(ge=1)
    affected_case_count: int = Field(ge=1)
    human_modified_field_count: int | None = Field(ge=0)
    failure_localization_ms: int | None = Field(ge=0)


class ImprovementAnalysis(StrictModel):
    baseline_input_hashes: dict[str, ContentHash]
    impact_weights: dict[str, int]
    impact_weights_hash: ContentHash
    attributions: dict[str, Attribution | None]
    candidates: list[ProblemEvidence]
    selected_problem: ProblemEvidence | None


def attribute_case(result: ScoredCase) -> Attribution | None:
    """Attribute hard errors once, then nonideal routing or measured edits.

    External failures are not evidence for changing model behavior. A recovered
    transport retry is visible in execution/retry metrics but adds no attribution.
    """
    for category in ATTRIBUTION_ORDER[:-1]:
        if category in result.validation_errors:
            return category
    if result.execution_status == "failed":
        return None
    if result.actual_action != result.expected_action or (
        result.human_modified_field_count is not None
        and result.human_modified_field_count > 0
    ):
        return "human_judgment"
    return None


def impact_for_case(result: ScoredCase, impact_weights: Mapping[str, int]) -> int:
    """Weight the case's observed harm, independently of its error category."""
    if attribute_case(result) is None:
        return 0
    expected, actual = result.expected_action, result.actual_action
    if (actual == "accept" and expected != "accept") or (
        expected == "manual_review" and actual not in (None, "manual_review")
    ):
        key = "unsafe_accept_or_missed_review"
    elif actual == "reject" and expected == "accept":
        key = "wrong_reject"
    elif actual == "manual_review" and expected != "manual_review":
        key = "unnecessary_review"
    else:
        key = "format_retry_only"
    return impact_weights[key]


def _complete_sum(results: Sequence[ScoredCase], field: str) -> int | None:
    values = [getattr(result, field) for result in results]
    return None if any(value is None for value in values) else sum(values)


def _beats(candidate: ProblemEvidence, incumbent: ProblemEvidence) -> bool:
    for field in (
        "weighted_impact",
        "affected_case_count",
        "human_modified_field_count",
        "failure_localization_ms",
    ):
        left, right = getattr(candidate, field), getattr(incumbent, field)
        # Never turn incomplete recovery or unmeasured human work into zero.
        if left is not None and right is not None and left != right:
            return cast(bool, left > right)
    return ATTRIBUTION_ORDER.index(candidate.problem) < ATTRIBUTION_ORDER.index(
        incumbent.problem
    )


def select_improvement(
    results: Sequence[ScoredCase],
    *,
    impact_weights: Mapping[str, int],
    baseline_raw_inputs: Mapping[str, bytes],
) -> ImprovementAnalysis:
    """Hash every supplied raw baseline artifact and select one mapped dimension.

    The runner must supply its complete raw input inventory and freshly recomputed
    case scores. Canonical category/case ordering makes selection independent of
    input iteration order, including ties with incomplete optional measurements.
    """
    if set(impact_weights) != WEIGHT_KEYS or any(
        type(value) is not int or value <= 0 for value in impact_weights.values()
    ):
        raise ValueError(
            "impact weights must contain the four frozen positive integers"
        )
    if not baseline_raw_inputs:
        raise ValueError("baseline raw input inventory must not be empty")
    ordered = sorted(results, key=lambda result: result.case_id)
    if len({result.case_id for result in ordered}) != len(ordered):
        raise ValueError("duplicate case IDs in baseline")
    attributions = {result.case_id: attribute_case(result) for result in ordered}
    candidates = []
    for category in ATTRIBUTION_ORDER:
        affected = [
            result for result in ordered if attributions[result.case_id] == category
        ]
        if affected:
            candidates.append(
                ProblemEvidence(
                    problem=category,
                    changed_dimension=DIMENSION_BY_ATTRIBUTION[category],
                    evidence_case_ids=[result.case_id for result in affected],
                    weighted_impact=sum(
                        impact_for_case(result, impact_weights) for result in affected
                    ),
                    affected_case_count=len(affected),
                    human_modified_field_count=_complete_sum(
                        affected, "human_modified_field_count"
                    ),
                    failure_localization_ms=_complete_sum(
                        affected, "failure_localization_ms"
                    ),
                )
            )
    winner = None
    for candidate in candidates:
        if winner is None or _beats(candidate, winner):
            winner = candidate
    weights = dict(sorted(impact_weights.items()))
    return ImprovementAnalysis(
        baseline_input_hashes={
            name: sha256(raw).hexdigest()
            for name, raw in sorted(baseline_raw_inputs.items())
        },
        impact_weights=weights,
        impact_weights_hash=sha256(
            json.dumps(weights, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        attributions=attributions,
        candidates=candidates,
        selected_problem=winner,
    )


def verify_analysis(
    saved: ImprovementAnalysis,
    recomputed_results: Sequence[ScoredCase],
    *,
    impact_weights: Mapping[str, int],
    baseline_raw_inputs: Mapping[str, bytes],
) -> None:
    """Reject saved evidence unless the complete normalized recomputation agrees."""
    recomputed = select_improvement(
        recomputed_results,
        impact_weights=impact_weights,
        baseline_raw_inputs=baseline_raw_inputs,
    )
    if saved.model_dump(mode="json") != recomputed.model_dump(mode="json"):
        raise ValueError("saved analysis differs from recomputed baseline evidence")


def guard_single_dimension(
    baseline: Mapping[str, object],
    improved: Mapping[str, object],
    *,
    changed_dimension: str,
) -> Dimension:
    """Compare complete behavior inventories, including any unknown/frozen keys."""
    changed = {
        key
        for key in baseline.keys() | improved.keys()
        if key not in baseline or key not in improved or baseline[key] != improved[key]
    }
    if changed_dimension not in DIMENSION_BY_ATTRIBUTION.values() or changed != {
        changed_dimension
    }:
        raise ValueError("exactly the one declared allowed dimension must change")
    return cast(Dimension, changed_dimension)
