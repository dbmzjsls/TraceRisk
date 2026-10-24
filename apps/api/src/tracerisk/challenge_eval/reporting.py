"""Auditable reports derived only from validated challenge-run journals."""

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field

from tracerisk.ai.schemas import ContentHash
from tracerisk.challenge_eval.attribution import (
    ImprovementAnalysis,
    ProblemEvidence,
    impact_for_case,
    verify_analysis,
)
from tracerisk.challenge_eval.runner import (
    canonical,
    digest,
    recompute_run,
    snapshot_run_inputs,
    validate_review_record,
    verify_improvement,
    write_bytes,
    write_json,
)
from tracerisk.challenge_eval.schemas import (
    AttemptRecord,
    Attribution,
    ExpectedAction,
    ReviewRecord,
    RunManifest,
    StrictModel,
)
from tracerisk.challenge_eval.scoring import ScoredCase
from tracerisk.challenge_eval.scoring import _cost as estimate_cost

ACTION_ORDER: tuple[ExpectedAction, ...] = ("accept", "reject", "manual_review")
ATTRIBUTION_ORDER: tuple[Attribution, ...] = (
    "structure_error",
    "citation_error",
    "support_error",
    "human_judgment",
)
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_hit_tokens",
    "cache_miss_tokens",
    "total_tokens",
)


class RunReportSummary(StrictModel):
    run_id: str
    phase: Literal["baseline", "improved"]
    case_count: int = Field(ge=0)
    first_attempt_usable_count: int = Field(ge=0)
    action_counts: dict[ExpectedAction, int]
    correct_accept_count: int = Field(ge=0)
    correct_reject_count: int = Field(ge=0)
    correct_manual_review_count: int = Field(ge=0)
    unnecessary_review_count: int = Field(ge=0)
    missed_review_count: int = Field(ge=0)
    attribution_counts: dict[Attribution, int]
    validation_error_counts: dict[str, int]
    logical_call_count: int = Field(ge=0)
    http_attempt_count: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    transport_retry_count: int = Field(ge=0)
    validation_repair_count: int = Field(ge=0)
    api_failure_count: int = Field(ge=0)
    api_error_category_counts: dict[str, int]
    execution_failure_count: int = Field(ge=0)
    input_chunk_count: int = Field(ge=0)
    cited_chunk_count: int = Field(ge=0)
    input_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    cache_hit_tokens: int | None = Field(ge=0)
    cache_miss_tokens: int | None = Field(ge=0)
    total_tokens: int | None = Field(ge=0)
    conservative_estimated_cost_usd: Decimal | None = Field(ge=0)
    total_model_latency_ms: int = Field(ge=0)
    total_failure_localization_ms: int | None = Field(ge=0)
    interrupted_failure_localization_count: int = Field(ge=0)
    reviewed_case_count: int | None = Field(ge=0)
    total_human_modified_field_count: int | None = Field(ge=0)
    total_review_duration_ms: int | None = Field(ge=0)
    review_measurement_status: Literal["measured", "partial", "not_measured"]
    requested_models: list[str]
    returned_models: list[str]
    system_fingerprints: list[str]
    requested_model_counts: dict[str, int]
    returned_model_counts: dict[str, int]
    system_fingerprint_counts: dict[str, int]
    returned_model_missing_count: int = Field(ge=0)
    system_fingerprint_missing_count: int = Field(ge=0)
    environment: dict[str, object]
    model_configuration: dict[str, object]
    dataset_hash: ContentHash
    config_hash: ContentHash
    lock_hash: ContentHash
    prompt_hash: ContentHash
    source_inventory_hash: ContentHash


class ProcessDelta(StrictModel):
    first_attempt_usable_count: int
    validation_repair_count: int
    citation_error_case_count: int
    support_error_case_count: int
    human_judgment_case_count: int
    manual_review_action_count: int
    execution_failure_count: int
    selected_problem_case_count: int | None


class TypicalCase(StrictModel):
    case_id: str
    expected_action: ExpectedAction
    actual_action: ExpectedAction | None
    attribution: Attribution | None
    severity: int = Field(ge=0)
    cited_chunk_count: int = Field(ge=0)
    input_chunk_count: int = Field(ge=0)


class TypicalCases(StrictModel):
    success: TypicalCase | None
    failure: TypicalCase | None
    manual_review: TypicalCase | None


class ComparisonSummary(StrictModel):
    schema_version: Literal["1"] = "1"
    baseline: RunReportSummary
    improved: RunReportSummary
    delta: ProcessDelta
    selected_problem: ProblemEvidence | None
    changed_dimension: str | None
    causal_status: Literal["clean", "contaminated", "unverifiable"]
    causal_contamination: bool
    causal_contamination_reasons: list[str]
    typical_cases: TypicalCases
    provenance: dict[str, dict[str, ContentHash]]
    limitations: list[str]


class _RunEvidence:
    def __init__(
        self,
        manifest: RunManifest,
        recomputed: list[ScoredCase],
        raw: dict[str, bytes],
        raw_hashes: dict[str, ContentHash],
        cases: list[ScoredCase],
        attempts: list[AttemptRecord],
        reviews: list[ReviewRecord],
    ) -> None:
        self.manifest = manifest
        self.recomputed = recomputed
        self.raw = raw
        self.raw_hashes = raw_hashes
        self.cases = cases
        self.attempts = attempts
        self.reviews = reviews


def _jsonl(raw: bytes, name: str) -> list[dict[str, object]]:
    if raw and not raw.endswith(b"\n"):
        raise ValueError(f"malformed {name}: nonempty journal must end with newline")
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        try:
            value = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"malformed {name} line {line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"malformed {name} line {line_number}")
        records.append(value)
    return records


def _unique_by_case[TCaseRecord: (ScoredCase, ReviewRecord)](
    values: Sequence[TCaseRecord], name: str
) -> dict[str, TCaseRecord]:
    indexed: dict[str, TCaseRecord] = {}
    for value in values:
        if value.case_id in indexed:
            raise ValueError(f"duplicate case ID in {name}")
        indexed[value.case_id] = value
    return indexed


def _load_run(path: Path) -> _RunEvidence:
    raw = snapshot_run_inputs(path)
    raw_hashes = {name: digest(value) for name, value in sorted(raw.items())}
    manifest, recomputed, validated_raw = recompute_run(
        path, raw_inputs=raw, strict_read_only=True
    )
    if validated_raw != raw:
        raise ValueError("read-only validation changed raw input snapshot")
    required = {
        "manifest.json",
        "dataset.jsonl",
        "config.json",
        "attempts.jsonl",
        "case-events.jsonl",
        "cases.jsonl",
    }
    if not required <= raw.keys():
        raise ValueError("validated run is missing required raw artifacts")
    # Parse every journal again into its strict public contract. recompute_run
    # performs the deeper cross-check against snapshots and durable validations.
    _jsonl(raw["case-events.jsonl"], "case-events.jsonl")
    cases = [
        ScoredCase.model_validate(item)
        for item in _jsonl(raw["cases.jsonl"], "cases.jsonl")
    ]
    attempts = [
        AttemptRecord.model_validate(item)
        for item in _jsonl(raw["attempts.jsonl"], "attempts.jsonl")
    ]
    reviews = [
        ReviewRecord.model_validate(item)
        for item in _jsonl(raw.get("reviews.jsonl", b""), "reviews.jsonl")
    ]
    by_case = _unique_by_case(cases, "cases.jsonl")
    by_recomputed = _unique_by_case(recomputed, "recomputed cases")
    if by_case.keys() != by_recomputed.keys():
        raise ValueError("finalized and recomputed case IDs differ")
    seen_attempts: set[tuple[str, int]] = set()
    for attempt in attempts:
        identity = (attempt.case_id, attempt.attempt_number)
        if attempt.case_id not in by_case or identity in seen_attempts:
            raise ValueError("attempt has unknown or duplicate identity")
        if attempt.requested_model != manifest.requested_model:
            raise ValueError("attempt requested model differs from manifest")
        seen_attempts.add(identity)
    by_review = _unique_by_case(reviews, "reviews.jsonl")
    for case_id, review in by_review.items():
        result = by_case.get(case_id)
        if result is None or not isinstance(review, ReviewRecord):
            raise ValueError("review references unknown case")
        validate_review_record(review, result)
    _assert_run_snapshot_unchanged(path, raw)
    return _RunEvidence(manifest, recomputed, raw, raw_hashes, cases, attempts, reviews)


def _assert_run_snapshot_unchanged(path: Path, expected: Mapping[str, bytes]) -> None:
    if snapshot_run_inputs(path) != expected:
        raise ValueError(f"run inputs changed after snapshot: {path}")


def _sum_optional(values: Sequence[int | None]) -> int | None:
    return (
        None
        if any(value is None for value in values)
        else sum(value for value in values if value is not None)
    )


def _environment(manifest: RunManifest) -> dict[str, object]:
    allowed = ("python", "os", "dependencies")
    return {
        key: manifest.environment[key] for key in allowed if key in manifest.environment
    }


def _model_configuration(config: Mapping[str, object]) -> dict[str, object]:
    allowed = (
        "provider",
        "base_url",
        "model",
        "thinking",
        "temperature",
        "max_tokens",
        "response_format",
        "max_attempts",
        "pricing_snapshot_date",
        "pricing_currency",
        "price_per_million_tokens",
        "pricing_policy",
    )
    return {key: config[key] for key in allowed if key in config}


def _run_summary(evidence: _RunEvidence) -> RunReportSummary:
    cases = evidence.cases
    attempts = evidence.attempts
    config = json.loads(evidence.raw["config.json"])
    if not isinstance(config, dict):
        raise ValueError("config snapshot must be an object")
    attempts_by_case = Counter(item.case_id for item in attempts)
    request_kinds = Counter(item.request_kind for item in attempts)
    logical_calls = sum(
        item.request_kind in {"original", "validation_repair"} for item in attempts
    )
    for case in cases:
        if attempts_by_case[case.case_id] != case.http_attempt_count:
            raise ValueError("case attempt count disagrees with attempt journal")
    human_status: Literal["measured", "partial", "not_measured"]
    manual_case_ids = {
        item.case_id for item in cases if item.actual_action == "manual_review"
    }
    reviewed_case_ids = {item.case_id for item in evidence.reviews}
    if evidence.reviews:
        human_status = "measured" if reviewed_case_ids == manual_case_ids else "partial"
        reviewed_case_count: int | None = len(evidence.reviews)
        modified: int | None = sum(
            item.changed_field_count for item in evidence.reviews
        )
        review_ms: int | None = sum(
            round(item.review_duration_seconds * 1000) for item in evidence.reviews
        )
    else:
        human_status = "not_measured"
        reviewed_case_count = modified = review_ms = None
    localizations = [item.failure_localization_ms for item in cases]
    source_inventory_hash = digest(canonical(evidence.manifest.source_inventory))
    return RunReportSummary(
        run_id=evidence.manifest.run_id,
        phase=evidence.manifest.phase,
        case_count=len(cases),
        first_attempt_usable_count=sum(item.first_attempt_usable for item in cases),
        action_counts={
            action: sum(item.actual_action == action for item in cases)
            for action in ACTION_ORDER
        },
        correct_accept_count=sum(item.correct_accept_count for item in cases),
        correct_reject_count=sum(item.correct_reject_count for item in cases),
        correct_manual_review_count=sum(
            item.correct_manual_review_count for item in cases
        ),
        unnecessary_review_count=sum(item.unnecessary_review_count for item in cases),
        missed_review_count=sum(item.missed_review_count for item in cases),
        attribution_counts={
            category: sum(item.attribution == category for item in cases)
            for category in ATTRIBUTION_ORDER
        },
        validation_error_counts=dict(
            sorted(
                Counter(
                    error for item in cases for error in item.validation_errors
                ).items()
            )
        ),
        logical_call_count=logical_calls,
        http_attempt_count=len(attempts),
        retry_count=sum(item.attempt_number > 1 for item in attempts),
        transport_retry_count=request_kinds["transport_retry"],
        validation_repair_count=request_kinds["validation_repair"],
        api_failure_count=sum(item.status == "failed" for item in attempts),
        api_error_category_counts=dict(
            sorted(
                Counter(
                    item.error_category
                    for item in attempts
                    if item.error_category is not None
                ).items()
            )
        ),
        execution_failure_count=sum(
            item.execution_status == "failed" for item in cases
        ),
        input_chunk_count=sum(item.input_chunk_count for item in cases),
        cited_chunk_count=sum(item.cited_chunk_count for item in cases),
        **{
            field: _sum_optional([getattr(item, field) for item in attempts])
            for field in TOKEN_FIELDS
        },
        conservative_estimated_cost_usd=estimate_cost(attempts, config),
        total_model_latency_ms=sum(item.latency_ms for item in attempts),
        total_failure_localization_ms=_sum_optional(localizations),
        interrupted_failure_localization_count=sum(
            value is None for value in localizations
        ),
        reviewed_case_count=reviewed_case_count,
        total_human_modified_field_count=modified,
        total_review_duration_ms=review_ms,
        review_measurement_status=human_status,
        requested_models=sorted({item.requested_model for item in attempts}),
        returned_models=sorted(
            {item.returned_model for item in attempts if item.returned_model}
        ),
        system_fingerprints=sorted(
            {item.system_fingerprint for item in attempts if item.system_fingerprint}
        ),
        requested_model_counts=dict(
            sorted(Counter(item.requested_model for item in attempts).items())
        ),
        returned_model_counts=dict(
            sorted(
                Counter(
                    item.returned_model
                    for item in attempts
                    if item.returned_model is not None
                ).items()
            )
        ),
        system_fingerprint_counts=dict(
            sorted(
                Counter(
                    item.system_fingerprint
                    for item in attempts
                    if item.system_fingerprint is not None
                ).items()
            )
        ),
        returned_model_missing_count=sum(
            item.returned_model is None for item in attempts
        ),
        system_fingerprint_missing_count=sum(
            item.system_fingerprint is None for item in attempts
        ),
        environment=_environment(evidence.manifest),
        model_configuration=_model_configuration(config),
        dataset_hash=evidence.manifest.dataset_hash,
        config_hash=evidence.manifest.config_hash,
        lock_hash=evidence.manifest.lock_hash,
        prompt_hash=evidence.manifest.prompt_hash,
        source_inventory_hash=source_inventory_hash,
    )


def _severity(case: ScoredCase, weights: Mapping[str, int]) -> int:
    impact = impact_for_case(case, weights)
    if impact:
        return impact
    if case.attribution is not None:
        return weights["format_retry_only"]
    return 0


def _typical(cases: Sequence[ScoredCase], weights: Mapping[str, int]) -> TypicalCases:
    def choose(candidates: Sequence[ScoredCase]) -> TypicalCase | None:
        if not candidates:
            return None
        result = min(
            candidates, key=lambda item: (-_severity(item, weights), item.case_id)
        )
        return TypicalCase(
            case_id=result.case_id,
            expected_action=result.expected_action,
            actual_action=result.actual_action,
            attribution=result.attribution,
            severity=_severity(result, weights),
            cited_chunk_count=result.cited_chunk_count,
            input_chunk_count=result.input_chunk_count,
        )

    success = [
        item
        for item in cases
        if item.execution_status == "completed"
        and item.actual_action == item.expected_action
        and item.attribution is None
    ]
    failure = [
        item
        for item in cases
        if item.execution_status == "failed"
        or item.actual_action != item.expected_action
        or item.attribution is not None
    ]
    manual = [item for item in cases if item.actual_action == "manual_review"]
    return TypicalCases(
        success=choose(success), failure=choose(failure), manual_review=choose(manual)
    )


def _causal_assessment(
    baseline: RunReportSummary, improved: RunReportSummary
) -> tuple[Literal["clean", "contaminated", "unverifiable"], list[str]]:
    contaminated = []
    unverifiable = []
    for phase, summary in (("baseline", baseline), ("improved", improved)):
        if len(summary.returned_model_counts) > 1:
            contaminated.append(f"{phase} has multiple returned models")
        if len(summary.system_fingerprint_counts) > 1:
            contaminated.append(f"{phase} has multiple system fingerprints")
        if summary.returned_model_missing_count:
            unverifiable.append(f"{phase} has missing returned model identifiers")
        if summary.system_fingerprint_missing_count:
            unverifiable.append(f"{phase} has missing system fingerprints")
    identity_pairs = [
        (
            "requested model identities",
            baseline.requested_models,
            improved.requested_models,
        )
    ]
    if not (
        baseline.returned_model_missing_count or improved.returned_model_missing_count
    ):
        identity_pairs.append(
            (
                "returned model identities",
                baseline.returned_models,
                improved.returned_models,
            )
        )
    if not (
        baseline.system_fingerprint_missing_count
        or improved.system_fingerprint_missing_count
    ):
        identity_pairs.append(
            (
                "system fingerprint identities",
                baseline.system_fingerprints,
                improved.system_fingerprints,
            )
        )
    for label, left, right in identity_pairs:
        if left != right:
            contaminated.append(f"{label} differs between runs")
    if contaminated:
        return "contaminated", [*contaminated, *unverifiable]
    if unverifiable:
        return "unverifiable", unverifiable
    return "clean", []


def _problem_count(
    summary: RunReportSummary, problem: Attribution | None
) -> int | None:
    return None if problem is None else summary.attribution_counts[problem]


def build_comparison(
    baseline_path: Path, improved_path: Path, analysis_path: Path
) -> ComparisonSummary:
    baseline_evidence = _load_run(baseline_path)
    improved_evidence = _load_run(improved_path)
    if (
        baseline_evidence.manifest.phase != "baseline"
        or improved_evidence.manifest.phase != "improved"
    ):
        raise ValueError("comparison requires baseline and improved phases")
    if (
        baseline_evidence.manifest.dataset_hash
        != improved_evidence.manifest.dataset_hash
        or baseline_evidence.manifest.config_hash
        != improved_evidence.manifest.config_hash
    ):
        raise ValueError("comparison inputs differ")
    analysis_raw = analysis_path.read_bytes()
    analysis_hash = digest(analysis_raw)
    analysis = ImprovementAnalysis.model_validate_json(analysis_raw)
    config = json.loads(baseline_evidence.raw["config.json"])
    verify_analysis(
        analysis,
        baseline_evidence.recomputed,
        impact_weights=config["impact_weights"],
        baseline_raw_inputs=baseline_evidence.raw,
    )
    verify_improvement(
        baseline_path,
        analysis_path,
        improved_evidence.manifest,
        baseline_raw_inputs=baseline_evidence.raw,
        strict_read_only=True,
        analysis=analysis,
    )
    baseline = _run_summary(baseline_evidence)
    improved = _run_summary(improved_evidence)
    if (baseline.review_measurement_status == "not_measured") != (
        improved.review_measurement_status == "not_measured"
    ):
        raise ValueError("review provenance must be symmetric")
    selected = analysis.selected_problem.problem if analysis.selected_problem else None
    before_selected = _problem_count(baseline, selected)
    after_selected = _problem_count(improved, selected)
    causal_status, reasons = _causal_assessment(baseline, improved)
    provenance = {
        "baseline": {
            name: value for name, value in baseline_evidence.raw_hashes.items()
        },
        "improved": {
            name: value for name, value in improved_evidence.raw_hashes.items()
        },
        "analysis": {"analysis.json": analysis_hash},
    }
    limitations = [
        "逐条手工策划的合成小样本挑战集"
        f"（本次 {baseline.case_count} 条），不代表生产分布。",
        "仅包含一组基线/复测，不能证明改进可泛化。",
        "模型版本仅按 API 返回的模型标识与 system fingerprint 记录。",
        "本地非生产环境的延迟、重试与成本不能外推至生产系统。",
        "评测隔离数据库与检索，不衡量端到端召回质量。",
    ]
    review_statuses = {
        baseline.review_measurement_status,
        improved.review_measurement_status,
    }
    if review_statuses == {"not_measured"}:
        limitations.append("未完成真实人工复核，修改字段数与复核耗时为 not measured。")
    elif "partial" in review_statuses:
        limitations.append("人工复核记录仅覆盖部分需复核案例，人工指标为 partial。")
    _assert_run_snapshot_unchanged(baseline_path, baseline_evidence.raw)
    _assert_run_snapshot_unchanged(improved_path, improved_evidence.raw)
    if analysis_path.read_bytes() != analysis_raw:
        raise ValueError("analysis changed after snapshot")
    return ComparisonSummary(
        baseline=baseline,
        improved=improved,
        delta=ProcessDelta(
            first_attempt_usable_count=(
                improved.first_attempt_usable_count
                - baseline.first_attempt_usable_count
            ),
            validation_repair_count=(
                improved.validation_repair_count - baseline.validation_repair_count
            ),
            citation_error_case_count=(
                improved.attribution_counts["citation_error"]
                - baseline.attribution_counts["citation_error"]
            ),
            support_error_case_count=(
                improved.attribution_counts["support_error"]
                - baseline.attribution_counts["support_error"]
            ),
            human_judgment_case_count=(
                improved.attribution_counts["human_judgment"]
                - baseline.attribution_counts["human_judgment"]
            ),
            manual_review_action_count=(
                improved.action_counts["manual_review"]
                - baseline.action_counts["manual_review"]
            ),
            execution_failure_count=(
                improved.execution_failure_count - baseline.execution_failure_count
            ),
            selected_problem_case_count=(
                None
                if before_selected is None or after_selected is None
                else after_selected - before_selected
            ),
        ),
        selected_problem=analysis.selected_problem,
        changed_dimension=(
            analysis.selected_problem.changed_dimension
            if analysis.selected_problem
            else None
        ),
        causal_status=causal_status,
        causal_contamination=causal_status != "clean",
        causal_contamination_reasons=reasons,
        typical_cases=_typical(baseline_evidence.cases, config["impact_weights"]),
        provenance=provenance,
        limitations=limitations,
    )


def _display(value: object) -> str:
    return "not measured" if value is None else str(value)


def render_comparison(summary: ComparisonSummary) -> str:
    left, right = summary.baseline, summary.improved
    requested_models = (
        f"{', '.join(left.requested_models) or 'not returned'} → "
        f"{', '.join(right.requested_models) or 'not returned'}"
    )
    returned_models = (
        f"{', '.join(left.returned_models) or 'not returned'} → "
        f"{', '.join(right.returned_models) or 'not returned'}"
    )
    fingerprints = (
        f"{', '.join(left.system_fingerprints) or 'not returned'} → "
        f"{', '.join(right.system_fingerprints) or 'not returned'}"
    )
    returned_model_counts = (
        f"{json.dumps(left.returned_model_counts, sort_keys=True)} → "
        f"{json.dumps(right.returned_model_counts, sort_keys=True)}"
    )
    fingerprint_counts = (
        f"{json.dumps(left.system_fingerprint_counts, sort_keys=True)} → "
        f"{json.dumps(right.system_fingerprint_counts, sort_keys=True)}"
    )
    if summary.selected_problem is None:
        experiment = ["- 基线没有形成可授权的行为问题，未声明改进变量。"]
    else:
        problem = summary.selected_problem.problem
        experiment = [
            f"- 选中问题：`{problem}`",
            f"- 唯一变更维度：`{summary.changed_dimension}`",
            f"- 受影响案例：{summary.selected_problem.affected_case_count}",
            f"- 冻结加权影响：{summary.selected_problem.weighted_impact}",
            "- 相关归因案例："
            f"{left.attribution_counts[problem]} → "
            f"{right.attribution_counts[problem]} "
            f"（delta {summary.delta.selected_problem_case_count}）",
        ]
    rows = [
        (
            "首次可用案例",
            left.first_attempt_usable_count,
            right.first_attempt_usable_count,
        ),
        ("正确接受", left.correct_accept_count, right.correct_accept_count),
        ("正确拒绝", left.correct_reject_count, right.correct_reject_count),
        (
            "正确转人工复核",
            left.correct_manual_review_count,
            right.correct_manual_review_count,
        ),
        (
            "路由到人工复核",
            left.action_counts["manual_review"],
            right.action_counts["manual_review"],
        ),
        (
            "不必要人工复核",
            left.unnecessary_review_count,
            right.unnecessary_review_count,
        ),
        ("漏复核", left.missed_review_count, right.missed_review_count),
        ("模型逻辑调用", left.logical_call_count, right.logical_call_count),
        ("HTTP 尝试", left.http_attempt_count, right.http_attempt_count),
        ("重试", left.retry_count, right.retry_count),
        ("API 失败", left.api_failure_count, right.api_failure_count),
        ("执行失败案例", left.execution_failure_count, right.execution_failure_count),
        ("输入 Chunk", left.input_chunk_count, right.input_chunk_count),
        ("实际引用 Chunk", left.cited_chunk_count, right.cited_chunk_count),
        ("输入 Token", left.input_tokens, right.input_tokens),
        ("输出 Token", left.output_tokens, right.output_tokens),
        ("总 Token", left.total_tokens, right.total_tokens),
        ("缓存命中 Token", left.cache_hit_tokens, right.cache_hit_tokens),
        ("缓存未命中 Token", left.cache_miss_tokens, right.cache_miss_tokens),
        (
            "成本（USD）",
            left.conservative_estimated_cost_usd,
            right.conservative_estimated_cost_usd,
        ),
        ("模型耗时（ms）", left.total_model_latency_ms, right.total_model_latency_ms),
        (
            "失败定位耗时（ms）",
            left.total_failure_localization_ms,
            right.total_failure_localization_ms,
        ),
        ("已复核案例", left.reviewed_case_count, right.reviewed_case_count),
        (
            "人工修改字段实例",
            left.total_human_modified_field_count,
            right.total_human_modified_field_count,
        ),
        (
            "人工复核耗时（ms）",
            left.total_review_duration_ms,
            right.total_review_duration_ms,
        ),
        (
            "人工测量状态",
            left.review_measurement_status,
            right.review_measurement_status,
        ),
    ]
    lines = [
        "# TraceRisk 真实模型挑战评测前后对比",
        "",
        "本报告按原始 journal 聚合计数与过程指标，不以单一通过率作为成果。",
        "",
        "## 单变量实验",
        "",
        *experiment,
        "",
        "| 指标 | 基线 | 复测 |",
        "| --- | ---: | ---: |",
        *(
            f"| {name} | {_display(before)} | {_display(after)} |"
            for name, before, after in rows
        ),
        "",
        "## 错误归因",
        "",
        "| 归因 | 基线 | 复测 |",
        "| --- | ---: | ---: |",
        *(
            f"| {name} | {left.attribution_counts[name]} | "
            f"{right.attribution_counts[name]} |"
            for name in ATTRIBUTION_ORDER
        ),
        "",
        "校验错误类型（基线 / 复测）：",
        "",
        *(
            f"- `{name}`：{left.validation_error_counts.get(name, 0)} / "
            f"{right.validation_error_counts.get(name, 0)}"
            for name in sorted(
                left.validation_error_counts.keys()
                | right.validation_error_counts.keys()
            )
        ),
        *(
            ["- 本次没有校验错误记录。"]
            if not (left.validation_error_counts or right.validation_error_counts)
            else []
        ),
        "",
        "## 模型与运行边界",
        "",
        f"- 请求模型：{requested_models}",
        f"- 返回模型：{returned_models}",
        f"- system fingerprint：{fingerprints}",
        f"- 返回模型计数分布：`{returned_model_counts}`",
        f"- fingerprint 计数分布：`{fingerprint_counts}`",
        "- 缺失返回模型："
        f"{left.returned_model_missing_count} → "
        f"{right.returned_model_missing_count}",
        "- 缺失 fingerprint："
        f"{left.system_fingerprint_missing_count} → "
        f"{right.system_fingerprint_missing_count}",
        f"- 因果比较状态：`{summary.causal_status}`",
        *(f"  - {reason}" for reason in summary.causal_contamination_reasons),
        "",
        "## 局限",
        "",
        *(f"- {item}" for item in summary.limitations),
        "",
        "## 冻结环境",
        "",
        f"- Python：{left.environment.get('python', 'not recorded')}",
        f"- OS：{left.environment.get('os', 'not recorded')}",
        f"- 数据集 SHA-256：`{left.dataset_hash}`",
        f"- 配置 SHA-256：`{left.config_hash}`",
        f"- 依赖锁 SHA-256：`{left.lock_hash}`",
        f"- 源码清单 SHA-256：`{left.source_inventory_hash}`",
        "",
        "## Provenance",
        "",
        *(
            f"- `{phase}/{name}`: `{value}`"
            for phase, inputs in summary.provenance.items()
            for name, value in inputs.items()
        ),
        "",
    ]
    return "\n".join(lines)


def render_typical_cases(summary: ComparisonSummary) -> str:
    lines = [
        "# 典型案例",
        "",
        "以下案例从基线结果中按影响严重度降序、case_id 升序确定性选择。",
        "",
    ]
    for title, case in (
        ("成功案例", summary.typical_cases.success),
        ("失败案例", summary.typical_cases.failure),
        ("人工复核案例", summary.typical_cases.manual_review),
    ):
        lines.extend([f"## {title}", ""])
        if case is None:
            lines.extend(["本次运行中不存在该类案例。", ""])
            continue
        lines.extend(
            [
                f"- case_id：`{case.case_id}`",
                f"- 期望/实际：`{case.expected_action}` / `{case.actual_action}`",
                f"- 一级归因：`{case.attribution or 'none'}`",
                f"- 输入/引用 Chunk：{case.input_chunk_count} / "
                f"{case.cited_chunk_count}",
                "",
            ]
        )
    return "\n".join(lines)


def render_resume_bullet(summary: ComparisonSummary) -> str:
    problem = (
        summary.selected_problem.problem
        if summary.selected_problem
        else "未形成可选归因"
    )
    dimension = summary.changed_dimension or "未实施行为变量变更"
    before = (
        summary.baseline.attribution_counts[summary.selected_problem.problem]
        if summary.selected_problem
        else None
    )
    after = (
        summary.improved.attribution_counts[summary.selected_problem.problem]
        if summary.selected_problem
        else None
    )
    metric_decreased = before is not None and after is not None and after < before
    if metric_decreased and summary.causal_status == "clean":
        conclusion = "完成定向改进"
    elif summary.causal_status == "contaminated":
        conclusion = "模型标识变化使因果归因受污染"
    elif summary.causal_status == "unverifiable":
        conclusion = "模型标识缺失使因果关系不可验证"
    else:
        conclusion = "实验结论未显示该指标改善"
    returned = ", ".join(summary.baseline.returned_models) or "API 未返回模型标识"
    fingerprint = (
        ", ".join(summary.baseline.system_fingerprints) or "未返回 fingerprint"
    )
    return (
        "- 发现问题—完成改进—过程指标变化：在 TraceRisk 的 "
        f"{summary.baseline.case_count} 条逐条手工合成挑战样本上，使用真实 DeepSeek "
        f"调用（返回模型 {returned}，system fingerprint {fingerprint}）"
        f"定位 `{problem}`，"
        f"仅调整 `{dimension}`；{conclusion}，相关归因案例由 {_display(before)} 变为 "
        f"{_display(after)}。结果来自一组本地非生产环境前后实验，不代表生产分布或普遍提升。\n"
    )


def generate_reports(
    baseline_path: Path,
    improved_path: Path,
    analysis_path: Path,
    output: Path,
) -> ComparisonSummary:
    summary = build_comparison(baseline_path, improved_path, analysis_path)
    for phase, path in (("baseline", baseline_path), ("improved", improved_path)):
        current = {
            name: digest(raw) for name, raw in sorted(snapshot_run_inputs(path).items())
        }
        if current != summary.provenance[phase]:
            raise ValueError(f"{phase} inputs changed after snapshot")
    if (
        digest(analysis_path.read_bytes())
        != summary.provenance["analysis"]["analysis.json"]
    ):
        raise ValueError("analysis changed after snapshot")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "comparison-summary.json", summary.model_dump(mode="json"))
    write_json(baseline_path / "summary.json", summary.baseline.model_dump(mode="json"))
    write_json(improved_path / "summary.json", summary.improved.model_dump(mode="json"))
    write_bytes(output / "comparison.md", render_comparison(summary).encode("utf-8"))
    write_bytes(
        output / "typical-cases.md", render_typical_cases(summary).encode("utf-8")
    )
    write_bytes(
        output / "resume-bullet.md", render_resume_bullet(summary).encode("utf-8")
    )
    return summary
